import json
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from org_context.discovery import call

class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.path=Path(temp.name)/'inventory.sqlite';db=sqlite3.connect(self.path)
        db.executescript('CREATE TABLE projects(id TEXT,name TEXT,root TEXT,commit_sha TEXT,scanned_at TEXT); CREATE TABLE evidence(id TEXT,project_id TEXT,path TEXT,sha256 TEXT); CREATE TABLE dependencies(project_id TEXT,ecosystem TEXT,name TEXT,specification TEXT,scope TEXT,evidence_id TEXT);')
        self.ids=['a'*64,'b'*64,'c'*64]
        for i,pid in enumerate(self.ids):
            db.execute('INSERT INTO projects VALUES(?,?,?,?,?)',(pid,'same-name' if i<2 else 'third','/fixture/'+str(i),'d'*40,'2026-11-22'))
            db.execute('INSERT INTO evidence VALUES(?,?,?,?)',(str(i)*64,pid,'requirements.txt',str(i+1)*64))
        declarations=[(0,'python','pydantic','>=2','runtime'),(0,'python','pydantic','>=2','dev'),(1,'python','pydantic','==2.9','runtime'),(2,'python','pydantic','>=2','runtime'),(0,'python','only-one','1','runtime'),(0,'python','only-one','1','dev'),(0,'npm','marked','^12','runtime'),(1,'npm','marked','^12','runtime')]
        for i,eco,name,spec,scope in declarations:db.execute('INSERT INTO dependencies VALUES(?,?,?,?,?,?)',(self.ids[i],eco,name,spec,scope,str(i)*64))
        db.commit();db.close()
        db=sqlite3.connect(self.path.as_uri()+'?mode=ro',uri=True);db.row_factory=sqlite3.Row;db.execute('PRAGMA query_only=ON');self.addCleanup(db.close)
        self.ctx=SimpleNamespace(db=db,tables={'projects','dependencies','evidence'})
    def test_discover_without_ids(self):
        r=call(self.ctx,'list_projects',{});self.assertEqual(len(r['projects']),3)
        self.assertEqual(r['projects'][0]['scan_root'],'/fixture/0')
        self.assertNotEqual(r['projects'][0]['project_id'],r['projects'][1]['project_id'])
    def test_pagination(self):
        a=call(self.ctx,'list_projects',{'limit':2});b=call(self.ctx,'list_projects',{'limit':2,'offset':a['next_offset']})
        self.assertTrue(a['has_more']);self.assertFalse(b['has_more']);self.assertEqual(len(b['projects']),1)
    def test_search_root_and_evidence(self):
        r=call(self.ctx,'find_projects_using',{'technology':'PYDANTIC','ecosystem':'python'})
        self.assertEqual(len(r['results']),4)
        self.assertTrue(all(x['scan_root'] and x['path']=='requirements.txt' and len(x['sha256'])==64 for x in r['results']))
    def test_shared_specs_and_no_duplicate_count(self):
        r=call(self.ctx,'shared_dependencies',{});groups={x['name']:x for x in r['groups']}
        self.assertEqual(groups['pydantic']['project_count'],3);self.assertTrue(groups['pydantic']['has_different_specifications'])
        self.assertFalse(groups['marked']['has_different_specifications']);self.assertNotIn('only-one',groups)
    def test_scope_and_group_paging(self):
        r=call(self.ctx,'shared_dependencies',{'project_ids':self.ids[:2],'ecosystem':'python'})
        self.assertEqual(r['groups'][0]['project_count'],2)
        a=call(self.ctx,'shared_dependencies',{'limit':1});self.assertTrue(a['has_more'])
        b=call(self.ctx,'shared_dependencies',{'limit':1,'offset':a['next_offset']});self.assertFalse(b['has_more'])
    def test_validation_and_literal_sql(self):
        for tool,args in [('list_projects',{'limit':True}),('list_projects',{'offset':-1}),('list_projects',{'path':'/etc/passwd'}),('shared_dependencies',{'project_ids':[self.ids[0],self.ids[0]]}),('shared_dependencies',{'project_ids':[self.ids[0],'f'*64]}),('find_projects_using',{'technology':'x','ecosystem':'shell'})]:
            with self.assertRaises(ValueError):call(self.ctx,tool,args)
        self.assertEqual(call(self.ctx,'find_projects_using',{'technology':"x' OR 1=1 --"})['results'],[])
    def test_missing_inventory(self):
        c=SimpleNamespace(tables=set(),db=self.ctx.db)
        self.assertEqual(call(c,'list_projects',{})['projects'],[])
        self.assertEqual(call(c,'shared_dependencies',{})['groups'],[])
    def test_read_only_and_snapshot_cleanup(self):
        before=self.path.read_bytes();call(self.ctx,'shared_dependencies',{})
        self.assertFalse(self.ctx.db.in_transaction);self.assertEqual(before,self.path.read_bytes())
        with self.assertRaises(sqlite3.OperationalError):self.ctx.db.execute('DELETE FROM projects')

if __name__=='__main__':unittest.main()
