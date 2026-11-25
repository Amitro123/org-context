# Spec: index Claude Code skills and agents (SKILL.md frontmatter) as declared capabilities

Issue: #10. Intake: accepted, risk medium, surface security (parsing untrusted files from scanned repositories).

## 1. Problem

`find_capabilities` only knows capabilities declared in an `org-context.manifest.json`. A Claude Code
plugin or skill repository already states what each skill does in its `SKILL.md` frontmatter
(`name`, `description`, `when_to_use`), but the scanner never reads `SKILL.md` or `agents/*.md`. So
in a trial on a plugin marketplace (about 25 skills) `find_capabilities("redact")` returned
`results: []`, although one skill's description says it "Lints for private data". The people
affected are agents and developers asking "which skill does X?" across scanned roots, who today must
hand-write a manifest for repositories that already describe themselves.

## 2. Scope

In scope:

- During `scan`, find these files under the scan root, and only these:
  `skills/*/SKILL.md`, `plugins/*/skills/*/SKILL.md` (role `skill`), and `agents/*.md` (role `agent`).
- Read only a bounded prefix of each file, parse only the YAML frontmatter block between the first two
  `---` lines, and keep only `name`, `description`, `when_to_use`.
- Store each valid one as a capability row: id = `name`, summary = `description`,
  responsibility = `when_to_use` (or empty), role `skill` or `agent`, the file as the single entry
  point, marked `declared_skill_frontmatter` (separate from manifest capabilities).
- A missing or malformed frontmatter skips that file and is counted in the scan coverage; it never
  fails the root scan.
- Per-file and aggregate limits on these files.
- `find_capabilities` returns these rows next to manifest rows, with a field telling them apart.
- Docs (`docs/CAPABILITY_MANIFEST.md`) describe the new source.

Out of scope:

- Reading, storing or interpreting skill bodies (everything after the closing `---`), the skill's
  scripts, references or any other file in the skill directory.
- Running anything.
- Other frontmatter keys (`allowed-tools`, `model`, `metadata`, ...), and other locations
  (`plugins/*/agents/*.md`, `.claude/skills/`, `.claude/agents/`, `commands/`, nested skill dirs).
- Using `plugin.json` / `marketplace.json` `skills` or `agents` path overrides.
- Full YAML semantics; adding a YAML dependency (the package has `dependencies = []`).
- Changing the manifest format, the manifest's fail-the-root behaviour, or the `capabilities`
  table schema (no migration).
- Ranking or fuzzy matching changes in `find_capabilities`.

## 3. Design

### 3.1 New module `org_context/skill_frontmatter.py`

Holds the parser and the file discovery for this source, so `cli.py` stays a thin caller and the
evidence-id logic stays in `cli.py`.

Constants:

```
SKILL_PATTERNS = (('skills/*/SKILL.md', 'skill'),
                  ('plugins/*/skills/*/SKILL.md', 'skill'),
                  ('agents/*.md', 'agent'))
FRONTMATTER_MAX_BYTES = 16384      # bytes read per file (prefix), never more
FRONTMATTER_MAX_LINES = 200        # lines between the delimiters
MAX_SKILL_CANDIDATES  = 256        # glob results collected before stopping
MAX_SKILL_FILES       = 64         # files actually read per root
MAX_SKIPPED_LISTED    = 64         # skipped entries listed in coverage (all are counted)
DESCRIPTION_MAX = 1024; WHEN_TO_USE_MAX = 1024
EVIDENCE_KIND = 'skill_frontmatter'
DECLARATION_SOURCE = 'declared_skill_frontmatter'
```

Aggregate bound that follows: at most 64 x (16384 + 1) bytes, about 1 MiB, read per root for this
source. These files are not added to `cli.ALLOWED`/`cli.PATTERNS`, do not count toward `cli.MAX_FILES`
or the 2,000,000-byte total of the main loop, and so can never make the main scan raise.

Types and functions (names are a suggestion; behaviour is the contract):

