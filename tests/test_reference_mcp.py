import hashlib
import io
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from org_context.reference import Context, load_demo
from org_context.mcp_server import Session, serve, MAX_LINE

ROOT=Path(__file__).resolve().parents[1]
FIXTURE=ROOT/"examples/observability-reference.json"

def init():
    return {"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"test","version":"1"}}}

class ReferenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.db=Path(self.tmp.name)/"ref.sqlite"
        load_demo(self.db,FIXTURE)
        self.context=Context(self.db); self.addCleanup(self.context.close)

    def test_card_and_unknown(self):
        self.assertEqual(len(self.context.call("project_card",{"project_id":"synthetic-observability"})["available_claims"]),6)
        self.assertEqual(self.context.call("project_card",{"project_id":"missing"})["status"],"not_found")

    def test_evidence_and_eval_coverage(self):
        result=self.context.call("get_reference_blueprint",{"project_id":"synthetic-observability"})
        claims={c["id"]:c for c in result["claims"]}
        for c in claims.values():
            self.assertEqual(c["source_sha256"],hashlib.sha256(c["source_text"].encode()).hexdigest())
            self.assertEqual(c["support_status"],"synthetic_fixture_assertion")
        questions=json.loads((ROOT/"examples/reference-eval.json").read_text())["questions"]
        self.assertEqual(len(questions),6)
        self.assertTrue(all(q["required_claim"] in claims for q in questions))
        self.assertIsNone(result["snapshot"]["commit"])
        self.assertEqual(len(result["unknowns"]),3)

    def test_read_only(self):
        with self.assertRaises(sqlite3.OperationalError):
            self.context.db.execute("DELETE FROM reference_claims")

    def test_no_arbitrary_arguments(self):
        for tool,args in [("shell",{}),("project_card",{"project_id":"../secret"}),("project_card",{"project_id":"ok","path":"/tmp"})]:
            with self.assertRaises(ValueError): self.context.call(tool,args)

    def test_reimport_idempotent(self):
        load_demo(self.db,FIXTURE)
        self.assertEqual(self.context.db.execute("SELECT count(*) FROM reference_claims").fetchone()[0],6)

    def test_invalid_import_preserves_snapshot(self):
        bad=Path(self.tmp.name)/"bad.json"; bad.write_text('{"synthetic":false}')
        with self.assertRaises(ValueError): load_demo(self.db,bad)
        self.assertEqual(self.context.db.execute("SELECT count(*) FROM reference_claims").fetchone()[0],6)

    def test_lifecycle_and_tools(self):
        s=Session(self.context)
        self.assertIn("error",s.handle({"jsonrpc":"2.0","id":2,"method":"tools/list"}))
        self.assertEqual(s.handle(init())["result"]["protocolVersion"],"2025-03-26")
        self.assertIsNone(s.handle({"jsonrpc":"2.0","method":"notifications/initialized"}))
        self.assertEqual(len(s.handle({"jsonrpc":"2.0","id":3,"method":"tools/list"})["result"]["tools"]),2)

    def test_bad_json_and_size(self):
        out=io.StringIO(); serve(self.context,io.StringIO('bad\n'),out)
        self.assertEqual(json.loads(out.getvalue())["error"]["code"],-32700)
        out=io.StringIO(); serve(self.context,io.StringIO('x'*(MAX_LINE+1)),out)
        self.assertEqual(json.loads(out.getvalue())["error"]["message"],"Message too large")

    def test_batch_and_notifications(self):
        s=Session(self.context)
        self.assertEqual(len(s.handle([{"jsonrpc":"2.0","id":1,"method":"ping"},{"jsonrpc":"2.0","method":"notifications/cancelled"}])),1)
        self.assertIn("error",s.handle([]))

    def test_stdio_process(self):
        requests=[init(),{"jsonrpc":"2.0","method":"notifications/initialized"},
          {"jsonrpc":"2.0","id":2,"method":"tools/list"},
          {"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"project_card","arguments":{"project_id":"synthetic-observability"}}},
          {"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"get_reference_blueprint","arguments":{"project_id":"synthetic-observability"}}},
          {"jsonrpc":"2.0","id":5,"method":"tools/call","params":{"name":"project_card","arguments":{"project_id":"x","path":"/etc/passwd"}}}]
        before=self.db.read_bytes()
        run=subprocess.run([sys.executable,"-m","org_context.mcp_server","--db",str(self.db)],cwd=ROOT,input=''.join(json.dumps(r)+'\n' for r in requests),capture_output=True,text=True,timeout=10)
        self.assertEqual(run.returncode,0,run.stderr)
        responses=[json.loads(line) for line in run.stdout.splitlines()]
        self.assertEqual(len(responses),5)
        bundle=json.loads(responses[3]["result"]["content"][0]["text"])
        self.assertEqual(len(bundle["claims"]),6)
        self.assertEqual(responses[4]["error"]["code"],-32602)
        self.assertEqual(before,self.db.read_bytes())

if __name__=="__main__": unittest.main()
