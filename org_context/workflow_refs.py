"""Bounded lexical metadata from conventional, local CI filenames."""
import re

MAX_REFS = 20
SEMANTICS = 'lexical declarations only; not YAML validation, execution, deployment, or approval'
_TOKEN = r'[A-Za-z0-9_][A-Za-z0-9_.-]{0,79}'
_ALIAS = re.compile(_TOKEN)
_REPO = re.compile(rf'{_TOKEN}(?:/{_TOKEN}){{0,2}}')
_FILE = re.compile(rf'(?:\./|/)?(?:{_TOKEN}/)*{_TOKEN}\.ya?ml(?:@{_TOKEN})?')
_ITEM = re.compile(r'(-)(?:[ \t]+|$)')
_SIMPLE_FIELD = re.compile(r'(?:([A-Za-z][A-Za-z0-9_-]*)|(["\'])([A-Za-z][A-Za-z0-9_-]*)\2):[ \t]*(.*)')
_GENERIC_FIELD = re.compile(r'(.+?):(?:[ \t]+(.*)|[ \t]*)')
_CONDITIONAL_KEY = re.compile(r'\$\{\{\s*(?:(?:if|elseif|each)\b[^}\r\n]{0,200}|else\s*)\}\}')
_BLOCK_SCALAR = re.compile(r'[|>][+\-1-9]{0,2}')
_TEMPLATE_SECTIONS = {'stages', 'jobs', 'steps', 'variables'}
_TEMPLATE_ANCESTORS = {
    'stages', 'jobs', 'steps', 'variables',
    'strategy', 'runOnce', 'rolling', 'canary',
    'preDeploy', 'deploy', 'routeTraffic', 'postRouteTraffic',
    'on', 'success', 'failure',
}
_CONDITIONAL_PARENT = '__conditional__'
_UNSUPPORTED_PARENT = '__unsupported__'


def _scalar(value):
    s = value.strip()
    if not s or s.startswith('#'):
        return ''
    if s[0] in "\"'":
        quote = s[0]
        i = 1
        while i < len(s):
            ch = s[i]
            if quote == '"' and ch == '\\' and i + 1 < len(s):
                i += 2
                continue
            if quote == "'" and ch == "'" and i + 1 < len(s) and s[i + 1] == "'":
                i += 2
                continue
            if ch == quote:
                tail = s[i + 1:]
                if not tail or re.fullmatch(r'[ \t]+(?:#.*)?', tail):
                    return s[:i + 1]
                break
            i += 1
        return s
    return re.sub(r'[ \t]+#.*$', '', s).strip()


def _unquote(value):
    if len(value) >= 2 and value[0] in "\"'" and value[-1] == value[0]:
        return value[1:-1]
    return value


def _is_supported_template_context(item, chain):
    effective = [parent for parent in chain if parent != _CONDITIONAL_PARENT]
    if not item:
        return effective == ['extends']
    return bool(effective) and effective[-1] in _TEMPLATE_SECTIONS and all(
        parent in _TEMPLATE_ANCESTORS for parent in effective
    )


def pipeline_metadata(text):
    """Report only constrained references; never scripts, values, or unmatched lines."""
    refs = []
    seen = set()
    omitted = 0
    block_indent = None
    parents = []
    repository = None

    def emit(ref):
        nonlocal omitted
        key = tuple(ref.items())
        if key in seen:
            return
        if len(refs) >= MAX_REFS:
            omitted += 1
            return
        seen.add(key)
        refs.append(ref)

    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        indent = len(line) - len(line.lstrip(' '))
        if block_indent is not None:
            if indent > block_indent:
                continue
            block_indent = None

        stripped = line[indent:]
        item_match = _ITEM.match(stripped)
        item = '-' if item_match else None
        rest = stripped[item_match.end():] if item_match else stripped
        key_col = indent + (item_match.end() if item_match else 0)

        # Always update indentation and sequence-item boundaries, even for lines
        # whose mapping keys are not directly extractable (e.g. ${{ if ... }}:).
        while parents and (indent < parents[-1][0] or
                           (indent == parents[-1][0] and not item)):
            parents.pop()
        if repository is not None and indent <= repository[0]:
            repository = None

        field = _SIMPLE_FIELD.fullmatch(rest)
        if field is None:
            generic = _GENERIC_FIELD.fullmatch(rest)
            if generic is not None:
                raw_key = generic.group(1).strip()
                value = _scalar(generic.group(2) or '')
                if _BLOCK_SCALAR.fullmatch(value):
                    block_indent = key_col
                elif not value:
                    parent_key = (
                        _CONDITIONAL_PARENT
                        if _CONDITIONAL_KEY.fullmatch(raw_key)
                        else _UNSUPPORTED_PARENT
                    )
                    parents.append((key_col if item else indent, parent_key))
            continue

        key = field.group(1) or field.group(3)
        value = _scalar(field.group(4))
        is_block_scalar = bool(_BLOCK_SCALAR.fullmatch(value))
        if is_block_scalar:
            # For "- script: |" the content is indented beyond the key.
            block_indent = key_col
        chain = [entry[1] for entry in parents]
        value = _unquote(value)
        if key == 'template':
            if _is_supported_template_context(item, chain):
                if len(value) <= 180 and _FILE.fullmatch(value):
                    emit({'kind': 'template', 'reference': value})
                else:
                    omitted += 1
            elif item:
                # Explicitly count list-style template keys in unsupported contexts
                # (such as parameters object data) without asserting them.
                omitted += 1
        elif item and key == 'repository' and chain == ['resources', 'repositories']:
            if _ALIAS.fullmatch(value):
                repository = (indent, value)
            else:
                omitted += 1
        elif repository and not item and key == 'name' and chain == ['resources', 'repositories']:
            # Name must be a sibling property of the current list item.
            if key_col == repository[0] + 2 and _REPO.fullmatch(value):
                emit({'kind': 'repository', 'alias': repository[1], 'reference': value})
            else:
                omitted += 1
            repository = None
        if not value and not is_block_scalar:
            parents.append((key_col if item else indent, key))
    return {'semantics': SEMANTICS, 'references': refs, 'omitted_or_over_limit': omitted}
