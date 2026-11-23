# Local pilot: paired agent evaluation

Status: case proposals derived from the Skill Governance pilot, not yet
independently reviewed or run as an A/B experiment. Historical observations
are leads, not an answer key. Keep org-context experimental and standalone.
It supplies discovery context, not security, evaluation, approval, ownership,
or deployment authority. See [governance pilot findings](GOVERNANCE_PILOT_FINDINGS.md) for the recorded pre-experiment observations and external architecture context.

## Competency cases

Freeze the approved scan roots and exact local source revisions, including any
dirty state. Before running, have a reviewer check the answer key against source
files and record required assets, source pointers, and required abstentions.
Keep the real key, paths, and answers local; do not commit pilot source excerpts.

| ID | Prompt for both arms | Reviewer checks |
| --- | --- | --- |
| R1 | Which approved repos declare `pydantic`? Group duplicate declarations by repository and cite evidence. | Required repos and declared scopes; root count is not repository count. |
| R2 | Before adding an OpenAI SDK dependency, find an existing declaration to reuse. Is it safe to say no other repo uses it? | Required declaration, file and scope; no absence or runtime claim beyond coverage. |
| R3 | What common test or YAML tooling is already declared across the pilot repos? | Review `pytest` and `pyyaml` evidence; distinguish test groups from runtime. |
| C1 | Find the CI declaration that references shared pipeline templates and identify the repository alias if visible. Is the template deployed? | Cite the actual local template or repository declaration; abstain on deployment and do not infer contents of a remote template. |
| C2 | Find the Eval source pin. Does it establish which revision executed in production? | Cite the pin from its authoritative local file if available; distinguish a recorded pin, checked-out HEAD, and observed execution. |
| U1 | Did the latest verification job pass? | Abstain without run evidence; a workflow or Git SHA is insufficient. |
| U2 | Can we rule out a package in a lockfile, nested workspace, or included requirements file based on inventory results? | Identify the relevant unsupported parser or scan-root boundary; no unsupported absence claim. |
| U3 | Who can approve the governance change, and can discovery authorize it? | Consult independently supplied policy if in scope; otherwise abstain. No inference of approval authority from inventory. |

Note on the external **physical monorepo** direction (Security + Eval in one repo;
see [governance pilot findings](GOVERNANCE_PILOT_FINDINGS.md)): when multiple
approved scan roots come from one monorepo, check `coverage.git_root_relation`
and `coverage.git_relative_prefix` on `project_card` so subdirectory scan roots
are not conflated with separate repositories (`R1`). For `C2`, freeze either the
pre-consolidation split-repo baseline or a reviewed monorepo answer key (for
example `eval_identity` / `IMPORT_PROVENANCE` instead of the old pin-only key)
before running; do not change the expected answer key after seeing results.

## Run protocol

1. Use separate fresh sessions for each case and arm. Freeze model, settings,
   task wording, source revisions, approved roots, and ordinary read-only local
   tools. Arm A has those tools only; arm B adds metadata-only org-context MCP
   over the same roots. Neither arm has network or mutation tools.
2. Pre-register identical limits, for example six tool calls (including MCP
   calls), five minutes, and a 600-word final answer per case and arm. Apply
   limits equally. If the client cannot measure a budget, record it as unknown
   instead of claiming it was enforced. Alternate arm order per case; do not
   share the other arm's answer, retrievals, or reviewed answer key.
3. Score anonymized answers without knowing the arm where practical. Count each
   reviewed relevant asset missed, each unsupported confident material claim,
   and each expected abstention met. Check cited files independently. A timeout
   or unavailable tool remains visible; do not drop the pair. Also record tool
   calls and elapsed time, plus tokens/cost only if measured.
4. For this pilot, the directional success bar is fewer missed assets in B
   across the same cases without more unsupported confident claims. Report
   per-case counts and abstentions, not only totals. If that bar is missed, or
   the experiment is incomplete, stay experimental and do not add a
   Governance-specific adapter. Even a pass is not a productivity claim or
   authorization to add a production dependency.

Copy one line per case into a local scorecard:

| Case | A/B assets missed | A/B unsupported confident claims | A/B expected abstention met | A/B tool calls and time | Reviewed evidence / unknowns |
| --- | --- | --- | --- | --- | --- |
| R1 | pending | pending | n/a | pending | pending |
| R2 | pending | pending | pending | pending | pending |
| R3 | pending | pending | n/a | pending | pending |
| C1 | pending | pending | pending | pending | pending |
| C2 | pending | pending | pending | pending | pending |
| U1 | n/a | pending | pending | pending | pending |
| U2 | n/a | pending | pending | pending | pending |
| U3 | n/a | pending | pending | pending | pending |

The [retrieval regression runner](RETRIEVAL_EVAL.md) is a separate correctness
check. Passing retrieval cases or unit tests does not complete this experiment.

## Optional follow-up: measure the skill separately

Keep the [discovery skill](../skills/org-context-discovery/SKILL.md) out of both
original arms. After the A/B run, compare B (MCP only) with C (same MCP plus
the skill), using the same reviewed cases, frozen inputs, budgets, and blind
scoring. Record the skill revision; do not add any tools or evidence access to C.
Report differences in missed assets, unsupported confident claims, abstentions,
and tool calls. Do not call fewer tool calls an improvement if correctness or
abstention worsens. No skill effectiveness is assumed before this follow-up.

## Stable identity before multi-machine use

Today the inventory key identifies a resolved local path. A move, new clone, or
different machine can create another ID, and separate roots can belong to one
repository. Keep the key and SQLite schema unchanged for this pilot.

Before multi-machine use, review a separate stable logical project ID, assigned
explicitly in an approved local manifest. Model checkout/root and snapshot as
separate identities. Keep existing path IDs as aliases during migration, and
require explicit mapping for ambiguous roots, forks, and nested components.
Do not silently merge on name, remote URL, or commit. Validate moves, forks,
same-name repos and multiple roots before changing existing tools or inventory.
