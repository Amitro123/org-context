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
