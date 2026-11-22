import json
import sqlite3
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from org_context.retrieval_eval import validate,pointer,evaluate,read_cases,main

class RetrievalEvalTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.root=Path(temp.name)
        self.path=self.root/'db.sqlite';db=sqlite3.connect(self.path)
        db.executescript('CREATE TABLE projects(id TEXT); CREATE TABLE dependencies(name TEXT); CREATE TABLE evidence(id TEXT); INSERT INTO projects VALUES("a"),("b");');db.commit();db.close()
        db=sqlite3.connect(self.path.as_uri()+'?mode=ro',uri=True);db.row_factory=sqlite3.Row;db.execute('PRAGMA query_only=ON');self.addCleanup(db.close)
        self.ctx=SimpleNamespace(db=db)
    def doc(self,op='count',expected=2):
        return {'schema_version':1,'cases':[{'id':'roots','tool':'list_projects','arguments':{},'checks':[{'path':'/projects','op':op,'expected':expected}]}]}
    def query_double(self,ctx,tool,args):
        return {'projects':[dict(r) for r in ctx.db.execute('SELECT id FROM projects')],'has_more':False}
    def test_pass_and_no_db_changes(self):
        before=self.path.read_bytes();r=evaluate(self.ctx,self.doc(),self.query_double)
        self.assertEqual(r['counts']['PASS'],1);self.assertEqual(before,self.path.read_bytes())
        with self.assertRaises(sqlite3.OperationalError):self.ctx.db.execute('DELETE FROM projects')
    def test_fail(self):
        self.assertEqual(evaluate(self.ctx,self.doc(expected=3),self.query_double)['counts']['FAIL'],1)
    def test_partial_is_inconclusive(self):
        r=evaluate(self.ctx,self.doc(),lambda *_:{'projects':[{},{}],'has_more':True})
        self.assertEqual(r['counts']['INCONCLUSIVE'],1)
    def test_error_is_content_free(self):
        def broken(*_):raise ValueError('PRIVATE_SOURCE_CONTENT')
        r=evaluate(self.ctx,self.doc(),broken)
        self.assertEqual(r['counts']['ERROR'],1);self.assertNotIn('PRIVATE_SOURCE_CONTENT',json.dumps(r))
    def test_schema_rejects_arbitrary_tools_and_duplicate_ids(self):
        d=self.doc();d['cases'][0]['tool']='shell'
        with self.assertRaises(ValueError):validate(d)
        d=self.doc();d['cases']*=2
        with self.assertRaises(ValueError):validate(d)
    def test_pointer_and_distinct_count(self):
        self.assertEqual(pointer({'a/b':{'~':4}},'/a~1b/~0'),4)
        d=self.doc('distinct_count',2);d['cases'][0]['checks'][0]['field']='id'
        self.assertEqual(evaluate(self.ctx,d,self.query_double)['counts']['PASS'],1)
        d=self.doc();d['cases'][0]['checks'][0]['path']='/projects/-1'
        self.assertEqual(evaluate(self.ctx,d,self.query_double)['counts']['FAIL'],1)
    def test_case_size(self):
        path=self.root/'large.json';path.write_bytes(b'x'*128001)
        with self.assertRaises(ValueError):read_cases(path)
    def test_report_cannot_overwrite_inputs(self):
        case=self.root/'cases.json';case.write_text(json.dumps(self.doc()));before=self.path.read_bytes()
        self.assertEqual(main(['--db',str(self.path),'--cases',str(case),'--output',str(self.path)]),2)
        self.assertEqual(before,self.path.read_bytes())

if __name__=='__main__':unittest.main()
