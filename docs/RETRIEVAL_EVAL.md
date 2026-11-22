# Offline retrieval regression runner

This is NOT an LLM evaluator, MCP transport test or proof of fewer iterations.
It calls the same read-only discovery dispatch directly on a SQLite snapshot.
It does not use models, credentials, network calls or live project scanning.

```sh
python -m org_context.retrieval_eval --db inventory.sqlite --cases reviewed-cases.json --output new-report.json
```

Exit codes: 0 all PASS, 1 any FAIL/INCONCLUSIVE/ERROR, 2 setup error. Report output
must be a new file. Raw source content, tool responses, arguments and exception
messages are not written to reports; case IDs may still be sensitive metadata.

Start by copying examples/retrieval-cases.template.json. Its 12-root/4-pydantic
expectations are historical examples from a reported four-repository run, NOT
freshly validated ground truth after Python coverage changes. Independently
review every expected answer and record inventory source commits separately.
Do not change expected values merely to make a failing run pass.

Each case names list_projects, find_projects_using or shared_dependencies,
arguments and 1-20 checks. Checks use JSON pointers and equals, count (list length)
or distinct_count (distinct field values in list rows). Equality is JSON-value
comparison. Package comparisons use the actual discovery output: groups, not
results. No arbitrary Python, SQL, shell or expressions can be specified.

Any reported truncation/has_more makes the case INCONCLUSIVE even if its checks
match. Narrow selection or design explicit page cases; the runner does not
silently fetch pages. Up to 100 cases and 128 KB case input. One SQLite read
transaction spans the run, but source code freshness/completeness is not verified.
The runner is not a sandbox or enterprise authorization boundary.

Unit tests use a SQLite-backed query double to test runner behavior independently.
Grokbot should also run the CLI against the actual discovery implementation and
four-repository inventory, and run the full repository test suite. Record case-file
hash, input versions and actual report. Client/Windows testing remains separate.
After retrieval regression, conduct a separately controlled agent experiment with
the same model, sources, tasks and budgets, with/without context and a static
blueprint baseline. This runner does not execute that product-value experiment.
