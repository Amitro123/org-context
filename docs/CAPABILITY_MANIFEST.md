# Optional capability manifest and read-only discovery

Status: bounded MVP for task-oriented capability and responsibility discovery.
`org-context` remains a local, read-only discovery tool — it never validates
ground truth, runs Security/Eval gates, resolves symbols statically, or grants
approval/publication authority.

## 1. Section 5 Manual Discovery Pilot (2026-11-25)

Before implementing the manifest scanner, a read-only three-question pilot was
run on the approved local governance monorepo (`Security-review-skills`, branch
`feat/trusted-eval-execution`, HEAD `c8e024e0a5b080e8701bc7d2f4499dc879679c3b`,
dirty working tree; zero files modified in that repository).

### Baseline vs explicit capability map

Two scan roots were scanned with the existing `org-context` CLI:
- `Security-review-skills` (`git_root_relation: repository_root`)
- `Security-review-skills/modules/evaluation` (`git_root_relation: subdirectory`, `git_relative_prefix: modules/evaluation`)

| Question | Existing `org-context` baseline | Reviewed local capability map |
| --- | --- | --- |
| Q1: Where is ground-truth validation implemented? | `0` relevant source paths (only root manifests/workflows/READMEs indexed) | `modules/evaluation/core/ground_truth.py` (`classify_ground_truth`, `independent_review`, `plausible_repair_rejected`) + submission gate `packages/application/skill_review/application/trusted_eval.py` (`accepted_ground_truth`) |
| Q2: Which component determines whether Eval evidence can enter an approval process? | `0` relevant source paths | Outer governance decision: `packages/domain/skill_review/domain/enterprise_decision.py` (`decide_enterprise_review`, `_binding_gap`), orchestrated by `enterprise_review.py` & `trusted_eval.py`; inner readiness contract: `modules/evaluation/core/enterprise_readiness.py` (`assess_enterprise_readiness`). Neither grants human `APPROVED`/published status. |
| Q3: Which source files should an agent read before changing those behaviors? | `0/6` source files discoverable via inventory | All `6` verified source files + `docs/enterprise-skill-review-orchestration.md` returned directly |

**Pilot conclusion:** The baseline could not answer any of the three
responsibility questions (`0/6` entry points), whereas a small explicit manifest
mapped all `6` locally verified entry points with explicit boundaries and zero
runtime/approval claims. This justified implementing the bounded optional
manifest MVP below. (This single-repo pilot justifies the MVP; it does not
establish general agent productivity gains.)

## 2. Optional manifest contract (`org-context.manifest.json`)

Place an optional `org-context.manifest.json` at the scan root. Repositories
without this file retain their exact existing behavior.

Synthetic example (placeholders only):

```json
{
  "schema_version": 1,
  "component": "example-evaluation-component",
  "role": "evaluation",
  "capabilities": [
    {
      "id": "ground-truth-validation",
      "summary": "Locate the implementation that checks evaluation ground truth",
      "responsibility": "Validate ground-truth inputs used by evaluation",
      "entrypoints": [
        {
          "path": "src/example/ground_truth.py",
          "symbol": "validate_ground_truth"
        }
      ],
      "references": ["docs/example-evaluation-contract.md"],
      "out_of_scope": ["Authorize publication", "Prove deployed runtime state"]
    }
  ]
}
```

### Validation bounds and safety

- `schema_version`: must be integer `1`.
- `component` (`1..128` chars), `role` (`1..64` chars), `capabilities` (`1..32` items, unique lowercase slug `id`).
- Per capability: `summary` (`1..240`), `responsibility` (`1..400`), `entrypoints` (`1..20`), `references` (`0..20`), `out_of_scope` (`0..10`, each `1..200`).
- All `path` values must be relative POSIX paths inside the scan root; absolute paths, `..`, backslashes, and symlinks escaping the root are rejected.
- Any invalid manifest fails that root scan cleanly with a content-free error and preserves the previous snapshot. A manifest over 256 KB is invalid in the same way (it is never skipped, so it cannot silently replace the stored capabilities with nothing). Removing the manifest file clears the root's capabilities.
- Paths keep their exact identity: a path with leading or trailing whitespace is rejected, never trimmed to a different file. Display text (`summary`, `responsibility`) is still trimmed.
- Referenced local files are checked strictly within the scan root for existence and optional SHA256 (when `<= 256 KB`), producing `observed_local_file`, `observed_local_file_over_size_limit`, `missing_file`, or `not_a_file`. Referenced file bodies are **never** stored or returned. Each referenced file is read at most once per scan, shared with the main scanner (a file the main scan already read, or reads later, is not opened or charged again), and the bytes read count once against the same 2 MB scan aggregate as the main scan; exceeding it fails the root scan and preserves the previous snapshot.
- Declared `symbol` values always report `symbol_status: "declared_only_not_resolved"`.

