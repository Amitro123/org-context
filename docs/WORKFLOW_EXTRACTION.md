# Bounded CI workflow reference extraction

Status: current local-scanner behavior for conventional CI files. All extracted
references are **lexical declarations only** — never YAML validation, condition
evaluation, template expansion, execution status, deployment state, or approval.

## Scope and supported files

- **GitHub Actions:** `.github/workflows/*.yml` and `.github/workflows/*.yaml`
  - Extracts lexical `uses:` values into retained workflow summary content
    (withheld in default metadata-only MCP `project_card` unless `--evidence-pages`
    is explicitly enabled).
  - Dynamic `${{ ... }}` and unparsed values are counted in `scan_coverage`
    (`omitted_declarations`) without storing the raw text.
- **Azure Pipelines:** conventional root files `azure-pipelines.yml`,
  `azure-pipelines.yaml`, `azure-pipelines-*.yml`, and `azure-pipelines-*.yaml`
  - Extracts constrained `template:` and `resources.repositories` declarations
    into `scan_coverage` and surfaces up to `20` references per file on
    `project_card` under `evidence[].workflow_declarations` without exposing
    file bodies.
  - Nested pipeline files outside the scan root are not auto-discovered.

## CI install commands (`ci-install:<path>` dependencies)

`org_context/ci_installs.py` reads the shell text of GitHub Actions `run:` values
and Azure Pipelines `script:`, `bash:` and `pwsh:` values in the files above.
Nothing is executed, and shell or YAML is never evaluated (no variable
expansion, conditions or matrix).

- **Locating shell text:** only the command field of a real step counts:
  `jobs.<id>.steps[].run` for GitHub, and `steps[].script` / `bash` / `pwsh` below
  the known pipeline ancestors (`stages`, `jobs`, `strategy`, `deploy`, ...) for
  Azure. `env`, `with`, `inputs`, `parameters`, `variables`, `defaults` and any
  other structure are data and never read; unknown structures do not inherit a
  step context. An inline value is one shell line (one level of quotes removed;
  an unclosed quote is `ci_install_unsupported`); a plain multi-line value is
  joined with spaces. In a literal block (`|`) every line is its own shell line.
  In a folded block (`>`, `>-`, `>+`) lines are joined with a space, and blank and
  more-indented lines keep their breaks, as YAML defines. A `run:` line inside a
  block body is shell text, not a new step. Explicit indentation indicators
  (`>2`, `|2`, with chomping) are honored, so more-indented lines stay separate
  lines. In every field, including ones that are never read, multi-line quoted
  scalars and flow collections (`[...]`, `{...}`) are consumed whole and their
  text is opaque data: a lookalike `run:` line inside them is never structure. A
  multi-line quoted command scalar is `ci_install_unsupported`.
- **Shell lines:** comments dropped, `\` continuations joined (up to 20 lines),
  split on `&&`, `||`, `;`, `|`; leading `sudo` and `NAME=value` dropped.
- **Recognized commands:** `pip install`, `pip3 install`, `python -m pip install`,
  `python3 -m pip install`, `pipx install`, `uv pip install`, `uv tool install`
  (ecosystem `python`, names normalized as for requirements), `npm i`,
  `npm install` (with or without `-g`/`--global`) and `npx <pkg>` (ecosystem
  `npm`, names lowercased; for `npx` only the package, never the command's
  arguments). Others (`poetry`, `yarn`, `pnpm`, `uvx`, `npm ci`, `npm add`, ...)
  are ignored.
- **Stored:** each static package as a dependency row with its specifier
  (`==8.3`, `1.2.3`) and scope `ci-install:<workflow relative path>`, linked to
  the workflow's evidence. Repeats in one file count as `duplicate_declaration`.
- **Counted, never stored** (in `omitted_declarations`): `ci_install_dynamic`
  (`$VAR`, `${{ }}`, `%VAR%`, backticks), `ci_install_requirements_file` (`-r`,
  `-c`), `ci_install_local_or_url` (URLs, paths, archives, `-e`, `git+`,
  `file:`), `ci_install_unsupported` (unparsed arguments, unbalanced quotes,
  markers, names over 100 or specifiers over 80 characters, logical lines over
  4096 characters) and `ci_install_over_limit` (beyond 50 rows per file).
  Values of known value-taking options (`--index-url`, `--registry`, `--target`,
  ...) are consumed and dropped, so credentials in them never reach the inventory.
- **Gaps:** indirect installs (scripts, `make`, `tox`, `nox`, `pre-commit`,
  Dockerfiles, `-r` files) are not followed; heredocs, PowerShell backtick
  continuation and flow-style YAML are not handled; an unknown value-taking
  option's value can be read as a package name if it looks like one.
- **Rescan:** inventories scanned before this extraction need a rescan to gain
  `ci-install:` rows; the schema is unchanged.

## Structural context rules (Azure Pipelines)

To avoid classifying arbitrary YAML data as pipeline includes, `workflow_refs.py`
tracks indentation, sequence-item boundaries, and parent mapping keys:

- **Supported `template:` contexts:**
  - `extends.template` (mapping property under `extends`).
  - List-item `- template:` under `stages`, `jobs`, `steps`, `variables`, or
    deployment `strategy` hooks (`runOnce`, `rolling`, `canary` -> `preDeploy`,
    `deploy`, `routeTraffic`, `postRouteTraffic`, `on` -> `success`/`failure`).
- **Quoted keys:** single- and double-quoted identifier keys (e.g. `'parameters':`,
  `"parameters":`, `"steps":`) are treated equivalently to unquoted keys.
- **Conditional wrappers:** `${{ if ... }}:`, `${{ elseif ... }}:`,
  `${{ else }}:`, and `${{ each ... }}:` are tracked as transparent structural
  wrappers. Static `template:` declarations inside them are reported if and only
  if the enclosing parent context is supported. Conditions are **not** evaluated
  and do not prove runtime execution.
- **Excluded subtrees:**
  - `parameters` object data (plain or quoted) is never classified as a template
    include, even when nested keys are named `steps`, `jobs`, `stages`, or
    `variables`, or contain `${{ if ... }}` blocks.
  - Any other non-conditional, non-identifier mapping key opening a nested block
    (e.g. `"custom object":`, `custom.dotted:`) marks its subtree as unsupported
    so nested entries cannot inherit a trusted ancestor context.
  - Block scalar bodies (`|`, `>`, `|-`, `>+`, etc.) are skipped.
  - List-style `template:` entries in excluded contexts, dynamic `${{ ... }}`
    template targets, invalid paths, and references beyond the 20-item cap are
    counted in `omitted_or_over_limit` without storing or exposing their values.

## Known gaps and unsupported syntax

This extractor is intentionally bounded and does **not** implement a full YAML
parser or Azure Pipelines evaluator:

- **Flow-style YAML** (`{...}`, `[...]`), **multi-line keys**, **anchors/aliases**
  (`&anchor`, `*alias`, `<<:`), and complex quoted keys with escape sequences are
  not expanded; unknown mapping blocks are conservatively treated as unsupported.
- **No template expansion or remote fetch:** referenced local or `@alias`
  templates are not opened, resolved, or validated.
- **No runtime or deployment semantics:** a declared `template:` or `uses:`
  reference does not establish that a pipeline ran, passed, or deployed.
- **Pagination for >20 evidence records:** when a scan root has more than 20
  evidence files, use `project_card` with `evidence_offset` / `evidence_limit`
  or filter directly with `evidence_kind: "workflow"` / `evidence_path` to
  retrieve workflow declarations beyond the first page.
