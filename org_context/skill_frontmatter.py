"""Bounded discovery and frontmatter parsing for Claude Code skills and agents.

Only the YAML frontmatter block of SKILL.md / agents/*.md files is read, and only the keys
``name``, ``description`` and ``when_to_use`` are kept. Bodies are never decoded, stored or
hashed. Repository content is untrusted data.
"""
import hashlib
import os
import re
import stat
import unicodedata
from .capabilities import _CAP_ID, _validate_relpath

SKILL_PATTERNS = (('skills/*/SKILL.md', 'skill'),
                  ('plugins/*/skills/*/SKILL.md', 'skill'),
                  ('agents/*.md', 'agent'))
FRONTMATTER_MAX_BYTES = 16384
FRONTMATTER_MAX_LINES = 200
MAX_SKILL_CANDIDATES = 256
MAX_SKILL_FILES = 64
MAX_SKIPPED_LISTED = 64
DESCRIPTION_MAX = 1024
WHEN_TO_USE_MAX = 1024
EVIDENCE_KIND = 'skill_frontmatter'
EVIDENCE_METHOD = 'bounded_frontmatter_parser'
DECLARATION_SOURCE = 'declared_skill_frontmatter'
FIELDS = ('name', 'description', 'when_to_use')
SKIP_REASONS = ('invalid_path', 'symlink', 'not_a_file', 'read_error', 'no_frontmatter',
                'malformed_frontmatter', 'frontmatter_too_large', 'missing_name', 'invalid_name',
                'missing_description', 'invalid_field', 'duplicate_capability_id', 'file_limit')

_BOM = b'\xef\xbb\xbf'
_KEY_LINE = re.compile(r'^([A-Za-z_][A-Za-z0-9_-]*):(?: (.*))?$')
_BLOCK = re.compile(r'^([|>])([-+]?)(?:[ ]+#.*)?$')
_WS = re.compile(r'[ \t\r\n\f\v]+')
# Bidi controls plus zero-width/invisible characters that would hide text in tool output.
_BIDI = frozenset([0x200B, 0x200C, 0x200D, 0x200E, 0x200F, *range(0x202A, 0x202F),
                   0x2060, *range(0x2066, 0x206A), 0xFEFF])
# Control characters, lone surrogates, and line/paragraph separators.
_REJECTED_CATEGORIES = frozenset(['Cc', 'Cf', 'Cs', 'Zl', 'Zp'])
_ESCAPES = {'\\': '\\', '"': '"', '/': '/', 'n': '\n', 't': '\t', 'r': '\r'}
_PLAIN_FORBIDDEN_START = tuple('[{&*!%@`')


class FrontmatterError(ValueError):
    """Content-free parse failure; ``reason`` is one of SKIP_REASONS."""

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


class _PathError(ValueError):
    pass


def _malformed():
    return FrontmatterError('malformed_frontmatter')


def _find_block(data):
    """Return (block_bytes, end_offset) of the frontmatter, end_offset counted in ``data``."""
    start = len(_BOM) if data.startswith(_BOM) else 0
    capped = data[:FRONTMATTER_MAX_BYTES]
    hit_cap = len(data) > FRONTMATTER_MAX_BYTES
    pos = start
    line_no = 0
    block_start = None
    while True:
        nl = capped.find(b'\n', pos)
        if nl != -1:
            line, end = capped[pos:nl], nl + 1
        elif pos < len(capped) and not hit_cap:
            line, end = capped[pos:], len(capped)  # unterminated last line of the file
        else:
            # Input exhausted (or only a partial line cut at the byte cap remains).
            if block_start is None:
                raise FrontmatterError('no_frontmatter')
            raise FrontmatterError('frontmatter_too_large' if hit_cap else 'malformed_frontmatter')
        stripped = line.rstrip(b' \r')
        if block_start is None:
            if stripped != b'---':
                raise FrontmatterError('no_frontmatter')
            block_start = end
        else:
            if stripped in (b'---', b'...'):
                return capped[block_start:pos], end
            line_no += 1
            if line_no > FRONTMATTER_MAX_LINES:
                raise FrontmatterError('frontmatter_too_large')
        pos = end


def _indent(line):
    return line[:len(line) - len(line.lstrip(' \t'))]


