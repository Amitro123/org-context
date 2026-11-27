"""Bounded lexical extraction of package installs named in CI step shell text.

Nothing is executed or evaluated: the text of GitHub Actions `run:` values and Azure
Pipelines `script:`/`bash:`/`pwsh:` values is read line by line, and only static package
names from a fixed set of install commands are returned. Everything else is ignored or
counted by reason; no text from the file is ever recorded in the counts.
"""
import re

from .workflow_refs import _BLOCK_SCALAR, _scalar

MAX_CI_INSTALLS = 50
MAX_LINE = 4096
MAX_CONTINUATION = 20
MAX_NAME = 100
MAX_SPEC = 80

# Shared with cli (requirements / pyproject / package.json); defined once here.
SPEC = re.compile(r'([A-Za-z0-9][A-Za-z0-9_.-]*)(?:\[[A-Za-z0-9_,.-]+\])?\s*([<>=!~][A-Za-z0-9.*<>=!~,+ -]+)?')
NPM_NAME = re.compile(r'@?[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)?')
_NPM_SPEC = re.compile(r'[A-Za-z0-9.*^~<>=|+-]{0,80}')


def normalize(name):
    return re.sub(r'[-_.]+', '-', name).lower()


_KEYS = {'github': ('run',), 'azure': ('script', 'bash', 'pwsh')}
_COMMAND_WORD = re.compile(r'\b(?:pip3?|pipx|uv|npm|npx)\b')
_ENV_ASSIGN = re.compile(r'[A-Za-z_][A-Za-z0-9_]*=')
_SHELL_COMMENT = re.compile(r'\s#.*$')
_EXPRESSION = re.compile(r'\$\{\{.*?\}\}')

_PIP_REQ = {'-r', '--requirement', '-c', '--constraint'}
_PIP_EDIT = {'-e', '--editable'}
_PIP_VALUE = {
    '-i', '--index-url', '--extra-index-url', '-f', '--find-links', '-t', '--target',
    '--prefix', '--root', '--src', '--trusted-host', '--platform', '--python-version',
    '--implementation', '--abi', '--cache-dir', '--log', '--proxy', '--retries', '--timeout',
    '--cert', '--client-cert', '--exists-action', '--no-binary', '--only-binary',
    '--upgrade-strategy', '--progress-bar', '-C', '--config-settings', '--python', '-p',
    '--username', '--password', '--keyring-provider', '--root-user-action', '--use-feature',
    '--report', '--global-option', '--install-option', '--index-strategy', '--resolution',
    '--prerelease', '--exclude-newer', '--extra', '--group', '--reinstall-package',
    '--upgrade-package', '-P', '--default-index', '--index',
}
_PIPX_VALUE = {'--python', '--pip-args', '--suffix', '--index-url', '--spec'}
_UV_TOOL_VALUE = {
    '--python', '-p', '--with', '--with-requirements', '--index-url', '--extra-index-url', '--from',
    '--index-strategy', '--resolution', '--prerelease', '--keyring-provider', '--default-index',
    '--index', '--exclude-newer',
}
_NPM_VALUE = {
    '--registry', '--prefix', '--tag', '-w', '--workspace', '--cache', '--userconfig',
    '--globalconfig', '--_authToken', '--_auth', '--otp', '--auth-type', '--loglevel',
    '--omit', '--include', '--location', '--install-strategy', '--before', '--scope',
    '--audit-level', '--cafile', '--ca', '--proxy', '--https-proxy', '--fetch-retries',
}

# (prefix tokens, ecosystem, command kind)
_COMMANDS = (
    (('python', '-m', 'pip', 'install'), 'python', 'pip'),
    (('python3', '-m', 'pip', 'install'), 'python', 'pip'),
    (('uv', 'pip', 'install'), 'python', 'pip'),
    (('uv', 'tool', 'install'), 'python', 'uvtool'),
    (('pip', 'install'), 'python', 'pip'),
    (('pip3', 'install'), 'python', 'pip'),
    (('pipx', 'install'), 'python', 'pipx'),
    (('npm', 'install'), 'npm', 'npm'),
    (('npm', 'i'), 'npm', 'npm'),
    (('npx',), 'npm', 'npx'),
)
_VALUE_OPTIONS = {'pip': _PIP_VALUE, 'pipx': _PIPX_VALUE, 'uvtool': _UV_TOOL_VALUE, 'npm': _NPM_VALUE, 'npx': _NPM_VALUE}


def _indent(line):
    return len(line) - len(line.lstrip())


