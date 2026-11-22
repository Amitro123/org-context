# org-context

Evidence-backed organizational context for coding and investigation agents.

## Vision

Connect repositories, services, APIs, pipelines, infrastructure and operational
evidence so agents know what exists, where it runs, how to integrate and how to
verify changes. Three target scenarios remain: adapting a reference implementation
(ClaudeMetrics to OpenRouter), building a new organizational feature, and
investigating a failed pipeline. These are future workflows, not implemented
Azure integrations. See [Product design](docs/PRODUCT_DESIGN.md) and [Roadmap](ROADMAP.md).

The graph provides context; authorized tools retrieve live evidence; an external
harness controls approvals and execution. Reduced iterations remain a hypothesis.

## Implemented local pilot

The scanner now feeds a read-only stdio MCP server directly through SQLite.
No hand-written synthetic fixture is required for real scanned projects.

Python 3.11+, no new runtime dependencies. From the checkout:

```sh
python -m unittest discover -s tests -v
python -m org_context.cli --db inventory.sqlite scan /path/to/project
python -m org_context.cli --db inventory.sqlite list
python -m org_context.mcp_server --db inventory.sqlite
```

For `org-context`, optionally install in a virtual environment with
`python -m pip install -e .`. Installation may fetch build tooling; scanning and
serving make no network requests. Existing `show`, `search` and `export` commands
remain available. Select `--db` before the CLI subcommand.

Use the project ID returned by scan/list in `project_card`. The server also exposes
`find_projects_using(technology, ecosystem?)` and `get_reference_blueprint`.
The latter returns `not_available` for a real scanned project without a linked
blueprint; it does not invent one. Synthetic reference import remains separate.
See [MCP setup and regression checklist](docs/MCP_DEMO.md).

## Extraction and coverage

- Root README variants, CLAUDE.md, requirements.txt, pyproject.toml and package.json.
- CODEOWNERS at root, .github or docs: retained as reference text; ownership
  matching is NOT evaluated.
- .github/workflows/*.yml and *.yaml: lexical `uses:` references only, not full
  YAML semantics, scripts, triggers, permissions or execution status.
- .claude-plugin/plugin.json and marketplace.json, and
  plugins/*/.claude-plugin/plugin.json or plugins/*/plugin.json: selected string
  metadata and marketplace plugin names, not all plugin configuration.

Package extraction supports simple Python/PEP 621 and npm declarations, not
lockfiles, Poetry-specific tables, requirement includes or environment markers.
Direct URL/path package specifications are omitted. Nested packages require
explicit roots; discovery is limited to the patterns above, not arbitrary recursion.
A snapshot records checked paths, skipped files, absent fixed candidates and
unsupported analysis. Legacy DBs can be read, but need rescanning for coverage.

Evidence hashes identify source bytes. Commit is obtained from Git even for a
subdirectory; HEAD does not describe dirty files or prove deployment. Scan time
is not remote freshness. Declarations are not runtime facts or approval.

## Limits and safety

Trusted local single-user use only. No Azure/DevOps connectors, LLM, Serena,
cloud actions, shell tools or agent repair automation. The fixed git rev-parse
command reads local metadata; scanned application code is never executed.
Root/descendant symlinks are rejected or skipped. Per-file limit: 256 KB;
aggregate: 2 MB; bounded discovery: 128 candidates. Oversized files are reported;
parse errors preserve that root's old snapshot. Multi-root scans commit per root.

This is NOT a secret scanner or a sandbox against concurrent malicious file
changes. README/CLAUDE.md/CODEOWNERS text can contain sensitive information and
hostile instructions. Protect DBs/exports; treat retrieved content only as data.
No shared-service ACLs, encryption, history or tenant isolation exist. Project IDs
are derived from local absolute paths. No production-readiness guarantee.
Never gate compliance ingestion on graph, agent or GitHub availability.

Owner must choose a license before external reuse; no license grant is included.