def _split_entries(lines):
    """Group lines into {key: (first_value, [continuation lines])} for the three fields."""
    entries = {}
    current = None  # (key, keep)
    for line in lines:
        if not line.strip():
            continue
        lead = _indent(line)
        if '\t' in lead:
            raise _malformed()
        if lead:
            if current is None:
                raise _malformed()
            if current[1]:
                entries[current[0]][1].append(line)
            continue
        if line.startswith('#'):
            continue
        m = _KEY_LINE.match(line)
        if not m:
            raise _malformed()
        key = m.group(1)
        keep = key in FIELDS
        if keep:
            if key in entries:
                raise _malformed()
            entries[key] = (m.group(2) or '', [])
        current = (key, keep)
    return entries


def _after_quote_ok(rest):
    rest = rest.strip(' ')
    return rest == '' or rest.startswith('#')


def _double_quoted(text):
    out = []
    i = 1
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == '"':
            if not _after_quote_ok(text[i + 1:]):
                raise _malformed()
            return ''.join(out)
        if ch == '\\':
            if i + 1 >= n:
                raise _malformed()
            esc = text[i + 1]
            if esc in _ESCAPES:
                out.append(_ESCAPES[esc]); i += 2; continue
            if esc == 'u':
                hexpart = text[i + 2:i + 6]
                if len(hexpart) != 4 or not all(c in '0123456789abcdefABCDEF' for c in hexpart):
                    raise _malformed()
                code = int(hexpart, 16)
                if 0xD800 <= code <= 0xDFFF:
                    raise _malformed()
                out.append(chr(code)); i += 6; continue
            raise _malformed()
        out.append(ch); i += 1
    raise _malformed()


def _single_quoted(text):
    out = []
    i = 1
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "'":
            if i + 1 < n and text[i + 1] == "'":
                out.append("'"); i += 2; continue
            if not _after_quote_ok(text[i + 1:]):
                raise _malformed()
            return ''.join(out)
        out.append(ch); i += 1
    raise _malformed()


def _strip_comment(text):
    idx = text.find(' #')
    return text[:idx] if idx != -1 else text


def _value(first, conts):
    first = first.strip(' ')
    conts = [c.strip(' ').rstrip('\r') for c in conts]
    if _BLOCK.match(first):
        return '\n'.join(conts)
    if first.startswith(('|', '>')):
        raise _malformed()
    if not first:
        if not conts:
            return ''
        first, conts = conts[0], conts[1:]
        if first.startswith(('|', '>')):
            raise _malformed()
    joined = ' '.join([first, *conts])
    if first.startswith('"'):
        return _double_quoted(joined)
    if first.startswith("'"):
        return _single_quoted(joined)
    if first.startswith(_PLAIN_FORBIDDEN_START):
        raise _malformed()
    return ' '.join(_strip_comment(part) for part in [first, *conts])


def _normalize(value):
    value = _WS.sub(' ', value).strip()
    for ch in value:
        if unicodedata.category(ch) in _REJECTED_CATEGORIES or ord(ch) in _BIDI:
            raise FrontmatterError('invalid_field')
    return value


def parse_frontmatter(data):
    """Parse the bounded frontmatter prefix of a skill/agent file. Never returns body text."""
    if not isinstance(data, (bytes, bytearray)):
        raise TypeError('data must be bytes')
    data = bytes(data[:FRONTMATTER_MAX_BYTES + 1])
    block, end = _find_block(data)
    try:
        text = block.decode('utf-8', errors='strict')
    except UnicodeDecodeError:
        raise _malformed() from None
    lines = [line.rstrip('\r') for line in text.split('\n')]
    entries = _split_entries(lines)
    values = {key: _value(*entries[key]) for key in FIELDS if key in entries}

    name = _WS.sub(' ', values.get('name', '')).strip()
    if not name:
        raise FrontmatterError('missing_name')
    if not _CAP_ID.fullmatch(name):
        raise FrontmatterError('invalid_name')
    description = _normalize(values.get('description', ''))
    if not description:
        raise FrontmatterError('missing_description')
    if len(description) > DESCRIPTION_MAX:
        raise FrontmatterError('invalid_field')
    when_to_use = _normalize(values.get('when_to_use', ''))
    if len(when_to_use) > WHEN_TO_USE_MAX:
        raise FrontmatterError('invalid_field')
    return {
        'name': name,
        'description': description,
        'when_to_use': when_to_use,
        'frontmatter_sha256': hashlib.sha256(data[:end]).hexdigest(),
    }