_KEY = re.compile(r"""(?:([A-Za-z_][^:#'"\[\]{},]*?)|"([^"]*)"|'([^']*)')[ \t]*:(?:[ \t]+(.*)|[ \t]*)$""")
# Ancestors that may lead to a step list in a pipeline (the same shape workflow_refs trusts).
_AZURE_ANCESTORS = frozenset([
    'stages', 'jobs', 'strategy', 'runOnce', 'rolling', 'canary', 'preDeploy', 'deploy',
    'routeTraffic', 'postRouteTraffic', 'on', 'success', 'failure',
])


def _parse_line(line):
    """Return (dash columns, key, key column, value or None, text after the dashes) for one line."""
    col = len(line) - len(line.lstrip(' '))
    rest = line[col:]
    dashes = []
    while rest[:1] == '-' and (len(rest) == 1 or rest[1] in ' \t'):
        dashes.append(col)
        inner = rest[1:]
        stripped = inner.lstrip(' \t')
        col += 1 + len(inner) - len(stripped)
        rest = stripped
    m = _KEY.match(rest)
    if not m:
        return dashes, None, col, None, rest
    key = next(g for g in m.groups()[:3] if g is not None)
    return dashes, key, col, m[4], rest


def _is_step_field(tokens, flavor):
    """True when the key at the end of `tokens` is a command field of a step mapping.

    Only `jobs.<id>.steps[].run` (GitHub) and `steps[].script|bash|pwsh` below the known pipeline
    ancestors (Azure) count. env, with, parameters and every unknown structure are data.
    """
    if len(tokens) < 3 or tokens[-2] != 'I' or tokens[-3] != 'K:steps':
        return False
    field = tokens[-1][2:]
    if flavor == 'github':
        return field == 'run' and len(tokens) == 5 and tokens[0] == 'K:jobs'
    prefix = tokens[:-3]
    return field in _KEYS['azure'] and all(
        t == 'I' or (t.startswith('K:') and t[2:] in _AZURE_ANCESTORS) for t in prefix)


def _is_steps_key(tokens, flavor):
    """True when the key at the end of `tokens` is a `steps` list in a supported position."""
    if tokens[-1] != 'K:steps':
        return False
    if flavor == 'github':
        return len(tokens) == 3 and tokens[0] == 'K:jobs'
    return all(t == 'I' or (t.startswith('K:') and t[2:] in _AZURE_ANCESTORS) for t in tokens[:-1])


def _fold(lines, base=None):
    """Apply YAML folding to the lines of a `>` block scalar (blank and more-indented lines keep breaks).

    `base` is the content indentation from an explicit indicator (`>2`); without one it is the
    indentation of the first non-blank line.
    """
    if base is None:
        base = next((_indent(x) for x in lines if x.strip()), 0)
    out, current = [], None
    for raw in lines:
        if not raw.strip():
            if current is not None:
                out.append(current)
                current = None
        elif _indent(raw) > base:
            if current is not None:
                out.append(current)
                current = None
            out.append(raw.strip())
        else:
            current = raw.strip() if current is None else current + ' ' + raw.strip()
    if current is not None:
        out.append(current)
    return out


_MAX_OPAQUE_LINES = 500


def _opaque_end(first, lines, i):
    """Consume a quoted scalar or flow collection that starts with `first`.

    Returns the index of the first line after it, whether it spanned further lines and whether it
    was terminated. Its text is opaque data: nothing inside is read as YAML structure. An
    unterminated one swallows at most _MAX_OPAQUE_LINES lines (invalid YAML), which can only hide
    declarations, never invent them; the caller reports it.
    """
    quote, depth = None, 0
    text, start = first, i
    while True:
        j = 0
        while j < len(text):
            ch = text[j]
            if quote == '"':
                if ch == '\\':
                    j += 1
                elif ch == '"':
                    quote = None
            elif quote == "'":
                if ch == "'":
                    if text[j + 1:j + 2] == "'":
                        j += 1
                    else:
                        quote = None
            elif ch in '"\'':
                quote = ch
            elif ch in '[{':
                depth += 1
            elif ch in ']}':
                depth -= 1
            elif ch == '#' and (j == 0 or text[j - 1] in ' \t'):
                break
            j += 1
        if quote is None and depth <= 0:
            return i, i != start, True
        if i >= len(lines) or i - start >= _MAX_OPAQUE_LINES:
            return i, i != start, False
        text = lines[i]
        i += 1


def _consume_block(lines, i, parent_col):
    """Return the raw lines of a block scalar body and the index after it."""
    body = []
    while i < len(lines):
        nxt = lines[i]
        if nxt.strip() and _indent(nxt) <= parent_col:
            break
        body.append(nxt)
        i += 1
    return body, i


