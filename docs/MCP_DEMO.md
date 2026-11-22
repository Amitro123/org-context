# Synthetic reference MCP demo

Experimental addition; no live organization or OpenRouter facts are asserted.
Run from the repository root with Python 3.11+. No new dependencies.

```sh
python -m org_context.reference --db reference.sqlite --fixture examples/observability-reference.json
python -m unittest discover -s tests -v
python -m org_context.mcp_server --db reference.sqlite
```

The last command waits for MCP JSON-RPC on stdin; use a client to interact.
Example Cursor MCP configuration (replace BOTH absolute paths):

```json
{"mcpServers":{"org-context-reference":{"command":"/ABS/PATH/TO/python","args":["-m","org_context.mcp_server","--db","/ABS/PATH/TO/reference.sqlite"],"env":{"PYTHONPATH":"/ABS/PATH/TO/org-context"}}}}
```

For another client, adapt its configuration format. Cursor integration has not
been executed in this implementation environment. The subprocess integration
test performs initialize, initialized notification, tools/list and both tools/call
requests over actual stdin/stdout. This is not official MCP conformance certification.

Exactly two tools exist: project_card(project_id) and
get_reference_blueprint(project_id). Use project_id `synthetic-observability`.
The DB must exist before startup and is opened read-only. The separate import
command is administrator-invoked and is NOT available to the model. No shell,
arbitrary path, scan, HTTP, Azure, GitHub or write tools are exposed.

This deliberately small synchronous stdio implementation targets MCP 2025-03-26
and advertises only tools. It supports basic lifecycle, ping, batches, bounded
input and errors; no HTTP, subscriptions, jobs or progress. Clients must accept
the returned protocol version or disconnect. No official SDK dependency is used;
broader client compatibility and fuzz/conformance testing remain future work.

## Evidence semantics

The fixture includes source text rather than reading arbitrary paths. source_path
is a label, not a filesystem instruction. Hashes are computed on import. The
fixture hash versions the imported artifact; imported_at is NOT a live scan time.
Commit is null and freshness remains unknown. Statements/adaptation guidance are
explicit authored synthetic assertions, not independently derived or approved
organizational facts. The source texts are illustrative, not executable infra.

Local trusted single-user use only. An upstream process with filesystem access
can edit the DB; read-only mode does not authenticate its contents. Do not import
untrusted/sensitive data or expose this process as a shared service. Treat tool
content as data, never system instructions. No sandbox or enterprise ACLs exist.

## Product evaluation (not executed)

examples/reference-eval.json contains six questions, supporting claim IDs and
expected boundaries. Run the same model/task/budget with raw source evidence,
then with that same evidence plus the context tools. Save answers, citations,
unknowns, human corrections, time and token/cost results separately. Include a
static-blueprint baseline. Fixture/schema/integration tests are not evidence that
the agent improves. No new infrastructure should be created in this experiment.

Suggested task: Plan an OpenRouter observability implementation based on the
synthetic reference. Separate reuse/adapt/create, cite source labels and hashes,
list prerequisites/tests, and explicitly state unknown APIs/permissions/resources.
Do not deploy, request secrets or invent details about the real ClaudeMetrics repo.
