# Spec: CI install commands as `ci-install:<path>` dependency declarations (issue #11)

## 1. Problem

`find_projects_using` only searches the `dependencies` table, and today that table is filled only
from `requirements*.txt`, `pyproject.toml` and `package.json`. CI workflow files are read, but only
their `uses:` (GitHub Actions) or `template:`/`resources.repositories` (Azure Pipelines) references are
extracted. Repositories that declare their tooling only in CI steps (`run: pip install pytest`,
`npm i -g @anthropic-ai/claude-code@2.1.312`) are invisible to `find_projects_using`, even though
`coverage.checked` lists those workflow files, so the "no result" reads as "not used". The people
affected are users and agents asking "which projects use X?" across scanned roots. The README
already documents the gap ("lexical `uses:` references only ... not ... scripts").

## 2. Scope

In scope:

- A bounded, lexical pass over the shell text of
  - GitHub Actions `run:` values in `.github/workflows/*.yml|*.yaml`, and
  - Azure Pipelines `script:`, `bash:` and `pwsh:` values in the conventional root
    `azure-pipelines*.yml|yaml` files already scanned,
  that recognizes these install commands only: `pip install`, `pip3 install`,
  `python -m pip install`, `python3 -m pip install`, `pipx install`, `uv pip install`,
  `uv tool install`, `npm i`, `npm install` (with or without `-g`/`--global`), and `npx <pkg>`
  (the package only, never the command's arguments).
- Each static package becomes a row in `dependencies` with `ecosystem` `python` or `npm`, the
  same name normalization as today, the version specifier when given (`==8.3`, `1.2.3`), and
  scope `ci-install:<workflow relative path>`, linked to the workflow's existing evidence row.
- Non-static or non-package arguments are not stored and are counted in
  `scan_coverage.omitted_declarations` by reason, like dynamic `uses:` values today.
- Docs updated: `README.md` "Extraction and coverage", `docs/WORKFLOW_EXTRACTION.md`,
  `docs/MCP_DEMO.md`.

Out of scope (stays out):

- Executing anything, or evaluating shell/YAML (no variable expansion, no conditions, no matrix).
- Indirect installs (what a script, `make`, `tox`, `nox`, `pre-commit`, a Dockerfile or a
  requirements file referenced by `-r` installs); `-r` files are covered only by the existing
  root requirements extraction.
- Lockfiles; other package managers (`poetry`, `pdm`, `conda`, `yarn`, `pnpm`, `bun`, `apt`, `brew`,
  `cargo`, `go install`, `gem`, ...); `npm ci`; `uvx`; `npm add`.
- Full YAML or shell parsing; flow-style YAML; anchors; PowerShell backtick continuation;
  heredocs; `run:`/`script:` in files other than the ones already scanned (no new file patterns).
- Changes to `find_projects_using`/`shared_dependencies` query logic, MCP tool schemas, or the DB
  schema.

## 3. Design

### 3.1 New module `org_context/ci_installs.py`

One public function, pure (no I/O):

```
ci_install_declarations(text: str, flavor: str, scope: str, notes: collections.Counter)
    -> list[tuple[str, str, str, str]]   # (ecosystem, name, specification, scope)
```

- `flavor` is `'github'` (keys: `run`) or `'azure'` (keys: `script`, `bash`, `pwsh`).
- `scope` is passed in by the caller as `'ci-install:' + name` (the posix relative path).
- `notes` is the per-file `Counter` already used by `parse_with_notes`; the function only adds
  counts under the reason names in 3.4. It never records any text from the file.
- Constants at module top: `MAX_CI_INSTALLS = 50` (stored rows per file), `MAX_LINE = 4096`
  (chars per logical line), `MAX_CONTINUATION = 20` (physical lines joined per logical line),
  `MAX_NAME = 100`, `MAX_SPEC = 80`.

Do not modify `workflow_refs.pipeline_metadata` or `cli.workflow_actions`: their outputs must stay
byte-for-byte the same (pipeline_metadata deliberately skips block scalar bodies; that stays).

### 3.2 Locating step shell text (line based)

Iterate `text.splitlines()`. A key line matches
`^(\s*)(-[ \t]+)?(run|script|bash|pwsh):(?:[ \t]+(.*))?$` with the key restricted by flavor
(quoted keys are not required). Let `key_col` be the column of the key.

- Value, after stripping a trailing ` #comment` (reuse the same rule as `workflow_refs._scalar`, or
  import it), is:
  - a block scalar indicator (`|`, `>`, with optional chomping/indent modifiers, regex as
    `workflow_refs._BLOCK_SCALAR`): the body is every following line whose indent is greater than
    `key_col`, plus blank lines inside it; the body ends at the first non-blank line with indent
    `<= key_col`. Body lines are de-indented and processed one by one (folded `>` is also read
    line by line).
  - empty: nothing (covers `defaults: run: shell: bash`, a mapping, not a script).
  - otherwise an inline scalar: unquote one level of matching `"`/`'` quotes; process as one line.
- Lines elsewhere in the file are ignored. Body lines are never matched as YAML keys (a
  `run:` inside a block body is shell text, not a new step).

### 3.3 Reading a shell line

1. Skip blank lines and lines whose first non-space char is `#`; strip a trailing
   whitespace-preceded `#...` comment.
2. Backslash continuation: a line ending in `\` is joined with the next line (up to
   `MAX_CONTINUATION` lines). A logical line longer than `MAX_LINE` is skipped and counted
   `ci_install_unsupported` only if it contains one of the command words (`pip`, `pipx`, `uv`,
   `npm`, `npx`); otherwise silently skipped.
3. Split into segments on `&&`, `||`, `;`, `|` (outside quotes, best effort).
4. Tokenize each segment on whitespace. A token wholly wrapped in matching `"` or `'` is unquoted.
   A token with an unbalanced quote makes the segment `ci_install_unsupported` if it is an
   install command; otherwise ignored.
5. Drop leading `sudo` and leading `NAME=value` environment assignments.
6. Match the command prefix (exact tokens):
   - python: `pip install`, `pip3 install`, `python -m pip install`, `python3 -m pip install`,
     `pipx install`, `uv pip install`, `uv tool install`
   - npm: `npm i`, `npm install`, `npx`
   Anything else: segment ignored, nothing counted.
7. Walk the remaining tokens:
   - Stop at a token that is `--` for `npx`, or after the first package for `npx` (the rest are
     the command's arguments, never read).
   - Option tokens (start with `-`): flags are skipped. `--opt=value` is skipped whole. Options in
     the value-taking set below also consume the next token, which is never stored.
     - pip / uv pip: `-r --requirement -c --constraint` (count `ci_install_requirements_file`
       once per occurrence), `-e --editable` (count `ci_install_local_or_url`), and
       `-i --index-url --extra-index-url -f --find-links -t --target --prefix --root --src
       --trusted-host --platform --python-version --implementation --abi --cache-dir --log
       --proxy --retries --timeout --cert --client-cert --exists-action --no-binary
       --only-binary --upgrade-strategy --progress-bar -C --config-settings --python -p`
       (consumed silently).
     - pipx: `--python --pip-args --suffix --index-url --spec`; uv tool: `--python -p --with
       --with-requirements --index-url --extra-index-url --from` (consumed silently; `--with`
       packages are not stored: out of scope).
     - npm/npx: `--registry --prefix --tag -w --workspace --cache --userconfig`; for `npx`,
       `-p`/`--package` value is the package (classified as below) and `-c`/`--call` stops the walk.
   - Positional tokens are classified (first matching rule wins):
     1. contains `${{`, `$`, `%` or a backtick: `ci_install_dynamic`.
     2. contains `://`, starts with `.`, `/`, `~`, `\`, contains `\`, or ends with `.whl`,
        `.tar.gz`, `.zip`, `.tgz`; for npm also starts with `file:`, `link:`, `git`, `github:`,
        `workspace:`, `http`; for python also contains `/` or `@`: `ci_install_local_or_url`.
     3. python: must fullmatch `cli.SPEC` (name, optional `[extras]`, optional specifier);
        stored as `('python', cli.normalize(name), spec.strip() or '', scope)`. A `;` marker,
        glob, or non-match is `ci_install_unsupported`.
     4. npm: split on the last `@` that is not at index 0; name must fullmatch
        `@?[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)?`, spec (may be empty) must fullmatch
        `[A-Za-z0-9.*^~<>=|+-]{0,80}`; stored as `('npm', name.lower(), spec, scope)`.
        Else `ci_install_unsupported`.
     5. name longer than `MAX_NAME` or spec longer than `MAX_SPEC`: `ci_install_unsupported`.
   - `npm i` / `npm install` / `pip install` with no positional package (e.g. `npm install` from
     package.json, `pip install -r x`) stores nothing beyond the counts above.
8. After `MAX_CI_INSTALLS` stored rows in a file, further matches are counted
   `ci_install_over_limit` and not stored.

To avoid an import cycle (`cli` imports this module), either move `SPEC`/`normalize` into
`ci_installs.py` and have `cli` import them from there, or pass them in; the builder chooses, but
there must be one definition of each (no copied regex).

### 3.4 Coverage reasons (counts only)

`ci_install_dynamic`, `ci_install_requirements_file`, `ci_install_local_or_url`,
`ci_install_unsupported`, `ci_install_over_limit`. They flow into
`coverage['omitted_declarations']` as `{'path', 'reason', 'count'}` through the existing loop in
`cli.scan` (cli.py:230). Existing reasons (`dynamic_action_reference`, `unsupported_expression`)
are not reused, so existing counts are unchanged. Duplicate packages in one file are handled by
the existing `duplicate_declaration` logic in `cli.scan` (cli.py:232-237).

### 3.5 Wiring in `org_context/cli.py`

In `parse_with_notes` (cli.py:124-165) add two branches:

- `name.startswith('.github/workflows/') and name.endswith(('.yml', '.yaml'))`:
  `result.extend(ci_install_declarations(text, 'github', 'ci-install:' + name, notes))`
- root `azure-pipelines*.yml|yaml` (same test as cli.py:216, `'/' not in name`):
  `flavor='azure'`.

`scan` then attaches rows to the workflow's evidence id with no further change (cli.py:229). The
GitHub evidence `content` JSON (`declared_action_references`) and Azure `workflow_references_by_path`
are unchanged. Add `'CI script execution and indirect installs'` to `UNSUPPORTED` (cli.py:18) so
coverage states the remaining gap. No DB schema change; `dependency_schema_version` stays 1;
existing inventories need a rescan to gain `ci-install:` rows (document this).

### 3.6 Query side

No change. `discovery.call('find_projects_using')` (discovery.py:42-53) already matches
`python` names normalized and `npm` names lowercased and returns `scope` and `specification`.
`shared_dependencies` will now also group `ci-install:` rows; that is intended (each declaration
carries its scope). The result `semantics` stays `declared_dependency; not runtime usage`.

### 3.7 Failure modes

- Malformed or odd YAML/shell: never raises; unmatched text is ignored or counted. The function
  must not raise on any `str` input (no `SourceFormatError` from this pass).
- Secrets in commands (`--index-url https://user:TOKEN@host`, `$TOKEN`, `${{ secrets.X }}`):
  values of value-taking options are consumed and dropped; URLs and variables are counted, not
  stored. No raw text reaches `dependencies`, `scan_coverage` or evidence content.
- Huge files: existing `MAX_BYTES` (256000) per file and 2,000,000 total still apply before
  parsing; per-line and per-file caps above bound the work and stored rows.
- Unknown value-taking options (not in the lists) may cause their value to be read as a package
  if it matches the name regex; accepted as a lexical limitation and documented.

## 4. Acceptance criteria

Each is a `unittest` case in a new `tests/test_ci_installs.py` (using `cli.connect`, `cli.scan`,
`discovery.call`/`Context` like `tests/test_followup_hardening.py`), unless stated.

1. A fixture `.github/workflows/ci.yml` with steps `run: pip install pytest` and
   `run: npm i -g pkg@1.2.3`: after `cli.scan`, `find_projects_using({"technology":"pytest"})`
   returns one row for the project with `ecosystem='python'`, `scope='ci-install:.github/workflows/ci.yml'`,
   `specification=''`, and `path='.github/workflows/ci.yml'`; `("pkg")` returns `ecosystem='npm'`,
   same scope, `specification='1.2.3'`.
2. Specifier forms are kept: `python -m pip install "pytest==8.3"` gives `==8.3`;
   `npm install -g @anthropic-ai/claude-code@2.1.312` is found by technology
   `@anthropic-ai/claude-code` with spec `2.1.312`; `pip install Foo_Bar[extra]>=1` is stored as
   name `foo-bar`, spec `>=1`; `npx -y cowsay@1.5 hello` stores only `cowsay` `1.5` (not `hello`).
3. Each of `pipx install black`, `uv pip install ruff`, `uv tool install httpie`, `pip3 install x`,
   `python3 -m pip install y` stores a `python` row with the `ci-install:` scope.
4. `run: pip install ${{ matrix.pkg }}`, `pip install -r req.txt` and `pip install ./local`: no
   `dependencies` row for the file, and `omitted_declarations` for the path contains
   `ci_install_dynamic: 1`, `ci_install_requirements_file: 1`, `ci_install_local_or_url: 1`. Also
   `$PKG`, `https://...` URLs and `git+https://...` count, not store.
5. A multi-line `run: |` block with three lines (`echo hi`, `pip install a \`, `  b==2`) stores
   `a` and `b` (`==2`), reading the block line by line; a step body line `run: pip install z`
   inside another block is read as shell (stores `z`), and lines after the block that are at or
   below the key indent are not read as shell.
6. Nothing is executed: the scan of a fixture containing `run: pip install evil; touch MARKER`
   (with `subprocess` untouched by the new module) creates no `MARKER` file; `ci_installs.py`
   imports no `subprocess`/`os.system` (checked by a grep in the test or review).
7. Azure: root `azure-pipelines.yml` with `- script: pip install pytest`, `- bash: |` block with
   `npm i -g pkg@2`, and `- pwsh: python -m pip install tox` stores the three with scope
   `ci-install:azure-pipelines.yml`; `pipeline_metadata` output for the same file is identical to
   before (references and `omitted_or_over_limit`), and `defaults:`-style `run:` with an empty value
   stores nothing.
8. Secret hygiene: a workflow with `pip install --index-url https://u:SENTINEL1@h/simple pkg`,
   `pip install $SENTINEL2` and `pip install ${{ secrets.SENTINEL3 }}`: `pkg` is stored and none of
   `SENTINEL1..3` appears in a dump of `dependencies`, `evidence` and `scan_coverage` rows, nor in
   `project_card` / `find_projects_using` output.
9. Limits: a file with 60 distinct `pip install pN` lines stores 50 rows and counts
   `ci_install_over_limit: 10`; a 5000-char line containing `pip install` stores nothing and counts
   `ci_install_unsupported: 1`; repeated `pip install pytest` in one file is stored once and
   counted as `duplicate_declaration`.
10. Existing `uses:` behaviour unchanged: the existing suite passes unmodified
    (`python -m unittest discover -s tests -v`), in particular
    `tests/test_followup_hardening.py::WorkflowReferenceTests`, `tests/test_pilot_coverage.py`, and
    `tests/test_review_round3_regressions.py`/`round4` pipeline tests; GitHub workflow evidence
    `content` still contains exactly `declared_action_references` and `semantics`.
11. Docs: `README.md` "Extraction and coverage", `docs/WORKFLOW_EXTRACTION.md` and
    `docs/MCP_DEMO.md` describe the `ci-install:<path>` scope, the recognized commands, the counted
    reasons, that nothing is executed, and that old inventories need a rescan; `README.md` no longer
    says scripts are entirely unread.

## 5. Open questions

- Command variants beyond the issue's list: the spec adds `pip3` and `python3 -m pip` as obvious
  aliases of the listed commands; `py -m pip`, `uvx`, `npm add`, `yarn`, `pnpm` stay out. Assumed
  acceptable; trivial to drop.
- Should `shared_dependencies` exclude `ci-install:` scopes? Assumed no (scopes are visible per
  declaration). A reviewer may ask for a filter later; not in this scope.

## Code map

Files changed or created:

- `org_context/ci_installs.py` (new): the bounded lexical pass; must exist to keep the logic
  testable and out of the already dense `cli.py`.
- `org_context/cli.py`: must change to call the pass from `parse_with_notes` and to extend
  `UNSUPPORTED`; possibly to import `SPEC`/`normalize` from the new module.
- `tests/test_ci_installs.py` (new): the acceptance tests.
- `README.md`, `docs/WORKFLOW_EXTRACTION.md`, `docs/MCP_DEMO.md`: must change because they state
  workflow extraction is `uses:`-only.

Relevant code (checked):

- `org_context/cli.py:15-21` `ALLOWED`, `PIPELINE_PATTERNS`, `PATTERNS` (workflow globs),
  `UNSUPPORTED`, `MAX_BYTES=256000`, `SPEC` regex (PEP 508-ish name/extras/specifier).
- `org_context/cli.py:34-37` schema: `dependencies(project_id, ecosystem, name, specification,
  scope, evidence_id, UNIQUE(project_id,ecosystem,name,scope,evidence_id,specification))`.
- `org_context/cli.py:65-75` `workflow_actions(text, notes)`: the `uses:` extractor; do not change.
- `org_context/cli.py:85-86` `normalize(name)` (PEP 503 style, lowercase).
- `org_context/cli.py:102-111` `spec_item(raw, scope, notes)`: requirements line classification;
  model for the python rules (it counts `directive`, `direct_reference`, `environment_marker`,
  `unsupported_expression`; the new pass uses its own `ci_install_*` reasons).
- `org_context/cli.py:124-165` `parse_with_notes(name, text) -> (list[(eco,name,spec,scope)],
  Counter, list[dict])`: add the two workflow branches here. `parse()` at 167-168 wraps it.
- `org_context/cli.py:177-251` `scan(db, supplied)`: 201 calls `parse_with_notes`; 212-218 workflow
  evidence branches (GitHub content JSON, Azure `pipeline_metadata`); 229 attaches deps to the
  evidence id; 230 turns notes into `omitted_declarations`; 232-237 duplicate counting/dedup;
  238 coverage payload.
- `org_context/workflow_refs.py:4-15` constants incl. `_BLOCK_SCALAR`; `26-48` `_scalar(value)`
  (trailing comment / quote handling, reusable); `66-158` `pipeline_metadata(text)`: skips block
  scalar bodies (86-93); must stay unchanged.
- `org_context/discovery.py:42-53` `find_projects_using`: SQL over `dependencies` joined to
  `projects`/`evidence`, python name normalized, npm lowercased, returns `scope`, `specification`,
  `path`. `discovery.py:59-77` `shared_dependencies`: groups across all scopes. No change.
- `org_context/inventory_context.py:68-91` project_card coverage/workflow_declarations: no change.
- `tests/test_followup_hardening.py:16-58` `WORKFLOW` fixture and `WorkflowReferenceTests` (exact
  `omitted` dict expectations for uses:); `36-42` `FollowupCase` fixture helpers to copy.
- `tests/test_pilot_coverage.py:26-64`, `tests/test_review_round3_regressions.py:118-149`,
  `tests/test_review_round4_regressions.py:~277`: Azure fixtures with `- script:` bodies; they
  assert on `pipeline_metadata`/card output and SENTINEL absence, so they must keep passing (the
  script bodies there contain no install commands, so no new rows).

Copies of the touched logic:

- `cli.workflow_actions` (uses:) - out of scope, unchanged.
- `workflow_refs.pipeline_metadata` (Azure refs, block-scalar skipping) - out of scope, unchanged.
- `cli.SPEC` / `cli.normalize` and the npm name/spec regexes in `cli.parse_with_notes:161` -
  in scope for reuse only: the new module must use the same definitions (import or move), not a
  copy. If the npm regexes are factored into shared constants, `package.json` behaviour must stay
  the same.
- `discovery.py:49` repeats the python normalization inline (`re.sub(r'[-_.]+','-',...)`) - out of
  scope, unchanged; it matches `normalize`.
- No bundled/generated copies of the scanner exist (`skills/org-context-discovery/SKILL.md` is
  prose only).

Test commands:

- `python -m unittest discover -s tests -v` (full suite; the CI command in `.github/workflows/tests.yml:25`)
- `python -m unittest tests.test_ci_installs -v`
- `python -m unittest tests.test_followup_hardening tests.test_pilot_coverage tests.test_review_round3_regressions tests.test_review_round4_regressions -v`
