import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from types import SimpleNamespace
from org_context.evidence_pages import read_evidence,add_scan_root

class EvidencePagesTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.path=Path(temp.name)/'inventory.sqlite'; db=sqlite3.connect(self.path)
        db.executescript('CREATE TABLE projects(id TEXT,root TEXT,commit_sha TEXT,scanned_at TEXT); CREATE TABLE evidence(id TEXT,project_id TEXT,path TEXT,sha256 TEXT,kind TEXT,method TEXT,content TEXT);')
        self.pid='a'*64; self.eid='b'*64; self.other='c'*64; self.text='מידע\n'*1100; self.sha=hashlib.sha256(self.text.encode()).hexdigest()
        db.execute('INSERT INTO projects VALUES(?,?,?,?)',(self.pid,'/fixture/repo/demo','d'*40,'2026-11-22T00:00:00Z'))
        db.execute('INSERT INTO projects VALUES(?,?,?,?)',(self.other,'/fixture/other',None,'2026-11-22T00:00:00Z'))
        db.execute('INSERT INTO evidence VALUES(?,?,?,?,?,?,?)',(self.eid,self.pid,'README.md',self.sha,'unverified_document','reference_text',self.text)); db.commit(); db.close()
        db=sqlite3.connect(self.path.as_uri()+'?mode=ro',uri=True); db.row_factory=sqlite3.Row; db.execute('PRAGMA query_only=ON'); self.addCleanup(db.close)
        self.ctx=SimpleNamespace(db=db,tables={'projects','evidence'})
    def args(self,**kw): return {'project_id':self.pid,'evidence_id':self.eid,**kw}
    def test_reassemble_unicode(self):
        offset=0; chunks=[]
        while True:
            r=read_evidence(self.ctx,self.args(offset=offset,limit=997,expected_sha256=self.sha)); chunks.append(r['content'])
            self.assertLessEqual(r['returned_characters'],997)
            if not r['has_more']: break
            offset=r['next_offset']
        self.assertEqual(''.join(chunks),self.text)
        self.assertEqual(r['stored_content_sha256'],self.sha)
    def test_project_isolation_and_missing(self):
        self.assertEqual(read_evidence(self.ctx,{**self.args(),'project_id':self.other})['status'],'not_found')
        self.assertEqual(read_evidence(self.ctx,self.args(evidence_id='f'*64))['status'],'not_found')
    def test_bad_arguments(self):
        for args in [self.args(path='/etc/passwd'),self.args(project_id='../x'),self.args(offset=-1),self.args(offset=True),self.args(limit=4001),self.args(limit=False),self.args(expected_sha256='bad')]:
            with self.assertRaises(ValueError): read_evidence(self.ctx,args)
    def test_stale_and_eof(self):
        self.assertEqual(read_evidence(self.ctx,self.args(expected_sha256='f'*64))['status'],'stale_evidence')
        self.assertEqual(read_evidence(self.ctx,self.args(offset=len(self.text)))['content'],'')
        with self.assertRaises(ValueError): read_evidence(self.ctx,self.args(offset=len(self.text)+1))
    def test_snapshot_and_location(self):
        r=read_evidence(self.ctx,self.args()); self.assertEqual(r['snapshot']['commit'],'d'*40)
        r=add_scan_root(self.ctx,{'status':'ok','project_id':self.pid,'synthetic':False})
        self.assertEqual(r['location']['scan_root'],'/fixture/repo/demo'); self.assertIsNone(r['location']['repository_root'])
    def test_not_filesystem_reader(self):
        # The path is only an evidence label; there is no corresponding README on disk.
        r=read_evidence(self.ctx,self.args()); self.assertEqual(r['source_path'],'README.md'); self.assertTrue(r['content'])
        with self.assertRaises(sqlite3.OperationalError): self.ctx.db.execute('DELETE FROM evidence')
    def test_summary_hash_scope(self):
        self.ctx.db.close()
        db=sqlite3.connect(self.path); db.execute('UPDATE evidence SET method=?,content=?',('structured_parser','{"name":"sample"}')); db.commit(); db.close()
        db=sqlite3.connect(self.path.as_uri()+'?mode=ro',uri=True); db.row_factory=sqlite3.Row; self.addCleanup(db.close); self.ctx.db=db
        r=read_evidence(self.ctx,self.args()); self.assertIn('summary',r['content_representation']); self.assertNotEqual(r['source_sha256'],r['stored_content_sha256'])
    def test_metadata_only(self):
        self.ctx.db.close(); db=sqlite3.connect(self.path); db.execute('UPDATE evidence SET content=NULL'); db.commit(); db.close()
        db=sqlite3.connect(self.path.as_uri()+'?mode=ro',uri=True); db.row_factory=sqlite3.Row; self.addCleanup(db.close); self.ctx.db=db
        self.assertEqual(read_evidence(self.ctx,self.args())['status'],'not_available')

if __name__=='__main__': unittest.main()