def _block_lines(header, body, parent_col):
    digit = re.search(r'[1-9]', header)
    if header[0] == '>':
        return _fold(body, parent_col + int(digit.group())) if digit else _fold(body)
    return [x.strip() for x in body]


def _script_bodies(text, flavor, notes):
    """Yield lists of shell lines for each step command field, tracking the YAML context."""
    lines = text.splitlines()
    stack = []  # (column, token): 'I' for a sequence item, 'K:<key>' for a mapping key
    i = 0
    while i < len(lines):
        line = lines[i]
        i += 1
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        dashes, key, kcol, value, rest = _parse_line(line)
        for d in dashes:
            while stack and (stack[-1][0] > d or (stack[-1][0] == d and stack[-1][1] == 'I')):
                stack.pop()
            stack.append((d, 'I'))
        if key is None:
            # A scalar sequence item: consume it whole so its text is never read as structure.
            if dashes:
                item = _scalar(rest)
                if _BLOCK_SCALAR.fullmatch(item):
                    _, i = _consume_block(lines, i, dashes[-1])
                elif item[:1] in ('"', "'", '[', '{'):
                    start = i - 1
                    i, _, closed = _opaque_end(rest.strip(), lines, i)
                    if not closed and _COMMAND_WORD.search(' '.join(lines[start:i])):
                        notes['ci_install_unterminated_scalar'] += 1
            continue
        while stack and stack[-1][0] >= kcol:
            stack.pop()
        tokens = [t for _, t in stack] + ['K:' + key]
        stack.append((kcol, 'K:' + key))
        target = _is_step_field(tokens, flavor)
        scalar = _scalar(value or '')
        if _BLOCK_SCALAR.fullmatch(scalar):
            body, i = _consume_block(lines, i, kcol)
            if target:
                yield _block_lines(scalar, body, kcol)
            continue
        if not scalar:
            continue
        if scalar[0] in '"\'[{':
            end, multiline, closed = _opaque_end(scalar, lines, i)
            if not closed and _COMMAND_WORD.search(' '.join(lines[i - 1:end])):
                # unterminated (invalid YAML): the rest was skipped, and it looked like it held installs
                notes['ci_install_unterminated_scalar'] += 1
            if _is_steps_key(tokens, flavor) and _COMMAND_WORD.search(' '.join(lines[i - 1:end])):
                # steps written in flow style are not read; say so instead of reporting nothing
                notes['ci_install_unsupported'] += 1
            if multiline:
                # The whole scalar is data (or an unsupported command scalar): never re-parsed.
                if target and _COMMAND_WORD.search(' '.join(lines[i - 1:end])):
                    notes['ci_install_unsupported'] += 1
                i = end
                continue
            if target and scalar[0] in '"\'' and len(scalar) >= 2 and scalar[-1] == scalar[0]:
                yield [scalar[1:-1]]
            continue
        if not target:
            continue
        parts = [scalar]
        while i < len(lines) and lines[i].strip() and _indent(lines[i]) > kcol and len(parts) < MAX_CONTINUATION:
            parts.append(lines[i].strip())
            i += 1
        yield [' '.join(parts)]


def _logical_lines(body):
    """Drop comments, join backslash continuations (bounded)."""
    current = None
    joined = 0
    for raw in body:
        line = raw.strip()
        if current is None and (not line or line.startswith('#')):
            continue
        line = '' if line.startswith('#') else _SHELL_COMMENT.sub('', line).rstrip()
        if current is None:
            current, joined = '', 0
        joined += 1
        if line.endswith('\\') and joined < MAX_CONTINUATION:
            current += line[:-1] + ' '
            continue
        current += line.rstrip('\\')
        yield current
        current = None
    if current is not None:
        yield current


def _segments(line):
    """Split on &&, ||, ;, | outside quotes (best effort)."""
    out, buf, quote, i = [], [], None, 0
    while i < len(line):
        ch = line[i]
        if quote:
            if ch == quote:
                quote = None
            buf.append(ch)
        elif ch in '"\'':
            quote = ch
            buf.append(ch)
        elif (ch in ';|' or line.startswith('&&', i)
              or (ch == '&' and line[i - 1:i] not in ('>', '<') and line[i + 1:i + 2] != '>')):
            out.append(''.join(buf))
            buf = []
            if line.startswith(('&&', '||'), i):
                i += 1
        else:
            buf.append(ch)
        i += 1
    out.append(''.join(buf))
    return out


_REDIRECT = re.compile(r'(?:\d*|&)(?:>>?|<)(?:&\d+|[^<>\s]*)')
_REDIRECT_BARE = re.compile(r'(?:\d*|&)(?:>>?|<|>&)')