### Second source: Claude Code skill and agent frontmatter

Repositories that already describe their skills do not need a manifest. During `scan`, these
files (and only these) under the scan root are read:

| Pattern | Role | Component |
|---|---|---|
| `skills/*/SKILL.md` | `skill` | scan root name |
| `plugins/*/skills/*/SKILL.md` | `skill` | the `<plugin>` directory name |
| `agents/*.md` | `agent` | scan root name |

Only the YAML frontmatter block between the first two `---` lines is parsed (a bounded subset of
YAML, no dependency), and only three keys are kept: `name` (becomes `capability_id`, must match the
lowercase slug syntax `^[a-z0-9][a-z0-9._-]{0,63}$`), `description` (becomes `summary`, required,
`<= 1024` chars) and `when_to_use` (becomes `responsibility`, optional, `<= 1024` chars). Other keys
(`allowed-tools`, `model`, `metadata`, ...) are ignored. Values are whitespace-normalized to one line;
control characters and bidi controls are rejected, never truncated. Plain, single-quoted,
double-quoted and `|` / `>` block scalars are accepted; flow collections, anchors, tags and
indentation digits are not.

The skill body (everything after the closing `---`), its scripts and references are never decoded,
stored, hashed or returned. Each row's evidence has kind `skill_frontmatter`, method
`bounded_frontmatter_parser`, `content` NULL, and `sha256` over the bytes from the start of the file
through the closing delimiter line. The single entry point is the file itself with
`path_status: "observed_local_file_frontmatter_only"` and `file_sha256: null`.

Limits (per scan root, separate from the main scan's file and byte limits, so they never fail it):

- at most `16384` bytes read per file (one extra byte to detect overflow) and `200` frontmatter lines;
- at most `256` candidate paths collected (`truncated: true` beyond that) and `64` files read.

A file that cannot be used is skipped, never failing the root scan. Skips are recorded in
`scan_coverage.skill_frontmatter` (`candidates_found`, `indexed`, `skipped` listing at most 64
`{path, reason}` entries, `skipped_count`, `skipped_by_reason`, `truncated`, `limits`). Skip reasons:
`invalid_path`, `symlink` (any symlink on the path; the target is not opened), `not_a_file`,
`read_error`, `no_frontmatter`, `malformed_frontmatter`, `frontmatter_too_large`, `missing_name`,
`invalid_name`, `missing_description`, `invalid_field`, `duplicate_capability_id` (a manifest
capability with that id wins; otherwise the lexically first path wins), `file_limit`.

An invalid `org-context.manifest.json` still fails the whole root scan, skills included.

## 3. MCP tool: `find_capabilities`

`find_capabilities(query, role?, project_id?, offset?, limit?)`:
- Deterministic case-insensitive token matching over `capability_id`, `component`, `role`, `summary`, `responsibility`, and entrypoint/reference paths and symbols.
- Executes within one SQLite read snapshot (`SAVEPOINT`), ordered deterministically by `project_id, capability_id` (`limit` `1..20`, default `20`).
- Returns `manifest_evidence` (manifest path and SHA256, distinct from referenced `file_sha256`), `git_root_relation` / `git_relative_prefix` (preserving monorepo subdirectory identity), `snapshot`, `out_of_scope`, and explicit no-match coverage limits.
- Each result has `declaration_source`: `capability_manifest` or `declared_skill_frontmatter`. Skill rows keep the same shape (`manifest_evidence` points at the SKILL.md path with kind `skill_frontmatter`) and carry `declaration_semantics` stating that the labels are the skill author's untrusted data and that the body was not read. Filter with `role: "skill"` or `role: "agent"`.
- Legacy SQLite databases without the `capabilities` table still open and return `results: []` with `unknowns: ["Legacy inventory has no capabilities table; rescan required"]`.
