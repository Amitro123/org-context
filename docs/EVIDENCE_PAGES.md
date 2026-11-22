# Opt-in stored-evidence paging

This increment adds read_evidence and scan_root metadata, not richer workflow
parsing, Git repository-root discovery, best-effort scanning or multi-user ACLs.
The legacy three tools remain the default. Reading more retained document content
is explicit opt-in because it may expose sensitive text already stored in SQLite.

```sh
python -m unittest discover -s tests -v
python -m org_context.mcp_server --db inventory.sqlite --evidence-pages
```

For Cursor/Claude Code, append --evidence-pages to the existing server args and
restart the connection. With the flag tools/list should return FOUR tools.
Without it the original three remain and read_evidence is unavailable.

Get project_id and evidence_id from project_card; call read_evidence with those
full IDs, offset=0, limit=1500. Continue with next_offset and expected_sha256 set
to source_sha256. Offsets count Unicode characters, not bytes or lines. Maximum
page size is 4000 characters. A missing ID after rescan is not_found; mismatched
expected hash is stale_evidence. Re-read the project card rather than mixing pages.

Only the existing DB is queried. No filesystem path or live file is read by the
tool. Cross-project IDs cannot retrieve another project's evidence. This scoping
is not enterprise authorization: the local client can query any project in its DB.
The caller must be authorized for the entire database before starting the server.

source_sha256 hashes original scanned file bytes. stored_content_sha256 hashes
the full retained representation; workflow/plugin summaries are NOT complete
source files and may have a different hash. Metadata-only evidence returns
not_available. Hashes establish identity, not factual truth or authenticity.

Project cards include location.scan_root from the stored snapshot. repository_root
remains null: path containment, matching commits or the name demo alone do not
prove repository membership. Paths may be sensitive local metadata.

Grokbot: run all previous tests; verify three tools by default/four with the flag,
then read and reassemble the real claudemod README beyond 1500 characters. Check
hashes, Hebrew, EOF, wrong-project IDs, invalid offsets/limits, rescan behavior,
metadata-only evidence and unchanged DB bytes. Also confirm ordinary project_card
and synthetic reference calls still work. Windows/Cursor and SDK compatibility
must be reported separately from Linux/helper tests. No live cloud access needed.
