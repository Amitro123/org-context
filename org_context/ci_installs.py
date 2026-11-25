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
_KEY_LINE = re.compile(r'^(\s*)(-[ \t]+)?(run|script|bash|pwsh):(?:[ \t]+(.*))?$')
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


def _script_bodies(text, keys):
    """Yield lists of shell lines: one per inline value, or a block scalar body."""
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        m = _KEY_LINE.match(lines[i])
        i += 1
        if not m or m[3] not in keys:
            continue
        key_col = len(m[1]) + len(m[2] or '')
        value = _scalar(m[4] or '')
        if not value:
            continue
        if _BLOCK_SCALAR.fullmatch(value):
            body = []
            while i < len(lines):
                line = lines[i]
                if line.strip() and _indent(line) <= key_col:
                    break
                body.append(line.strip())
                i += 1
            yield body
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in '"\'':
            value = value[1:-1]
        yield [value]


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


def _segment_packages(segment, notes, labels=()):
    """Yield (ecosystem, name, spec) for one shell segment.

    `labels` are the flavor's step keys with a colon (`run:`): a block body line such as
    `run: pip install z` is shell text, not a new step, and its leading label is skipped.
    """
    tokens, unbalanced = _tokens(segment)
    if tokens and tokens[0] in labels:
        tokens = tokens[1:]
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
    keys = _KEYS.get(flavor, ())
    labels = tuple(k + ':' for k in keys)
    result, distinct = [], set()
    for body in _script_bodies(text, keys):
        for line in _logical_lines(body):
            if len(line) > MAX_LINE:
                if _COMMAND_WORD.search(line):
                    notes['ci_install_unsupported'] += 1
                continue
            # A `${{ ... }}` expression is one opaque dynamic token, never split or read.
            line = _EXPRESSION.sub('${{}}', line)
            for segment in _segments(line):
                for eco, name, spec in _segment_packages(segment, notes, labels):
                    row = (eco, name, spec, scope)
                    if row not in distinct and len(distinct) >= MAX_CI_INSTALLS:
                        notes['ci_install_over_limit'] += 1
                        continue
                    distinct.add(row)
                    result.append(row)
    return result
