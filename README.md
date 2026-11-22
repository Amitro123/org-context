# org-context

Evidence-backed organizational context for coding and investigation agents.

## Vision

Help agents understand what already exists, where it runs, how to integrate with
it, and how to verify a change. Connect repositories, services, APIs, pipelines,
infrastructure, organizational patterns and operational evidence into a
versioned system map. Deliver task-specific context rather than the entire graph.

The graph supplies the map; authorized tools retrieve current evidence; an
external agent harness controls execution, approvals and budgets; tests verify
outcomes. org-context is not a replacement for Cursor, Claude, Foundry, CI or
cloud authorization. Fewer iterations are a hypothesis to evaluate, not a promise.

## Three target scenarios (planned)

1. Reference-based development: adapt the observability pattern in ClaudeMetrics
   to a new OpenRouter Metrics project. Identify reusable components, necessary
   adaptations, prerequisites, approvals and verification. Do not blindly copy
   schemas, resource identifiers, identities or permissions.
2. New organizational features: plan an Azure DevOps failure-monitoring agent
   using the correct environment, agent deployment path and an existing
   notification service if one is available and suitable. Return contracts,
   examples, dependencies and unresolved decisions.
3. Investigation and repair: investigate a failed overnight pipeline using the
   exact run, commit and environment; locate relevant code, logs and checks;
   test hypotheses; propose a fix and verify it after an approved deployment.

These are design scenarios, not claims that the required integrations exist.
See [Product design](docs/PRODUCT_DESIGN.md) and [Roadmap](ROADMAP.md).

## Current implementation

Experimental local, single-user dependency inventory. Not yet an organizational
context service. It scans explicit roots, extracts supported Python/npm
dependency declarations, retains README reference text, stores SQLite snapshots
and exports a JSON graph with source evidence.

No MCP server, cross-system discovery, live Azure/DevOps access, blueprint
generation, agent orchestration or repair automation is implemented.

## Quick start

Python 3.11+. No third-party runtime dependencies. From the checkout:

```sh
python -m org_context.cli scan /path/to/repo-a /path/to/repo-b
python -m org_context.cli list
python -m org_context.cli show repo-a
python -m org_context.cli search --technology fastapi --ecosystem python
python -m org_context.cli export > graph.json
python -m unittest discover -s tests -v
```

Pass `--db /path/to/index.sqlite` before the subcommand to select storage.
For the `org-context` command, install in a virtual environment with
`python -m pip install -e .`. Build tooling installation may require network
access; scanning itself makes no network requests.

## Current extraction scope

Only exact allowlisted root files are read: README, README.md, README.rst,
README.txt, requirements.txt, pyproject.toml and package.json. Scanning is
non-recursive; supply nested monorepo packages separately. Standard PEP 621
dependencies, simple requirements and four npm dependency scopes are supported.
Lockfiles, Poetry-specific tables, requirement includes, environment markers and
other unsupported expressions are not currently extracted. Direct URL/path
dependencies are excluded. This is not a completeness guarantee.

A dependency declaration is not proof of runtime usage or organizational approval.
README text is unverified reference material, not accepted facts. Manifest bodies
are not retained. Evidence includes path, content hash and extraction method;
projects include scan time and Git HEAD if available. HEAD does not describe
uncommitted changes or prove deployment; scan time does not prove source freshness.

Successful root scans atomically replace their snapshots. Parse errors retain
the old snapshot; earlier roots in a multi-root invocation may already have
committed. Oversized/symlinked files are skipped and reported. Project identity
uses absolute local path; use full IDs for ambiguous names.

## Safety and limitations

- No LLM, MCP, Serena, remote crawling, cloud ingestion or deployment is included.
- No scanned application code, build scripts or hooks are executed. A fixed local
  `git rev-parse` command reads commit metadata only.
- Root symlinks are rejected; leaf symlinks and files above 256 KB are skipped.
- This is not a secret scanner. READMEs may contain secrets, PII or malicious
  instructions. Treat content as untrusted data and protect DBs/exports.
- Path checks are not a sandbox against concurrent malicious file changes.
  Scan trusted, stable checkouts only.
- No source ACL propagation, encryption, history, tenant isolation or multi-user
  authorization. Local paths are not cross-machine identities.
- No guarantee of freshness, completeness or production readiness.
- Neither graph updates nor agent/GitHub failures may block compliance ingestion.

Owner must select a license before external reuse. No license grant is included.
