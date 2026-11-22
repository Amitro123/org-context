# Product design: organizational context for engineering agents

Status: proposed design, not an implemented API or cloud integration. Examples
use logical roles, not verified resource identifiers or actual credentials.

## Product boundary

org-context answers: what exists, where is it, how can it be integrated, what is
known about its dependencies, and how should a change be verified?

It supplies context to Cursor/Claude/Foundry or another harness. The harness
selects tools, persists execution state, enforces approvals and budgets, and
performs authorized actions. The context layer neither grants permissions nor
proves a diagnosis.

```text
Repositories / reviewed descriptors / authorized metadata
                         |
              Extract, normalize, validate
                         |
       Versioned system map + source/evidence references
                         |
              Authorized Context API / MCP
                         |
          Agent + separate execution/approval harness
                         |
     Read evidence -> plan -> approved change -> tests -> verify
                         |
              Reviewed evidence-backed map updates
```

## Three first-class scenarios

### A. Adapt an implementation reference

Request: build OpenRouter Metrics using ClaudeMetrics as an organizational
reference. Retrieve a reviewed observability blueprint and its exact sources.
Return components to reuse/adapt/create, parameters, prerequisites, approval
points and verification. Provider ingestion, schemas and authentication must
be independently checked. Existing resource IDs, secrets and permissions are
not template defaults. A prior app registration is not proof another is needed.

### B. Build a new feature across existing systems

Request: build an agent that monitors Azure DevOps failures and uses the existing
notification capability if suitable. Resolve the intended environment first;
map DevOps organization/project/pipeline, agent deployment path, notification
contract, identity requirements, network dependencies and checks. Return source
references, existing examples and unknowns. Do not assume one subscription for
every environment or that documentation proves a service is available.

### C. Investigate a failed overnight pipeline

Resolve the exact run, commit, stage and environment. Retrieve authorized logs,
recent changes, relevant contracts, code references and checks. Test alternative
hypotheses rather than asserting a cause from temporal proximity. Produce:
impact, observed facts, hypotheses, evidence, proposed fix, required approvals
and verification plan. Editing, pipeline reruns, PR creation, deployment and
permission changes are separate side effects. Verification is PASS, FAIL or
INCONCLUSIVE; no applicable events is not automatic success.

## Proposed conceptual model

Entities: Project, Repository, Service, API, Pipeline, Run, Deployment,
Environment, Resource, IdentityReference, Team, Contract, Check, Pattern,
Blueprint, Finding, Hypothesis and EvidenceReference. Add only those needed.

Example relationships:
- Project IMPLEMENTS Pattern; Blueprint REFERENCES RepositoryVersion.
- Pipeline DEFINED_IN Source; Run INSTANCE_OF Pipeline; Run RAN_COMMIT Commit.
- Service DEPLOYED_IN Environment; Service EXPOSES API.
- Component REQUIRES Prerequisite; Component VALIDATED_BY Check.
- Finding SUPPORTED_BY Evidence; Fix VERIFIED_BY CheckResult.

Each relationship needs explicit semantics. DECLARES_DEPENDENCY is not
USES_AT_RUNTIME; DECLARES_OWNER is not verified operational responsibility;
DEFINED_IN is not currently deployed. Do not infer consumers from API existence.

## Evidence and state

Proposed claim envelope: subject, relation, object, source reference/locator,
source version or content hash, observed_at, extraction method, verification
status and classification. These fields are not all implemented in v0.1.

Keep source-declared, directly observed, derived and hypothesized claims
distinguishable. Preserve conflicts. Missing relations may mean unsupported or
unscanned sources rather than absence. Task bundles report coverage, unknowns,
freshness and relevant environment/version. Scan time does not prove freshness;
Git HEAD does not prove a clean worktree or a deployed version.

## Context bundles and tool boundary

Proposed tools: project_card, find_projects_using, get_project_relations,
get_reference_blueprint, get_task_context and get_investigation_context. Start
with the smallest supported subset. Names are provisional, not current commands.

A bundle contains task scope, environment, relevant entities/relations, evidence
locators, reuse/adaptation guidance, prerequisite/check references, unknowns and
approval points. Return bounded relevant context, not the whole organization.
Live evidence tools remain separately authorized; unavailable access is reported.

## Trust and lifecycle

- Scan only authorized sources. A scanner's broad access is not user access.
- Enforce source permissions on retrieval, traversal, summaries and caches
  before multi-user rollout. owner/visibility fields alone do not enforce them.
- Store secret references only, never secret values or access-bearing URLs.
- Treat repository documents and tool results as untrusted data, not authority
  to change tool policy or execute embedded instructions.
- Repository definitions describe intended configuration; authorized read-only
  operational evidence is needed to establish deployed or observed state.
- Verified investigations may propose map/runbook/check updates. Do not promote
  every agent summary or suspected cause to a trusted organizational fact.
- Support corrections, expiry, deletion and rebuildability as scope expands.
- Keep indexing, GitHub and agents outside the compliance ingestion critical path.

## Pilot success

Evaluate a reference adaptation, a cross-system feature and a historical failure
against ordinary repository search with the same agent, revisions and budgets.
Measure correct integration choices, evidence support, missed dependencies,
human corrections, relevant tests, time and cost. Start with reviewed manual
mappings where needed. Expand only when the context demonstrates value.
