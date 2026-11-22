# Scanner-backed MCP: setup and Grokbot regression checklist

Run from the updated checkout with Python 3.11+. No Azure access is needed.

```sh
python -m unittest discover -s tests -v
python -m org_context.cli --db inventory.sqlite scan /path/to/claudemod /path/to/claudemod/demo
python -m org_context.cli --db inventory.sqlite list
python -m org_context.mcp_server --db inventory.sqlite
```

Copy project IDs from scan/list. No fixture import is needed. The real project
root and demo are separate roots; nested package discovery is not implemented.
Existing DBs work read-only; rescan to populate scan_coverage and new sources.
The separate synthetic import command in org_context.reference still works;
never mark real organizational facts synthetic to force them into that importer.

## Cursor configuration

Replace all absolute paths with actual local values. This is a configuration
example, not a claim of a tested Cursor/Windows connection:

```json
{"mcpServers":{"org-context":{"command":"/absolute/path/to/python","args":["-m","org_context.mcp_server","--db","/absolute/path/to/inventory.sqlite"],"env":{"PYTHONUTF8":"1","PYTHONPATH":"/absolute/path/to/org-context"}}}}
```

On Windows use the virtualenv python.exe and JSON-escaped backslashes. The server
configures stdio as UTF-8 and emits ASCII-escaped JSON so Hebrew round-trips even
through legacy output encodings. PYTHONUTF8 remains an optional defensive setting.
No PowerShell execution-policy changes are required by the tool itself.

## Expected calls

- tools/list exposes project_card, find_projects_using, get_reference_blueprint.
- project_card(project_id) reads actual scanner evidence and stored coverage.
- find_projects_using(technology="react", ecosystem="npm") searches declarations.
- get_reference_blueprint(project_id) returns not_available for a scanned project
  with no blueprint, not a fabricated adaptation plan.
- project_card evidence is capped at 20 records and dependencies at 50; flags
  disclose truncation. Search is capped at 50. Full trusted-local evidence remains
  accessible through CLI show/export; these are not exposed as shell tools.

## What Grokbot should verify on claudemod

1. Run the whole test suite and report actual totals and failures.
2. Record the tested org-context commit and claudemod commit/dirty state.
3. Scan claudemod root and demo without manually authored claims.
4. Confirm evidence for supported plugin manifests, marketplace and workflows;
   report actual counts, don't assume nine plugins or five workflows permanently.
5. Check that coverage contains only this scan's paths/general limitations, not
   hardcoded OpenRouter/ClaudeMetrics names; CODEOWNERS is text, not resolved ownership.
6. Find an actual demo dependency through MCP and check evidence path/hash.
7. Request a real project's blueprint and confirm not_available.
8. Verify Hebrew via actual stdio; test Windows/Cursor separately if available.
9. Reject shell/extra-path/SQL arguments and verify DB remains unchanged.
10. Send a newline-terminated request over 16 KB followed by ping: error then ping
    response. Unterminated or >1 MB input terminates defensively; no time-based
    protection against a stalled local client is claimed.
11. Scan a repository subdirectory and check commit SHA when Git HEAD exists.
12. Review skipped/unsupported sources and truncation before claiming completeness.

Tool invocation validation errors return isError with guidance; unknown tools,
malformed envelopes and non-object arguments remain protocol errors. This is a
synchronous subset of MCP 2025-03-26, not SDK conformance certification or a shared
server. No HTTP, subscriptions or live Azure integration.

The six synthetic questions in examples/reference-eval.json remain a manual
product-value experiment, not an executed evaluation. Use equal sources/model/
budgets with and without tools plus a static-blueprint baseline. Test-suite passes
do not demonstrate fewer iterations or a valid root-cause diagnosis.
