"""Bounded lexical metadata from conventional, local CI filenames."""
import re

MAX_REFS = 20
SEMANTICS = 'lexical declarations only; not YAML validation, execution, deployment, or approval'
_TOKEN = r'[A-Za-z0-9_][A-Za-z0-9_.-]{0,79}'
_ALIAS = re.compile(_TOKEN)
_REPO = re.compile(rf'{_TOKEN}(?:/{_TOKEN}){{0,2}}')
_FILE = re.compile(rf'(?:\./|/)?(?:{_TOKEN}/)*{_TOKEN}\.ya?ml(?:@{_TOKEN})?')


def _scalar(value):
    return re.sub(r'\s+#.*$', '', value).strip()


def _unquote(value):
    if len(value) >= 2 and value[0] in "\"'" and value[-1] == value[0]:
        return value[1:-1]
    return value


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
        field = re.fullmatch(r' *(?:(-)[ ]+)?([A-Za-z][A-Za-z0-9_-]*):[ \t]*(.*)', line)
        if field is None:
            continue
        item, key, raw = field.groups()
        value = _scalar(raw)
        if re.fullmatch(r'[|>][+\-1-9]{0,2}', value):
            # For "- script: |" the content is indented beyond the key.
            block_indent = field.start(2)
        while parents and (indent < parents[-1][0] or
                           (indent == parents[-1][0] and not item)):
            parents.pop()
        if repository is not None and indent <= repository[0]:
            repository = None
        chain = [entry[1] for entry in parents]
        value = _unquote(value)
        if key == 'template' and (item or chain == ['extends']):
            if len(value) <= 180 and _FILE.fullmatch(value):
                emit({'kind': 'template', 'reference': value})
            else:
                omitted += 1
        elif item and key == 'repository' and chain == ['resources', 'repositories']:
            if _ALIAS.fullmatch(value):
                repository = (indent, value)
            else:
                omitted += 1
        elif repository and not item and key == 'name' and chain == ['resources', 'repositories']:
            # Name must be a sibling property of the current list item.
            if field.start(2) == repository[0] + 2 and _REPO.fullmatch(value):
                emit({'kind': 'repository', 'alias': repository[1], 'reference': value})
            else:
                omitted += 1
            repository = None
        if not item and not value and key in {'resources', 'repositories', 'extends'}:
            parents.append((indent, key))
    return {'semantics': SEMANTICS, 'references': refs, 'omitted_or_over_limit': omitted}