```
class FrontmatterError(ValueError): reason: str     # one of the skip reasons in 3.4

def parse_frontmatter(data: bytes) -> dict[str, str]
    # data = the bytes read (<= FRONTMATTER_MAX_BYTES + 1). Returns
    # {'name', 'description', 'when_to_use'} (when_to_use '' if absent), validated and normalized,
    # plus 'frontmatter_sha256' = sha256 of the bytes from offset 0 through the end of the
    # closing delimiter line. Raises FrontmatterError(reason) otherwise. Never returns body text.

def collect_skill_declarations(root: Path, taken_ids: set[str], is_safe) -> tuple[list[dict], dict]
    # is_safe = cli.safe (passed in to avoid an import cycle). Returns (declarations, coverage_block).
    # Each declaration: {'path', 'role', 'component', 'capability_id', 'summary',
    #                    'responsibility', 'frontmatter_sha256'}.
```

### 3.2 Discovery

1. For each pattern in `SKILL_PATTERNS`, iterate `root.glob(pattern)` lazily; collect `(path, role)`
   until `MAX_SKILL_CANDIDATES` are held in total, then stop iterating and set `truncated: true`.
2. Sort by relative POSIX path. The first `MAX_SKILL_FILES` are processed; the rest are skipped with
   reason `file_limit` (not opened).
