# org-context

Local, evidence-backed repository inventory for coding agents. Experimental
single-user CLI, not an enterprise authorization service.

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
For the `org-context` command, install in your virtual environment using
`python -m pip install -e .`. Build tooling installation may require network
access; scanning itself makes no network requests.

## Scope
Only exact allowlisted files at explicitly supplied roots are read: README,
README.md, README.rst, README.txt, requirements.txt, pyproject.toml and package.json.
Scanning is non-recursive; supply nested monorepo packages separately. Standard
PEP 621 dependencies, simple requirements and four npm scopes are supported.
Lockfiles, Poetry-specific tables, requirement includes, environment markers and
other unsupported expressions are not currently extracted. Direct URL/path
dependencies are excluded. This is not a completeness guarantee.

Declared dependencies are not proof of runtime usage or organizational approval.
README text is retained as unverified reference material, not accepted facts.
Manifest bodies are not retained. Evidence includes file path, content hash and
extraction method; project metadata includes scan time and Git HEAD if available.
HEAD alone does not describe uncommitted changes.

A successful root scan replaces its snapshot atomically. Parse errors retain the
old snapshot. Earlier roots in a multi-root scan may already have committed.
Skipped oversized or symlinked files are reported. Project identity derives from
absolute local path; use full IDs to disambiguate names.

## Safety and limits
- No LLM, cloud ingestion, MCP, Serena, remote crawling or deployment is included.
- No scanned application code, build scripts or hooks are executed. A fixed local
  `git rev-parse` command is used solely for commit metadata.
- Root symlinks are rejected; leaf symlinks and files above 256 KB are skipped.
- This is NOT a secret scanner. READMEs can contain secrets, PII and malicious
  instructions. Treat content as untrusted data; protect DBs and exports.
- Symlink checks are not a sandbox against concurrent malicious file changes.
  Scan trusted stable checkouts only.
- No source ACL propagation, encryption, historical versioning, tenant isolation
  or multi-user authorization. Local paths are not cross-machine identities.
- No guarantee of freshness, extraction completeness or production readiness.
- Never make compliance ingestion depend on this tool.

See [ROADMAP.md](ROADMAP.md). Owner must select a license before external reuse;
no license grant is included in this bootstrap.