def _tokens(segment):
    tokens, unbalanced, skip = [], False, False
    for tok in segment.split():
        if skip:
            skip = False
            continue
        if _REDIRECT.fullmatch(tok):
            skip = _REDIRECT_BARE.fullmatch(tok) is not None
            continue
        if len(tok) >= 2 and tok[0] == tok[-1] and tok[0] in '"\'' and tok.count(tok[0]) == 2:
            tok = tok[1:-1]
        elif tok.count('"') % 2 or tok.count("'") % 2:
            unbalanced = True
        tokens.append(tok)
    return tokens, unbalanced


def _classify(token, eco, notes):
    """Return (name, spec) for a static package, or None after counting the reason."""
    if '${{' in token or any(c in token for c in '$%`'):
        notes['ci_install_dynamic'] += 1
        return None
    local = ('://' in token or token.startswith(('.', '/', '~', '\\')) or '\\' in token
             or token.endswith(('.whl', '.tar.gz', '.zip', '.tgz')))
    if eco == 'npm':
        local = local or token.startswith(('file:', 'link:', 'git+', 'git:', 'github:', 'workspace:', 'http:', 'https:'))
    else:
        local = local or '/' in token or '@' in token
    if local:
        notes['ci_install_local_or_url'] += 1
        return None
    if eco == 'python':
        m = SPEC.fullmatch(token) if ';' not in token else None
        if not m:
            notes['ci_install_unsupported'] += 1
            return None
        name, spec = normalize(m[1]), (m[2] or '').strip()
    else:
        at = token.rfind('@')
        name, spec = (token[:at], token[at + 1:]) if at > 0 else (token, '')
        if not NPM_NAME.fullmatch(name) or not _NPM_SPEC.fullmatch(spec):
            notes['ci_install_unsupported'] += 1
            return None
        name = name.lower()
    if len(name) > MAX_NAME or len(spec) > MAX_SPEC:
        notes['ci_install_unsupported'] += 1
        return None
    return name, spec


def _segment_packages(segment, notes):
    """Yield (ecosystem, name, spec) for one shell segment."""
    tokens, unbalanced = _tokens(segment)
    while tokens and (tokens[0] == 'sudo' or _ENV_ASSIGN.match(tokens[0])):
        was_sudo = tokens[0] == 'sudo'
        tokens = tokens[1:]
        while was_sudo and tokens and tokens[0].startswith('-'):
            flag = tokens[0]
            tokens = tokens[2:] if flag in ('-u', '-g', '-C', '-h', '-p') else tokens[1:]
    for prefix, eco, kind in _COMMANDS:
        if tuple(tokens[:len(prefix)]) == prefix:
            break
    else:
        return
    if unbalanced:
        notes['ci_install_unsupported'] += 1
        return
    rest = tokens[len(prefix):]
    values = _VALUE_OPTIONS[kind]
    i = 0
    while i < len(rest):
        tok = rest[i]
        i += 1
        if tok.startswith('-'):
            opt = tok.split('=', 1)[0]
            inline = '=' in tok
            if kind == 'npx':
                if tok == '--' or opt in ('-c', '--call'):
                    return
                if opt in ('-p', '--package'):
                    if not inline and i >= len(rest):
                        return
                    value = tok.split('=', 1)[1] if inline else rest[i]
                    found = _classify(value, eco, notes)
                    if found:
                        yield (eco, *found)
                    return
            if kind == 'pip' and opt in _PIP_REQ:
                notes['ci_install_requirements_file'] += 1
            elif kind == 'pip' and opt in _PIP_EDIT:
                notes['ci_install_local_or_url'] += 1
            elif opt not in values:
                continue
            if not inline:
                i += 1  # consume the option's value; never stored
            continue
        found = _classify(tok, eco, notes)
        if found:
            yield (eco, *found)
        if kind == 'npx':
            return


def ci_install_declarations(text, flavor, scope, notes):
    """Return (ecosystem, name, specification, scope) rows for static CI installs."""
    result, distinct = [], set()
    for body in _script_bodies(text, flavor, notes):
        for line in _logical_lines(body):
            if len(line) > MAX_LINE:
                if _COMMAND_WORD.search(line):
                    notes['ci_install_unsupported'] += 1
                continue
            # A `${{ ... }}` expression is one opaque dynamic token, never split or read.
            line = _EXPRESSION.sub('${{}}', line)
            for segment in _segments(line):
                for eco, name, spec in _segment_packages(segment, notes):
                    row = (eco, name, spec, scope)
                    if row not in distinct and len(distinct) >= MAX_CI_INSTALLS:
                        notes['ci_install_over_limit'] += 1
                        continue
                    distinct.add(row)
                    result.append(row)
    return result
