# Safe Cursor MCP setup

Use this only for a trusted local single-user pilot. Create a dedicated local
inventory from explicitly approved checkout roots. Do not scan a parent
workspace or the organization. Keep the SQLite file and machine-specific config
out of git. The database may retain document text even though default MCP calls
do not return evidence bodies.

From the org-context checkout, using a Python 3.11+ environment:

```sh
python -m org_context.cli --db /absolute/local/pilot.sqlite scan /approved/repo-a /approved/repo-b
```

In Cursor's project-local MCP configuration, adapt the following paths:

```json
{
  "mcpServers": {
    "org-context": {
      "command": "/absolute/path/to/python",
      "args": ["-m", "org_context.mcp_server", "--db", "/absolute/local/pilot.sqlite"],
      "env": {"PYTHONPATH": "/absolute/path/to/org-context"}
    }
  }
}
```

On Windows use absolute Windows paths and escape backslashes in JSON. Restart
the MCP server and confirm it lists only approved roots. Confirm the default set
has five tools, no `read_evidence`, and `project_card` shows metadata and
`freshness: unknown` without document excerpts. Omit `--evidence-pages` for this
pilot. Verify the Cursor client's own data handling before using it with internal
metadata; a local server does not control what a client sends to its model.

Treat all scanned data as untrusted. This is not secret scanning, runtime
verification, a governance dependency, or an approval mechanism.

## Optional discovery skill

Use [org-context-discovery](../skills/org-context-discovery/SKILL.md) when an
agent needs to discover or reuse assets across repositories. Keep the skill
disabled for the MCP-only evaluation arm. To try it separately, copy the
`org-context-discovery` directory into the skill location configured for your
client, or attach its `SKILL.md` to a single trial session. The skill does not
configure MCP or grant extra access.