3. Per file, in order: relative path must pass the same rules as `capabilities._validate_relpath`
   (printable, <= 256 chars, no `\`, `:`, `..`) else `invalid_path`; `is_safe(root, path)` else
   `symlink`; `path.is_file()` else `not_a_file`; open `rb` and `read(FRONTMATTER_MAX_BYTES + 1)`;
   `OSError` -> `read_error`. Then `parse_frontmatter`.
4. Component: `plugins/<p>/skills/...` -> `<p>` (must be printable and 1..128 chars, else
   `invalid_path`); `skills/...` and `agents/...` -> `root.name`.
5. Duplicate ids: `taken_ids` starts as the ids of manifest capabilities from the main loop. A
   declaration whose `capability_id` is already taken is skipped with `duplicate_capability_id`
   (manifest wins; then first in sorted path order wins). Needed because the table key is
   `(project_id, capability_id)`.

### 3.3 Frontmatter parser (bounded subset of YAML, no dependency)

- Strip one leading UTF-8 BOM. Line 1 must be exactly `---` (trailing spaces/`\r` allowed), else
  `no_frontmatter` (this includes empty files).
- The closing delimiter is the next line that is exactly `---` (or `...`). It must lie within the
  first `FRONTMATTER_MAX_BYTES` bytes and within `FRONTMATTER_MAX_LINES` lines; if not found and the
  read hit the byte cap -> `frontmatter_too_large`; if not found and the file ended -> `malformed_frontmatter`.
- Decode only the block (strict UTF-8; failure -> `malformed_frontmatter`). Bytes after the closing
  delimiter are discarded and never decoded, stored or hashed.
- Lines in the block: blank lines and lines starting with `#` are ignored. A line at column 0 must be
  `key:` or `key: value` with `key` matching `[A-Za-z_][A-Za-z0-9_-]*`, else `malformed_frontmatter`.
  Indented lines belong to the preceding key. Keys other than the three are ignored together with
  their indented lines, whatever those contain. A tab used for indentation -> `malformed_frontmatter`.
- Duplicate `name`, `description` or `when_to_use` -> `malformed_frontmatter`.
- Value forms accepted for the three keys:
  - plain scalar: rest of the line, with a trailing ` #...` comment removed; indented continuation
    lines are folded with one space. A colon inside the value is accepted (lenient, as Claude Code is).
    A value starting with `[`, `{`, `&`, `*`, `!`, `%`, `@` or a backtick -> `malformed_frontmatter`.
  - double-quoted: may continue on indented lines (folded); escapes `\\ \" \/ \n \t \r \uXXXX` only;
    any other escape or no closing quote -> `malformed_frontmatter`.
  - single-quoted: `''` is a literal quote; may continue on indented lines; no closing quote -> malformed.
  - block scalar `|` or `>` with optional `-`/`+` chomping indicator; an explicit indentation digit ->
    `malformed_frontmatter`. Content is the following indented lines.
- Normalization of each value: collapse every whitespace run (including newlines) to one space and
  strip. Then reject (`invalid_field`) any remaining character in Unicode category `Cc`, and the bidi
  controls U+200E, U+200F, U+202A..U+202E, U+2066..U+2069.
- Validation: `name` missing -> `missing_name`; not matching `capabilities._CAP_ID`
  (`^[a-z0-9][a-z0-9._-]{0,63}$`) -> `invalid_name`. `description` missing or empty -> `missing_description`;
  longer than 1024 -> `invalid_field`. `when_to_use` optional; longer than 1024 -> `invalid_field`.
  Values are rejected, never truncated.
- The parser is iterative (no recursion), O(lines), and touches no file system.

### 3.4 Skip reasons (closed set)

`invalid_path`, `symlink`, `not_a_file`, `read_error`, `no_frontmatter`, `malformed_frontmatter`,
`frontmatter_too_large`, `missing_name`, `invalid_name`, `missing_description`, `invalid_field`,
`duplicate_capability_id`, `file_limit`. Skip entries carry only `path` and `reason`, never file text
and never exception messages.

### 3.5 Changes in `cli.scan` (`org_context/cli.py`)

After the main loop (after line 231) and before `coverage` is built (line 238):

- Call `collect_skill_declarations(root, {c[3] for c in caps}, safe)` (entries of `caps` are
  `(pid, component, role, cap_id, summary, responsibility, payload, eid)`, so the id is index 3).
- For each declaration: `eid = digest(pid + ':' + path + ':' + frontmatter_sha256)`; append evidence
  record `(eid, pid, path, frontmatter_sha256, 'skill_frontmatter', 'bounded_frontmatter_parser', None)`
  (content is always `None`); append `path` to `checked`; append capability row
  `(pid, component, role, capability_id, summary, responsibility, payload_json, eid)` with

  ```
  payload = {'source': 'declared_skill_frontmatter',
             'entrypoints': [{'path': path, 'path_status': 'observed_local_file_frontmatter_only',
                              'file_sha256': None, 'symbol': None, 'symbol_status': None}],
             'references': [], 'out_of_scope': []}
  ```
  (`file_sha256` is `None` because the whole file is never read; `evidence.sha256` is the hash of the
  frontmatter block only.)
- Add `coverage['skill_frontmatter']`, always present (also when zero files were found):

  ```
  {'patterns_checked': ['skills/*/SKILL.md', 'plugins/*/skills/*/SKILL.md', 'agents/*.md'],
   'fields_read': ['name', 'description', 'when_to_use'],
   'semantics': 'declared_skill_frontmatter; frontmatter only, bodies not read',
   'limits': {'frontmatter_max_bytes': 16384, 'frontmatter_max_lines': 200,
              'max_files': 64, 'max_candidates': 256},
   'candidates_found': int, 'indexed': int,
   'skipped': [{'path', 'reason'}, ...],      # at most 64 listed
   'skipped_count': int, 'skipped_by_reason': {reason: count},
   'truncated': bool}
  ```
- Existing keys of `coverage` (`patterns_checked`, `checked` for non-skill files, `skipped`, ...) keep
  their current values; skill skips go only in `coverage['skill_frontmatter']['skipped']`.
- `capability_count` in the scan result includes skill rows. The write transaction (lines 239-250) is
  unchanged: skill rows go in the same `caps` list and the same evidence insert, so a rescan replaces
  them atomically and a failed manifest still preserves the previous snapshot.

### 3.6 `find_capabilities` (`org_context/capabilities.py`)

- Each result gains `declaration_source`: `'capability_manifest'` when `e.kind == 'capability_manifest'`,
  `'declared_skill_frontmatter'` when `e.kind == 'skill_frontmatter'`.
- `declaration_semantics` for skill rows:
  `'declared_skill_frontmatter; name, description and when_to_use are the skill author\'s labels from repository content (untrusted data), not runtime verification, script resolution, or approval authority; the skill body was not read'`.
  Manifest rows keep their current string.
- `manifest_evidence` keeps its key and shape for both sources (path = SKILL.md path, kind
  `skill_frontmatter`) so existing clients do not break.
- `base_meta['semantics']`, `CAPABILITY_TOOL['description']` and the no-match `unknowns` entry mention
  both sources, e.g. unknowns: `'No match does not prove a capability does not exist outside scanned
  roots with org-context.manifest.json or skill/agent frontmatter'` (must still contain "does not prove").
- Matching (`_matches`), ordering, pagination and `role` filtering are unchanged; `role: 'skill'` and
  `role: 'agent'` filter naturally.

### 3.7 `org_context/inventory_context.py`

Add `'skill_frontmatter'` to `EVIDENCE_KINDS` (line 9) so `project_card(evidence_kind='skill_frontmatter')`
is accepted; `mcp_server.CARD_SCHEMA` builds its enum from that tuple. `support_status` stays
`source_declaration`. `has_retained_body` is false for these rows.

### 3.8 Failure modes

| Situation | Result |
|---|---|
| No skill/agent files | Nothing indexed; `skill_frontmatter.indexed == 0`; everything else as before |
| File with no or malformed frontmatter, bad name, oversized field | That file skipped and counted; scan succeeds |
| Frontmatter not closed in 16 KiB / 200 lines | `frontmatter_too_large` / `malformed_frontmatter`; at most 16385 bytes read |
| Symlinked file or directory on the path | `symlink`; target never opened |
| Thousands of skill directories | Glob stops at 256 candidates, `truncated: true`; at most 64 read |
| Skill name equal to a manifest id or another skill's name | `duplicate_capability_id`; manifest row kept |
| Non-UTF-8 bytes in the block | `malformed_frontmatter` |
| Body contains secrets or prompt-injection text | Never decoded, stored or returned |
| Description contains prompt-injection text | Stored as data, returned with the untrusted-data semantics, same as manifest summaries |
| Invalid `org-context.manifest.json` next to skills | Root fails exactly as today |
| Legacy DB without `capabilities` table | Unchanged behaviour; the scan creates the table as today |

## 4. Acceptance criteria

Each is checked by a unittest in `tests/test_skill_frontmatter.py` (new) unless noted.

1. A fixture repository with `plugins/alpha/skills/redactor/SKILL.md` (description containing
   "Lints for private data") and `skills/reviewer/SKILL.md` (different description): after
   `cli.scan`, `find_capabilities({'query': 'private data'})` returns exactly the `redactor` row with
   `role == 'skill'`, `component == 'alpha'`, `declaration_source == 'declared_skill_frontmatter'`,
   `entrypoints[0]['path'] == 'plugins/alpha/skills/redactor/SKILL.md'`, and
   `manifest_evidence['kind'] == 'skill_frontmatter'`; a query on a word of the other description
   returns the `reviewer` row with path `skills/reviewer/SKILL.md` and `component == root.name`.
2. `agents/triage.md` with valid frontmatter is returned with `role == 'agent'`;
   `find_capabilities({'query': ..., 'role': 'agent'})` returns it and no skill rows.
3. `when_to_use` text is searchable: a query on a word that appears only in `when_to_use` returns the
   skill, and that text is in `responsibility`. A skill without `when_to_use` is indexed with
   `responsibility == ''`.
4. Each of these is skipped with the listed reason, appears in `coverage['skill_frontmatter']['skipped']`
   and `skipped_by_reason`, and the scan returns normally with the other skills indexed: no
   frontmatter (`no_frontmatter`), empty file (`no_frontmatter`), unclosed block (`malformed_frontmatter`),
   flow mapping value `description: {a: b}` (`malformed_frontmatter`), missing `name` (`missing_name`),
   `name: Bad Name` (`invalid_name`), missing `description` (`missing_description`), 1025-char
   description (`invalid_field`), description with a U+202E character (`invalid_field`).
5. Bounds: a SKILL.md whose frontmatter does not close within 16384 bytes is skipped as
   `frontmatter_too_large`; a test that wraps `Path.open`/the read (or a file-like spy) shows no read
   of more than 16385 bytes per skill file. With 70 valid skills, 64 are indexed, 6 are skipped as
   `file_limit`, and `skipped_count == 6`.
6. Bodies are never stored or returned: a fixture whose body (after the closing `---`) contains
   `BODY_SECRET_SENTINEL` yields no occurrence of the sentinel in any column of `evidence` or
   `capabilities` (checked by SQL over all text columns), in the scan result JSON, in
   `project_card` output with evidence pages enabled, or in `find_capabilities` output. The skill's
   `evidence.content` is NULL and `evidence.sha256` equals sha256 of the frontmatter block bytes.
7. Parser forms: plain, multi-line plain, double-quoted with `\"` and `é`, single-quoted with
   `''`, `>` and `|` block scalars (with `-` chomping), CRLF line endings, a leading BOM, and an
   ignored nested `metadata:` map each parse to the expected normalized single-line value
   (unit tests on `parse_frontmatter` only).
8. Symlinks: `skills/evil -> /some/outside/dir` and `skills/x/SKILL.md -> ../../secret.md` are skipped
   as `symlink`, and the target is not opened.
9. Duplicates: a manifest capability `reviewer` and a skill named `reviewer` in the same root give one
   row, from the manifest; the skill is skipped as `duplicate_capability_id`. Two skills with the same
   name keep the one with the lexically first path.
10. A repository without skills behaves as before: for a fixture with README, pyproject and a
    manifest, the scan's evidence rows, dependency rows, capability rows and `find_capabilities`
    results are equal to the expected values from the existing tests; `coverage['patterns_checked']`
    equals `list(cli.PATTERNS)` unchanged; the only new coverage key is `skill_frontmatter` with
    `indexed == 0` and `skipped == []`. The full existing suite passes unchanged:
    `python -m unittest discover -s tests -v`.
11. An invalid `org-context.manifest.json` alongside valid skills still fails the root scan with
    `ValueError` and leaves the previous snapshot intact (no skill rows written).
12. A rescan after deleting a SKILL.md removes its capability and evidence rows.
13. `project_card` accepts `evidence_kind: 'skill_frontmatter'` and lists the SKILL.md evidence with
    `has_retained_body == False`.
14. `docs/CAPABILITY_MANIFEST.md` documents the three patterns, the three fields, the limits, the
    skip reasons and `declaration_source` (reviewer check).

## 5. Open questions (with the assumption taken)

- Id collisions across plugins in a marketplace root (two plugins each with a `review` skill). The
  issue says id = the skill's `name`, and the table key is `(project_id, capability_id)`. Assumed:
  keep id = `name`, skip later duplicates and count them (`duplicate_capability_id`). Scanning each
  `plugins/*/` root separately, as in the trial, avoids collisions. Namespacing (`<plugin>.<name>`)
  can come later if wanted.
- Whole-file hash. Assumed: the file is never read past the frontmatter, so the entry point has
  `file_sha256: null` and `path_status: 'observed_local_file_frontmatter_only'`; the evidence hash covers
  only the frontmatter block. This follows the issue's "read only the YAML block".
- Plain scalars containing `: ` are accepted (strict YAML rejects them); assumed, because real skill
  descriptions often contain colons and Claude Code accepts them.

## Code map

Files the change touches or depends on (paths relative to the worktree
`/home/user/org-context/.factory/github-issue-10-index-claude-code-skills`):

- `org_context/cli.py` (CHANGE: calls the new collector and writes its rows)
  - `cli.py:12` imports from `capabilities`; add the import of the new module here.
  - `cli.py:15-20` `ALLOWED`, `PATTERNS`, `MAX_BYTES=256000`, `MAX_FILES=128`: leave unchanged
    (skill patterns must not go here, see 3.1).
  - `cli.py:23-24` `SourceFormatError`: do not raise it for skill files.
  - `cli.py:26-27` `digest(text)`: sha256 hex of a str; used for project and evidence ids.
  - `cli.py:170-175` `safe(root, path)`: symlink and escape check per path part; pass it to the collector.
  - `cli.py:177-251` `scan(db, supplied)`: 182 state lists; 183-188 candidate collection with the
    `MAX_FILES` raise; 189-231 main read loop (2,000,000-byte total raise at 200; manifest parsed at
    219-221 into `caps` tuples `(pid, component, role, cap_id, summary, responsibility, payload, eid)`);
    238 `coverage` dict; 239-250 single write transaction (capabilities table DDL at 242, inserts 247-250);
    251 result dict with `capability_count`.
- `org_context/skill_frontmatter.py` (CREATE: bounded frontmatter parser and skill/agent discovery,
  kept out of `cli.py` and `capabilities.py`).
- `org_context/capabilities.py` (CHANGE: result source field, semantics strings, tool description)
  - `capabilities.py:14` `_CAP_ID` regex; reuse for `name` validation (import, do not copy).
  - `capabilities.py:20-39` `CAPABILITY_TOOL`: description text to update.
  - `capabilities.py:42-58` `_require_text`, `_validate_relpath(raw, label, error_cls)`: path rules to
    reuse for skill paths (call with a local error class, catch and map to `invalid_path`).
  - `capabilities.py:85-171` `parse_capability_manifest(root, text, *, max_file_bytes, error_cls)`:
    returns `(component, role, cap_id, summary, responsibility, payload_json)` tuples; unchanged.
  - `capabilities.py:174-194` `_tokenize`, `_matches`: unchanged; already search id, component, role,
    summary, responsibility, entrypoint paths.
  - `capabilities.py:197-323` `find_capabilities(context, args)`: `base_meta` semantics 222-226; SQL
    259-269 already selects `e.kind AS manifest_kind`; result dict 282-311 (add `declaration_source`,
    branch `declaration_semantics` at 307-310); no-match `unknowns` 320-322.
- `org_context/inventory_context.py` (CHANGE: accept the new evidence kind)
  - `inventory_context.py:9` `EVIDENCE_KINDS` tuple; add `'skill_frontmatter'`.
  - `inventory_context.py:77-91` evidence projection; `support_status` at 89 (no change needed).
- `org_context/mcp_server.py` (DEPENDS, no change): `:14` `CARD_SCHEMA` enum built from
  `EVIDENCE_KINDS`; `:57` dispatches `find_capabilities`.
- `org_context/evidence_pages.py` (DEPENDS, no change): `:35` returns evidence `kind`/`method`;
  content is NULL for skill rows.
- `tests/test_skill_frontmatter.py` (CREATE: tests for AC 1-13). Reuse the helpers pattern from
  `tests/test_capabilities.py:15-29` (`_init_session`, `_call_tool`) and `:53-66` (setUp with
  `cli.connect`, `_write`).
- `tests/test_capabilities.py` (DEPENDS; must pass unchanged): `:110` asserts
  `manifest_evidence['kind'] == 'capability_manifest'`, `:123` asserts `'not runtime verification'` in
  `declaration_semantics`, `:128` asserts `'does not prove'` in unknowns, `:131-135` plain repo
  `capability_count == 0`.
- `tests/test_python_coverage.py:63` asserts `'requirements-*.txt' in coverage['patterns_checked']`
  (keep `patterns_checked` unchanged).
- `tests/test_reference_mcp.py:60` fixed tool set (no new tool is added).
- `docs/CAPABILITY_MANIFEST.md` (CHANGE: document the new source; sections 64-72 bounds, 74-80 the tool).
- `README.md:66` mentions `find_capabilities`; one line noting skill frontmatter (optional, same task as docs).
- `skills/org-context-discovery/SKILL.md` (DEPENDS): this repository's own skill; scanning this
  repository will now index it (expected).

Types used (copied, short):

```
capabilities(project_id TEXT, component TEXT NOT NULL, role TEXT NOT NULL, capability_id TEXT NOT NULL,
             summary TEXT NOT NULL, responsibility TEXT NOT NULL, payload TEXT NOT NULL,
             evidence_id TEXT REFERENCES evidence(id), PRIMARY KEY(project_id, capability_id))
evidence(id TEXT PRIMARY KEY, project_id TEXT, path TEXT, sha256 TEXT, kind TEXT, method TEXT, content TEXT)
scan_coverage(project_id TEXT PRIMARY KEY, payload TEXT NOT NULL)   # JSON of the coverage dict
evidence record tuple: (eid, pid, path, sha256, kind, method, content)
```

Copies of the logic touched:

- Capabilities DDL appears twice in `cli.py` (`:40` in `connect`, `:242` in `scan`): unchanged, out of scope.
- Path validation: `capabilities._validate_relpath` and `cli.safe` are the single sources; reuse, do
  not copy. In scope (reused).
- Declaration semantics strings: `capabilities.py:222-226` and `:307-310`; both in scope.
- Evidence kinds list: only `inventory_context.py:9` (the MCP enum derives from it). In scope.
- No bundled, generated or vendored copy of the scanner exists in the repository.

Test commands:

```
python -m unittest tests.test_skill_frontmatter -v
python -m unittest discover -s tests -v
```