def _read_prefix(path):
    """Read at most FRONTMATTER_MAX_BYTES + 1 bytes without following a final symlink."""
    flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_BINARY', 0)
    fd = os.open(path, flags)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise FrontmatterError('not_a_file')
        chunks = []
        remaining = FRONTMATTER_MAX_BYTES + 1
        while remaining > 0:
            chunk = os.read(fd, remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        return b''.join(chunks)
    finally:
        os.close(fd)


def _component(root, rel):
    parts = rel.split('/')
    if parts[0] == 'plugins':
        plugin = parts[1]
        if not plugin.isprintable() or not 1 <= len(plugin) <= 128:
            raise FrontmatterError('invalid_path')
        return plugin
    return root.name


def collect_skill_declarations(root, taken_ids, is_safe):
    """Discover skill/agent files under ``root`` and parse their frontmatter within fixed bounds.

    Returns (declarations, coverage_block). ``is_safe`` is ``cli.safe``.
    """
    candidates = []
    truncated = False
    for pattern, role in SKILL_PATTERNS:
        for path in root.glob(pattern):
            if len(candidates) >= MAX_SKILL_CANDIDATES:
                truncated = True
                break
            candidates.append((path.relative_to(root).as_posix(), path, role))
        if truncated:
            break
    candidates.sort(key=lambda item: item[0])

    taken = set(taken_ids)
    declarations = []
    skipped = []
    for index, (rel, path, role) in enumerate(candidates):
        try:
            if index >= MAX_SKILL_FILES:
                raise FrontmatterError('file_limit')
            try:
                rel.encode('utf-8')
                _validate_relpath(rel, 'skill path', _PathError)
                if any(unicodedata.category(c) in _REJECTED_CATEGORIES for c in rel):
                    raise _PathError('invalid_path')
            except (_PathError, UnicodeEncodeError):
                raise FrontmatterError('invalid_path') from None
            component = _component(root, rel)
            try:
                if not is_safe(root, path):
                    raise FrontmatterError('symlink')
                if not path.is_file():
                    raise FrontmatterError('not_a_file')
                data = _read_prefix(path)
            except OSError:
                raise FrontmatterError('read_error') from None
            parsed = parse_frontmatter(data)
            if parsed['name'] in taken:
                raise FrontmatterError('duplicate_capability_id')
        except FrontmatterError as exc:
            # A path that is not valid UTF-8 is reported with its bad bytes escaped.
            safe_rel = rel.encode('utf-8', 'backslashreplace').decode('utf-8')
            skipped.append({'path': safe_rel, 'reason': exc.reason})
            continue
        taken.add(parsed['name'])
        declarations.append({
            'path': rel,
            'role': role,
            'component': component,
            'capability_id': parsed['name'],
            'summary': parsed['description'],
            'responsibility': parsed['when_to_use'],
            'frontmatter_sha256': parsed['frontmatter_sha256'],
        })

    by_reason = {}
    for item in skipped:
        by_reason[item['reason']] = by_reason.get(item['reason'], 0) + 1
    coverage = {
        'patterns_checked': [pattern for pattern, _ in SKILL_PATTERNS],
        'fields_read': list(FIELDS),
        'semantics': DECLARATION_SOURCE + '; frontmatter only, bodies not read',
        'limits': {
            'frontmatter_max_bytes': FRONTMATTER_MAX_BYTES,
            'frontmatter_max_lines': FRONTMATTER_MAX_LINES,
            'max_files': MAX_SKILL_FILES,
            'max_candidates': MAX_SKILL_CANDIDATES,
        },
        'candidates_found': len(candidates),
        'indexed': len(declarations),
        'skipped': skipped[:MAX_SKIPPED_LISTED],
        'skipped_count': len(skipped),
        'skipped_by_reason': dict(sorted(by_reason.items())),
        'truncated': truncated,
    }
    return declarations, coverage
