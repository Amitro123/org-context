"""Regression cases for the follow-up review items; local fixtures only."""
import json
from pathlib import Path
import sqlite3
import tempfile
import tomllib
from types import SimpleNamespace
import unittest
from org_context import __version__, cli
from org_context.discovery import call as discovery_call, snapshot
from org_context.inventory_context import Context
from org_context.mcp_server import Session
from org_context.reference import load_demo

ROOT=Path(__file__).resolve().parents[1]
WORKFLOW="""jobs:
  test:
    steps:
      - uses: actions/checkout@v4
      - uses: "actions/setup-python@v5"
      - uses: 'actions/cache@v4' # pinned
      - uses: ${{ matrix.action }}
      - uses: "${{ matrix.other }}"
      - uses: "unterminated@v1
      - uses: ./local-action
"""

def request(name,args,rid=3):
    return {'jsonrpc':'2.0','id':rid,'method':'tools/call','params':{'name':name,'arguments':args}}

def initialize(session):
    session.handle({'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2025-03-26','capabilities':{},'clientInfo':{'name':'t','version':'1'}}})
    session.handle({'jsonrpc':'2.0','method':'notifications/initialized'})
    return session

class FollowupCase(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.root=Path(temp.name);self.repo=self.root/'service';self.repo.mkdir()
        self.path=self.root/'inventory.sqlite';self.db=cli.connect(self.path);self.addCleanup(self.db.close)
    def write(self,name,text):
        p=self.repo/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(text,encoding='utf-8')

class WorkflowReferenceTests(FollowupCase):
    def test_quoted_references_are_extracted_and_unparsed_values_are_counted(self):
        self.write('.github/workflows/check.yml',WORKFLOW)
        result=cli.scan(self.db,self.repo);evidence=cli.show(self.db,result['project_id'])['evidence']
        content=json.loads(next(e for e in evidence if e['kind']=='workflow')['content'])
        self.assertEqual(content['declared_action_references'],['actions/checkout@v4','actions/setup-python@v5','actions/cache@v4','./local-action'])
        omitted={x['reason']:x['count'] for x in result['coverage']['omitted_declarations'] if x['path']=='.github/workflows/check.yml'}
        self.assertEqual(omitted,{'dynamic_action_reference':2,'unsupported_expression':1})
        self.assertNotIn('matrix',json.dumps(result['coverage']));self.assertNotIn('unterminated',json.dumps(result['coverage']))
    def test_comment_and_empty_values(self):
        self.write('.github/workflows/a.yaml','steps:\n  - uses: actions/checkout@v4 # keep\n  - uses:\n')
        result=cli.scan(self.db,self.repo);omitted=result['coverage']['omitted_declarations']
        content=json.loads(cli.show(self.db,result['project_id'])['evidence'][0]['content'])
        self.assertEqual(content['declared_action_references'],['actions/checkout@v4'])
        self.assertIn({'path':'.github/workflows/a.yaml','reason':'unsupported_expression','count':1},omitted)

class FindProjectsPagingTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.path=Path(temp.name)/'inventory.sqlite';db=sqlite3.connect(self.path)
        db.executescript('CREATE TABLE projects(id TEXT,name TEXT,root TEXT,commit_sha TEXT,scanned_at TEXT); CREATE TABLE evidence(id TEXT,project_id TEXT,path TEXT,sha256 TEXT); CREATE TABLE dependencies(project_id TEXT,ecosystem TEXT,name TEXT,specification TEXT,scope TEXT,evidence_id TEXT);')
        for i in range(60):
            pid='%064x'%i;eid='%064x'%(1000+i)
            db.execute('INSERT INTO projects VALUES(?,?,?,?,?)',(pid,'p%d'%i,'/fixture/%d'%i,None,'2026-11-22'))
            db.execute('INSERT INTO evidence VALUES(?,?,?,?)',(eid,pid,'requirements.txt','a'*64))
            db.execute('INSERT INTO dependencies VALUES(?,?,?,?,?,?)',(pid,'python','pydantic','>=2','runtime',eid))
        db.commit();db.close()
        db=sqlite3.connect(self.path.as_uri()+'?mode=ro',uri=True);db.row_factory=sqlite3.Row;db.execute('PRAGMA query_only=ON');self.addCleanup(db.close)
        self.ctx=SimpleNamespace(db=db,tables={'projects','dependencies','evidence'})
    def search(self,**args): return discovery_call(self.ctx,'find_projects_using',{'technology':'pydantic',**args})
    def test_default_first_page_is_unchanged_but_has_a_continuation(self):
        page=self.search();self.assertEqual(len(page['results']),50)
        self.assertTrue(page['truncated']);self.assertTrue(page['has_more']);self.assertEqual(page['next_offset'],50)
    def test_pages_are_complete_ordered_and_non_overlapping(self):
        seen=[];offset=0
        while offset is not None:
            page=self.search(offset=offset,limit=7);seen+=[x['project_id'] for x in page['results']];offset=page['next_offset']
        self.assertEqual(len(seen),60);self.assertEqual(len(set(seen)),60);self.assertEqual(seen,sorted(seen))
        last=self.search(offset=55);self.assertEqual(len(last['results']),5);self.assertFalse(last['has_more']);self.assertIsNone(last['next_offset'])
    def test_invalid_paging_is_rejected(self):
        for args in ({'limit':0},{'limit':51},{'offset':-1},{'offset':100001},{'limit':True},{'offset':'0'}):
            with self.subTest(args=args),self.assertRaises(ValueError): self.search(**args)
    def test_schema_and_context_expose_the_same_contract(self):
        shared=self.ctx
        class Delegate(Context):
            def __init__(self): self.db=shared.db;self.tables=shared.tables;self.reference=None
        direct=Delegate().call('find_projects_using',{'technology':'pydantic','offset':58,'limit':5})
        self.assertEqual((len(direct['results']),direct['has_more']),(2,False))
        session=initialize(Session(Delegate()))
        tool=next(t for t in session.handle({'jsonrpc':'2.0','id':2,'method':'tools/list'})['result']['tools'] if t['name']=='find_projects_using')
        self.assertIn('offset',tool['inputSchema']['properties']);self.assertIn('limit',tool['inputSchema']['properties'])
        response=session.handle(request('find_projects_using',{'technology':'pydantic','offset':50,'limit':10}))
        self.assertEqual(len(json.loads(response['result']['content'][0]['text'])['results']),10)

class PartialInventoryTests(FollowupCase):
    def test_reference_database_with_projects_only_is_explicit_not_opaque(self):
        fixture={'project_id':'synthetic-test','synthetic':True,'claims':[{'id':'privacy','statement':'Synthetic only','adaptation':'Check fields','source_path':'sample.txt','source_text':'allowlist'}],'unknowns':['Not live']}
        fixture_path=self.root/'fixture.json';fixture_path.write_text(json.dumps(fixture));partial=self.root/'partial.sqlite'
        load_demo(partial,fixture_path)
        db=sqlite3.connect(partial);pid='a'*64
        db.execute('CREATE TABLE projects(id TEXT PRIMARY KEY,name TEXT,root TEXT UNIQUE,commit_sha TEXT,scanned_at TEXT)')
        db.execute('INSERT INTO projects VALUES(?,?,?,?,?)',(pid,'legacy','/fixture/legacy',None,'old-time'));db.commit();db.close()
        before=partial.read_bytes();context=Context(partial);self.addCleanup(context.close)
        card=context.call('project_card',{'project_id':pid})
        self.assertEqual((card['status'],card['evidence'],card['dependencies']),('ok',[],[]))
        self.assertTrue(any('evidence, dependencies missing' in x and 'rescan required' in x for x in card['unknowns']))
        self.assertEqual(context.call('project_card',{'project_id':'synthetic-test'})['status'],'ok')
        self.assertEqual(before,partial.read_bytes())

class SavepointTests(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:');self.addCleanup(self.db.close);self.db.execute('CREATE TABLE t(v)');self.db.commit()
    def test_exception_rolls_back_partial_work_and_closes_savepoint(self):
        with self.assertRaises(RuntimeError):
            with snapshot(self.db):
                self.db.execute('INSERT INTO t VALUES(1)');raise RuntimeError('failure')
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM t').fetchone()[0],0);self.assertFalse(self.db.in_transaction)
    def test_success_still_releases_without_open_transaction(self):
        with snapshot(self.db): self.db.execute('INSERT INTO t VALUES(1)')
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM t').fetchone()[0],1);self.assertFalse(self.db.in_transaction)

class VersionAndShowTests(FollowupCase):
    def test_server_version_matches_package_and_metadata(self):
        project=tomllib.loads((ROOT/'pyproject.toml').read_text(encoding='utf-8'))['project']['version']
        self.assertEqual(project,__version__)
        self.write('requirements.txt','requests\n');cli.scan(self.db,self.repo);self.db.close()
        context=Context(self.path);self.addCleanup(context.close)
        response=Session(context).handle({'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2025-03-26','capabilities':{},'clientInfo':{'name':'t','version':'1'}}})
        self.assertEqual(response['result']['serverInfo']['version'],__version__)
    def test_show_includes_scan_coverage_for_current_and_empty_snapshots(self):
        self.write('requirements.txt','requests\n');result=cli.scan(self.db,self.repo)
        self.assertEqual(cli.show(self.db,result['project_id'])['scan_coverage'],result['coverage'])
        with self.db:
            self.db.execute('DELETE FROM scan_coverage')
        self.assertIsNone(cli.show(self.db,result['project_id'])['scan_coverage'])

if __name__=='__main__':unittest.main()
