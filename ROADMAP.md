# Roadmap

## Outcome and status

Target: organizational context for reference-based development, new integrations
and evidence-backed investigation/repair. See [Product design](docs/PRODUCT_DESIGN.md).

Current: local explicit-root inventory with SQLite evidence, bounded Python/npm
declaration extraction with scan coverage, and a read-only stdio MCP: five tools by
default and six with --evidence-pages. Discovery and comparison operate over scan
roots, not verified repositories. README references are separated from structured
declarations. Test presence is not a verified live deployment.
Everything below is planned unless explicitly marked current.

Current pilot priority: run the [paired multi-repo evaluation](docs/PILOT_EVAL.md)
(see [governance pilot findings](docs/GOVERNANCE_PILOT_FINDINGS.md))
and improve local coverage honesty. Do not expand to cloud connectors,
Governance adapters, gates, or automated repair on retrieval results alone.

## Phase 0 - Establish a trustworthy baseline

- Run tests against the exact repository commit; record results and fix blockers.
- Preserve snapshot safety and report skipped/unsupported sources.
- Choose a license through an owner decision; do not assume one.
- Define data-handling rules before scanning real organizational repositories.

Exit: reproducible local baseline, known limitations and approved pilot scope.

## Phase 1 - Define the pilot and evaluation first

Use a small, authorized slice around ClaudeMetrics: repository, pipeline,
ingestion contracts, infrastructure, KQL and checks. Add agent-platform and
notification references only when available and authorized. Do not scan the
entire organization or claim live resource knowledge from repository files.

- Define 5-10 competency questions and reviewed expected answers.
- Establish replay fixtures covering all three product scenarios.
- Include missing mappings, stale/conflicting sources, same-name entities,
  failed evidence tools and cases where abstention is correct.
- Compare the same agent/model/tasks/budgets with existing tools alone versus
  existing tools plus org-context.
- Measure supported correctness, relevant reuse, missed prerequisites, human
  corrections, test selection, time, tokens and cost.

Exit: repeatable benchmark and a documented adoption decision. Numerical success
thresholds require owner approval. Evaluation continues through later phases.

## Phase 2 - Versioned context contract and minimal read-only MCP

- Define canonical project/system identities beyond local paths.
- Model source claims with provenance, commit/version, observation time,
  extraction method and verification status.
- Return coverage, unknowns, conflicts and freshness (including unknown).
- Distinguish repository host owner, CODEOWNERS rules and service ownership.
- Record visibility only when known; metadata is not access enforcement.
- Expose bounded local read-only tools for project cards, dependency discovery
  and supported relationships. Do not expose arbitrary shell/SQL/path access.
- Produce task-specific bundles before considering a dedicated graph database.

Exit: an agent can consume supported context with evidence and explicit gaps.
No cloud writes, deployment or autonomous repair.

## Phase 3 - System map and reviewed implementation blueprint

- Add selected deterministic sources: CODEOWNERS, existing catalog descriptors,
  CI YAML, OpenAPI/proto; then targeted Docker/IaC/ADRs as evaluation requires.
- Do not equate a declared API with deployed availability, or a workflow file
  with a successful run. Consumer inference must carry evidence and limits.
- Create one reviewed ClaudeMetrics observability blueprint linking code,
  contracts, privacy requirements, identities, deployment and checks.
- Classify components: reuse, adapt, create, verify, approval required.
- Produce an OpenRouter adaptation plan, not automatic infrastructure creation.
- Produce a DevOps monitoring-agent integration plan using known environment and
  notification contracts, explicitly reporting absent sources.
- Evaluate SBOM import/adapters instead of expanding every dependency parser;
  Syft is a candidate producer and CycloneDX a candidate interchange format.

Exit: source-backed plans for both development scenarios outperform the baseline
under agreed criteria; manual curation is acceptable and recorded.

## Phase 4 - Authorized operational context and investigation loop

- Add read-only connectors for approved DevOps run/log metadata and Azure
  resource/deployment/log evidence, with independent authorization.
- Investigate exact run/commit/environment; separate desired, deployed and
  observed state. Bind approved checks to components.
- Structure hypotheses, evidence, proposed fixes and unknowns.
- Replay an overnight failure before live shadow investigation.
- Integrate with an external harness for staged approvals, isolated edits, CI,
  draft PRs and deployment-linked PASS/FAIL/INCONCLUSIVE verification.
- Track finding occurrences separately from problem fingerprints; prevent
  duplicate actions without suppressing recurrence.
- Close fixes only with required verification evidence, not on PR merge alone.

Exit: one auditable investigation-to-verification slice without blocking ingestion.
Writes require a separate design and explicit authorization; not granted here.

## Deferred until evaluation justifies them

- Serena for symbol-level work; Graphify/Graphicle for code-map enrichment.
- Graphiti or other temporal investigation-memory frameworks.
- Dedicated graph DB, graph diffs, automatic ontology generation and rich UI.
- Incremental organization-wide discovery, extensive parser coverage and scale.

Before multi-user use: source permission propagation, query/traversal and cache
authorization, deletion/retention, tenant isolation, audit and secret handling.
Security/correctness prerequisites are never deferred merely to ship context.

## Invariants

No secret values in context; no instructions trusted merely because they appear
in scanned text; no inferred causation from graph paths; no implicit approval to
create identities, change permissions, rerun pipelines or deploy. Graph/agent/Git
failures must never interrupt compliance ingestion. No zero-iteration guarantee.
