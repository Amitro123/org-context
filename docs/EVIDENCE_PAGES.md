# Opt-in stored-evidence paging

This increment adds read_evidence and scan_root metadata, not richer workflow
parsing, Git repository-root discovery, best-effort scanning or multi-user ACLs.
The default tool set remains available without paging. Scanned project cards
return metadata only by default: content_excerpt is null, has_retained_body and
retained_bytes describe body presence and stored-representation UTF-8 byte size,
and content_withheld says that a body was not returned. Reading excerpts and
paged bodies requires explicit --evidence-pages because retained text may be
sensitive. The flag does not prevent bodies being stored locally during scanning.

```sh
python -m unittest discover -s tests -v
python -m org_context.mcp_server --db inventory.sqlite --evidence-pages
```

For Cursor/Claude Code, append --evidence-pages to the existing server args and
restart the connection. With the flag tools/list should return SIX tools: the five
default tools plus read_evidence. Without it five tools are listed and read_evidence
is unavailable. With the flag, project cards can include excerpts up to 1500
characters as well. This flag is a per-session disclosure choice, not an ACL or
secret scrubber. Synthetic blueprint claims are a separate explicit read path;
local CLI show also remains a full diagnostic read. Graph export omits bodies
unless the local caller supplies export --include-content.

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

Grokbot: run all previous tests; verify five tools by default/six with the flag,
then read and reassemble the real claudemod README beyond 1500 characters. Check
hashes, Hebrew, EOF, wrong-project IDs, invalid offsets/limits, rescan behavior,
metadata-only evidence and unchanged DB bytes. Also confirm ordinary project_card
and synthetic reference calls still work. Windows/Cursor and SDK compatibility
must be reported separately from Linux/helper tests. No live cloud access needed.
