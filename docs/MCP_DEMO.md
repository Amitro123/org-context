# Scanner-backed MCP and multi-project discovery

Python 3.11+, local authorized single-user use only. No new dependencies.

```sh
python -m unittest discover -s tests -v
python -m org_context.cli --db inventory.sqlite scan /path/to/repo-a /path/to/repo-b /path/to/repo-b/demo
python -m org_context.mcp_server --db inventory.sqlite
# Optional scanned-document excerpts and paging:
python -m org_context.mcp_server --db inventory.sqlite --evidence-pages
```

Five tools are available by default: list_projects, project_card,
find_projects_using, shared_dependencies and get_reference_blueprint.
--evidence-pages adds read_evidence as a sixth tool. Restart clients after updating.
See [evidence paging](EVIDENCE_PAGES.md). Default scanned project cards return
metadata only, not excerpts. Existing complete inventory DBs need no migration
for discovery. A write-side dependency-storage upgrade requires all old roots to
be rescanned to recover formerly discarded specifications; see README.md.

## Agent workflow

1. list_projects({}) discovers full scanned IDs without CLI access. Pages accept
   offset (0..100000) and limit (1..50, default 20). Follow next_offset.
2. project_card({project_id: ...}) retrieves source metadata and scan coverage;
   document excerpts require the server flag, not a tool argument.
3. find_projects_using({technology: "pydantic", ecosystem: "python"}) returns
   declarations with scan_root, commit, scan time, evidence ID, path and hash.
   Pages accept offset (0..100000) and limit (1..50, default 50); when has_more
   is true, call again with next_offset.
4. shared_dependencies({}) finds packages declared by >=2 scanned project IDs.
   Optional ecosystem and project_ids (2..10 distinct IDs) restrict the comparison;
   offset/limit page package groups. Each group's declarations are capped at 50
   and explicitly marked if truncated. project_count is distinct scanned IDs,
   not declarations, names or distinct repositories.
5. With --evidence-pages, read stored source text by authorized project/evidence IDs.

Specification differences (>=2 versus ==2.9, for example) do not establish
installed-version differences or incompatibility. All queries operate on imported
snapshots. Missing results do not prove absence outside scan coverage. Synthetic
reference projects are not included in list_projects; their existing reference
tools remain usable. Pagination order is deterministic for an unchanged DB;
a rescan between calls can shift offset pages. Comparison groups and their evidence
are read within one SQLite read snapshot. Response sizes are bounded, not a
performance SLA for arbitrary database sizes. This is not enterprise authorization.
The local client must be authorized for all projects and local paths in the DB.

## Cursor/Claude Code

For a minimal approved-roots-only Cursor config, see [safe Cursor setup](CURSOR_MCP.md).
Keep the existing stdio configuration, pointing --db at inventory.sqlite and
PYTHONPATH at the checkout. Add --evidence-pages only if reading retained document
text is approved. PythonUTF8=1 may be retained as a defensive environment setting.
No machine-specific configuration should be committed. Windows, Cursor and Claude
Code must be tested separately, not inferred from Linux/SDK tests.

## Grokbot acceptance checklist

- Run all tests and record exact commits/dirty states for code and scan targets.
- Scan the same four approved repositories/root selections as before, no fixtures.
- Discover all 12 scan roots via list_projects pages without pre-known IDs;
  use actual counts if source revisions/root selections changed.
- Confirm duplicate display names retain distinct IDs/roots.
- Compare pydantic/requests/marked declarations with original source files.
- Check shared dependencies and specification_count against actual declarations;
  duplicate scopes must not inflate project_count.
- Check project_ids and ecosystem filters, group paging and declared-row truncation.
- Verify scan_root and evidence path/hash on search and comparison results.
- Test bad parameters, SQL-like literal search, readonly DB and paging opt-in.
- Confirm five default/six opt-in tools and existing synthetic-reference behavior.

Python dependency-groups and root requirements-*.txt extraction are implemented.
Workflow `uses:` extraction is lexical: quoted values are read; dynamic or
unparsed values are counted in coverage, not stored. Install commands in GitHub
`run:` and Azure `script:`/`bash:`/`pwsh:` steps (`pip`/`pip3`/`python -m pip`,
`pipx`, `uv pip`, `uv tool`, `npm i`/`install` and `npx`) are read lexically and
never executed; static packages are found by `find_projects_using` with scope
`ci-install:<workflow path>`, while variables, URLs, local paths, `-r` files and
unparsed arguments are counted as `ci_install_*` reasons, not stored. Older
inventories need a rescan to gain these rows. Project cards can also
surface bounded lexical template and repository declarations from conventional
root Azure pipeline YAML. These are local declarations, not evaluated YAML or
deployed state. Actions search, workspace discovery, repository_root,
best-effort scanning and full CI parsing are not implemented.
Known extraction gaps remain and must be reported in evaluation. The manual
reference-eval experiment is still not an executed product-value measurement;
see [paired pilot evaluation](PILOT_EVAL.md).
