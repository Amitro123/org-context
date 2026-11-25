"""Regression cases for the three safety findings; no network or external repos."""
import contextlib
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock
from org_context import cli
from org_context.discovery import call as discovery_call
from org_context.inventory_context import Context
from org_context.mcp_server import Session,serve,MAX_LINE,HARD_LIMIT

ROOT=Path(__file__).resolve().parents[1]
MARKER='RETAINED_BODY_SENTINEL_5f0c'
README_BODY=MARKER+' מידע\n'
README_BYTES=README_BODY.encode('utf-8')
LEGACY_SCHEMA='\n    CREATE TABLE IF NOT EXISTS projects(id TEXT PRIMARY KEY,name TEXT,root TEXT UNIQUE,commit_sha TEXT,scanned_at TEXT);\n    CREATE TABLE IF NOT EXISTS evidence(id TEXT PRIMARY KEY,project_id TEXT REFERENCES projects(id),path TEXT,sha256 TEXT,kind TEXT,method TEXT,content TEXT);\n    CREATE TABLE IF NOT EXISTS dependencies(project_id TEXT REFERENCES projects(id),ecosystem TEXT,name TEXT,specification TEXT,scope TEXT,evidence_id TEXT REFERENCES evidence(id),UNIQUE(project_id,ecosystem,name,scope,evidence_id));\n    CREATE TABLE IF NOT EXISTS scan_coverage(project_id TEXT PRIMARY KEY REFERENCES projects(id),payload TEXT NOT NULL);\n    '

def request(name,args,rid=3):
    return {'jsonrpc':'2.0','id':rid,'method':'tools/call','params':{'name':name,'arguments':args}}

def initialize(session):
    session.handle({'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2025-03-26','capabilities':{},'clientInfo':{'name':'safety-test','version':'1'}}})
    session.handle({'jsonrpc':'2.0','method':'notifications/initialized'})
    return session

def ping(rid):
    return (json.dumps({'jsonrpc':'2.0','id':rid,'method':'ping'})+'\n').encode('utf-8')

class SafetyCase(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.root=Path(temp.name);self.repo=self.root/'service';self.repo.mkdir()
        self.path=self.root/'inventory.sqlite'
        self.db=cli.connect(self.path);self.addCleanup(self.db.close)
    def write(self,name,text):
        p=self.repo/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(text,encoding='utf-8')
    def context(self):
        c=Context(self.path);self.addCleanup(c.close);return c
    def legacy(self):
        self.db.close();self.path.unlink()
        db=sqlite3.connect(self.path);db.row_factory=sqlite3.Row
        db.executescript(LEGACY_SCHEMA);db.execute('PRAGMA foreign_keys=ON')
        self.addCleanup(db.close);self.db=db
        for name in ('service','other'):
            root=self.root/name;root.mkdir(exist_ok=True);pid=cli.digest(str(root.resolve()));eid=cli.digest(name)
            db.execute('INSERT INTO projects VALUES(?,?,?,?,?)',(pid,name,str(root.resolve()),None,'old-time'))
            db.execute('INSERT INTO evidence VALUES(?,?,?,?,?,?,?)',(eid,pid,'requirements.txt','a'*64,'manifest','structured_parser',None))
            db.execute('INSERT INTO dependencies VALUES(?,?,?,?,?,?)',(pid,'python','requests','>=2','runtime',eid))
        db.commit()
        return db
    def key(self):
        return self.db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='dependencies'").fetchone()[0]

class SpecIntegrityTests(SafetyCase):
    def test_distinct_specs_from_same_file_are_kept(self):
        self.write('requirements.txt','requests>=2\nrequests<3\n')
        r=cli.scan(self.db,self.repo);rows=cli.show(self.db,r['project_id'])['dependencies']
        self.assertEqual({x['specification'] for x in rows},{'>=2','<3'})
        self.assertEqual((r['dependency_count'],r['declaration_count']),(2,2))
        self.assertEqual(len({x['evidence_id'] for x in rows}),1)
        self.assertEqual(r['coverage']['omitted_declarations'],[])
    def test_groups_preserve_specs_and_scopes(self):
        self.write('pyproject.toml','[project]\ndependencies=["requests>=2","requests<3"]\n[dependency-groups]\ndev=["requests>=2","requests<3"]\n')
        r=cli.scan(self.db,self.repo);rows=cli.show(self.db,r['project_id'])['dependencies']
        self.assertEqual({(x['scope'],x['specification']) for x in rows},{('runtime','>=2'),('runtime','<3'),('group:dev','>=2'),('group:dev','<3')})
    def test_exact_duplicates_are_explicit_and_counts_are_stored_counts(self):
        self.write('requirements.txt','requests>=2\nrequests<3\nRequests>=2\n')
        r=cli.scan(self.db,self.repo)
        self.assertEqual((r['dependency_count'],r['declaration_count']),(2,3))
        self.assertIn({'path':'requirements.txt','reason':'duplicate_declaration','count':1},r['coverage']['omitted_declarations'])
        self.assertNotIn('>=2',json.dumps(r['coverage']))
    def test_separate_files_keep_separate_evidence(self):
        self.write('requirements.txt','requests>=2\n');self.write('requirements-dev.txt','requests>=2\n')
        r=cli.scan(self.db,self.repo);rows=cli.show(self.db,r['project_id'])['dependencies']
        self.assertEqual(len(rows),2);self.assertEqual(len({x['evidence_id'] for x in rows}),2)
    def test_legacy_migration_preserves_other_roots(self):
        self.legacy();self.write('requirements.txt','requests>=2\nrequests<3\n')
        other=cli.digest(str((self.root/'other').resolve()));before=cli.show(self.db,other)
        r=cli.scan(self.db,self.repo)
        self.assertEqual(cli.show(self.db,other),before)
        self.assertEqual(r['dependency_count'],2);self.assertEqual(self.db.execute('PRAGMA user_version').fetchone()[0],1)
        self.assertEqual(self.db.execute('PRAGMA foreign_key_check').fetchall(),[])
        self.assertIn('specification)',self.key())
        self.assertEqual(r['project_id'],cli.digest(str(self.repo.resolve())))
        c=self.context();card=c.call('project_card',{'project_id':other})
        self.assertTrue(any('rescan required' in x for x in card['unknowns']))
    def test_migration_failure_rolls_back_schema_and_rows(self):
        self.legacy();self.write('requirements.txt','requests>=2\nrequests<3\n')
        before=self.key();rows=[tuple(r) for r in self.db.execute('SELECT * FROM dependencies')]
        migrate=cli._migrate_dependency_specs
        def fail(db):
            migrate(db);raise RuntimeError('injected migration failure')
        with mock.patch('org_context.cli._migrate_dependency_specs',side_effect=fail):
            with self.assertRaises(RuntimeError):cli.scan(self.db,self.repo)
        self.assertEqual(self.key(),before)
        self.assertEqual([tuple(r) for r in self.db.execute('SELECT * FROM dependencies')],rows)
        self.assertEqual(self.db.execute('PRAGMA user_version').fetchone()[0],0)
        self.assertFalse(self.db.in_transaction)
    def test_bad_source_does_not_migrate_or_replace_snapshot(self):
        self.legacy();self.write('pyproject.toml','SECRET = = 1\n')
        before=self.path.read_bytes()
        with self.assertRaises(ValueError):cli.scan(self.db,self.repo)
        self.assertEqual(self.path.read_bytes(),before);self.assertEqual(self.db.execute('PRAGMA user_version').fetchone()[0],0)
    def test_reads_do_not_migrate_legacy_inventory(self):
        self.legacy();before=self.path.read_bytes();pid=cli.digest(str(self.repo.resolve()))
        c=self.context();c.call('project_card',{'project_id':pid})
        for args in (['list'],['show',pid],['search','--technology','requests'],['export']):
            with contextlib.redirect_stdout(io.StringIO()):self.assertEqual(cli.main(['--db',str(self.path),*args]),0)
        self.assertEqual(before,self.path.read_bytes());self.assertEqual(self.db.execute('PRAGMA user_version').fetchone()[0],0)
    def test_future_schema_refused_without_writes(self):
        self.db.execute('PRAGMA user_version=99');self.write('requirements.txt','requests\n');before=self.path.read_bytes()
        with self.assertRaisesRegex(ValueError,'Unsupported inventory schema version'):cli.scan(self.db,self.repo)
        self.assertEqual(self.path.read_bytes(),before)
    def test_rescan_and_shared_counts(self):
        self.write('requirements.txt','requests>=2\nrequests<3\n')
        a=cli.scan(self.db,self.repo);cli.scan(self.db,self.repo)
        other=self.root/'second';other.mkdir();(other/'requirements.txt').write_text('requests>=2\nrequests<3\n')
        cli.scan(self.db,other);c=self.context()
        group=discovery_call(c,'shared_dependencies',{'ecosystem':'python'})['groups'][0]
        self.assertEqual((group['project_count'],group['specification_count'],len(group['declarations'])),(2,2,4))
        self.assertEqual(len(cli.show(self.db,a['project_id'])['dependencies']),2)

class ContentGateTests(SafetyCase):
    def prepare(self):
        # Use identical fixture bytes on Windows and POSIX; retention preserves them.
        (self.repo/'README.md').write_bytes(README_BYTES)
        self.write('CLAUDE.md',MARKER+' policy\n');self.write('CODEOWNERS',MARKER+' owner\n')
        self.write('.github/workflows/check.yml','- uses: fixture/'+MARKER+'@v1\n')
        self.write('.claude-plugin/plugin.json',json.dumps({'name':MARKER}))
        self.write('requirements.txt','requests\n')
        return cli.scan(self.db,self.repo)['project_id']
    def card(self,c,pid,enabled=False):
        s=initialize(Session(c,evidence_pages=enabled))
        result=s.handle(request('project_card',{'project_id':pid}))
        self.assertFalse(result['result']['isError'])
        return s,json.loads(result['result']['content'][0]['text'])
    def test_default_direct_card_withholds_all_retained_classes(self):
        pid=self.prepare();card=self.context().call('project_card',{'project_id':pid})
        self.assertNotIn(MARKER,json.dumps(card));self.assertEqual(len(card['evidence']),6)
        for e in card['evidence']:self.assertIsNone(e['content_excerpt']);self.assertNotIn('content',e)
        readme=next(e for e in card['evidence'] if e['path']=='README.md')
        self.assertTrue(readme['has_retained_body']);self.assertTrue(readme['content_withheld'])
        self.assertEqual(readme['retained_bytes'],len(README_BYTES))
    def test_direct_opt_in_is_bounded_and_not_sticky(self):
        pid=self.prepare();self.write('README.md',MARKER+' מידע'*700);cli.scan(self.db,self.repo);c=self.context()
        card=c.call('project_card',{'project_id':pid},include_content=True)
        e=next(e for e in card['evidence'] if e['path']=='README.md')
        self.assertEqual(len(e['content_excerpt']),1500);self.assertTrue(e['excerpt_truncated'])
        self.assertFalse(e['content_withheld']);self.assertNotIn(MARKER,json.dumps(c.call('project_card',{'project_id':pid})))
    def test_default_session_has_no_body_or_read_tool(self):
        pid=self.prepare();s,card=self.card(self.context(),pid)
        self.assertNotIn(MARKER,json.dumps(card))
        tools=s.handle({'jsonrpc':'2.0','id':7,'method':'tools/list'})['result']['tools'];self.assertEqual(len(tools),6)
        eid=card['evidence'][0]['id']
        self.assertEqual(s.handle(request('read_evidence',{'project_id':pid,'evidence_id':eid}))['error']['code'],-32602)
    def test_opt_in_session_reads_body_and_default_session_stays_private(self):
        pid=self.prepare();c=self.context();before=self.path.read_bytes();s,card=self.card(c,pid,True)
        self.assertIn(MARKER,json.dumps(card));self.assertEqual(len(s.tools),7)
        eid=next(e['id'] for e in card['evidence'] if e['path']=='README.md')
        response=s.handle(request('read_evidence',{'project_id':pid,'evidence_id':eid}))
        body=json.loads(response['result']['content'][0]['text'])['content']
        self.assertEqual(body,README_BODY)
        self.assertNotIn(MARKER,json.dumps(self.card(c,pid,False)[1]));self.assertEqual(before,self.path.read_bytes())
    def test_arguments_cannot_enable_content(self):
        pid=self.prepare();s=initialize(Session(self.context()))
        for key in ('include_content','evidence_pages','content'):
            r=s.handle(request('project_card',{'project_id':pid,key:True}))
            self.assertTrue(r['result']['isError']);self.assertNotIn(MARKER,json.dumps(r))
    def test_export_requires_explicit_local_flag(self):
        self.prepare();before=self.path.read_bytes()
        self.assertNotIn(MARKER,json.dumps(cli.graph(self.db)))
        self.assertIn(MARKER,json.dumps(cli.graph(self.db,include_content=True)))
        for flags,expected_body in (([],False),(['--include-content'],True)):
            output=io.StringIO()
            with contextlib.redirect_stdout(output):self.assertEqual(cli.main(['--db',str(self.path),'export',*flags]),0)
            graph=json.loads(output.getvalue());self.assertEqual(MARKER in json.dumps(graph),expected_body)
            for node in graph['nodes']:
                if node['type']=='Evidence':self.assertEqual('content' in node,expected_body)
        self.assertEqual(before,self.path.read_bytes())
    def test_default_and_opt_in_through_actual_stdio(self):
        pid=self.prepare()
        init={'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2025-03-26','capabilities':{},'clientInfo':{'name':'test','version':'1'}}}
        payload=''.join(json.dumps(x)+'\n' for x in [init,{'jsonrpc':'2.0','method':'notifications/initialized'},request('project_card',{'project_id':pid})]).encode('utf-8')
        for flags,body in (([],False),(['--evidence-pages'],True)):
            p=subprocess.run([sys.executable,'-m','org_context.mcp_server','--db',str(self.path),*flags],cwd=ROOT,input=payload,capture_output=True,timeout=5)
            self.assertEqual(p.returncode,0,p.stderr);self.assertEqual(MARKER.encode() in p.stdout,body)
    def test_null_body_and_empty_body_are_distinct(self):
        self.write('README.md','');self.write('requirements.txt','requests\n')
        pid=cli.scan(self.db,self.repo)['project_id'];rows=self.context().call('project_card',{'project_id':pid})['evidence']
        bypath={r['path']:r for r in rows}
        self.assertTrue(bypath['README.md']['has_retained_body']);self.assertFalse(bypath['requirements.txt']['has_retained_body'])
        self.assertEqual(bypath['README.md']['retained_bytes'],0)

class Utf8FramingTests(SafetyCase):
    def ctx(self):
        cli.scan(self.db,self.repo);return self.context()
    def read(self,source):
        output=io.StringIO();serve(self.ctx(),source,output)
        return [json.loads(x) for x in output.getvalue().splitlines()]
    def assert_recovered(self,rows):
        self.assertEqual([x['id'] for x in rows],[1,None,2])
        self.assertEqual(rows[1]['error']['code'],-32700)
    def test_binary_valid_invalid_valid(self):
        self.assert_recovered(self.read(io.BytesIO(ping(1)+b'\xff\n'+ping(2))))
    def test_text_wrapper_does_not_decode_ahead(self):
        source=io.TextIOWrapper(io.BytesIO(ping(1)+b'\xff\n'+ping(2)),encoding='utf-8',errors='strict')
        self.addCleanup(source.close);self.assert_recovered(self.read(source))
    def test_real_subprocess_recovers_without_stderr(self):
        self.ctx();p=subprocess.run([sys.executable,'-m','org_context.mcp_server','--db',str(self.path)],cwd=ROOT,input=ping(1)+b'\xff\n'+ping(2),capture_output=True,timeout=5)
        self.assertEqual(p.returncode,0,p.stderr);self.assertEqual(p.stderr,b'')
        self.assert_recovered([json.loads(x) for x in p.stdout.splitlines()])
    def test_invalid_multibyte_sequence_is_rejected_not_replaced(self):
        bad=b'{"jsonrpc":"2.0","id":99,"method":"ping","params":{"x":"\xc3"}}\n'
        self.assert_recovered(self.read(io.BytesIO(ping(1)+bad+ping(2))))
    def test_repeated_bad_frames_do_not_loop_or_hide_later_ping(self):
        rows=self.read(io.BytesIO(b'\xff\n\xfe\n'+ping(2)))
        self.assertEqual([r.get('error',{}).get('code') for r in rows],[-32700,-32700,None]);self.assertEqual(rows[-1]['id'],2)
    def test_oversize_bad_bytes_drain_then_ping(self):
        rows=self.read(io.BytesIO(b'\xff'*(MAX_LINE+5)+b'\n'+ping(2)))
        self.assertEqual(rows[0]['error']['code'],-32600);self.assertEqual(rows[1]['id'],2)
    def test_hard_limit_closes_at_bound(self):
        source=io.BytesIO(b'x'*(HARD_LIMIT+100)+b'\n'+ping(2));rows=self.read(source)
        self.assertEqual(len(rows),1);self.assertLessEqual(source.tell(),HARD_LIMIT+MAX_LINE+1)
    def test_bad_byte_at_eof(self):
        rows=self.read(io.BytesIO(b'\xff'));self.assertEqual(len(rows),1);self.assertEqual(rows[0]['error']['code'],-32700)
    def test_byte_not_character_limit(self):
        big=(json.dumps({'jsonrpc':'2.0','id':1,'method':'ping','params':{'x':'א'*9000}},ensure_ascii=False)+'\n').encode('utf-8')
        self.assertGreater(len(big),MAX_LINE)
        rows=self.read(io.BytesIO(big+ping(2)));self.assertEqual(rows[0]['error']['code'],-32600);self.assertEqual(rows[1]['id'],2)
    def test_text_only_decode_error_fails_closed(self):
        class BrokenText:
            def readline(self,*_):raise UnicodeDecodeError('utf-8',b'\xff',0,1,'invalid')
        rows=self.read(BrokenText());self.assertEqual(len(rows),1);self.assertEqual(rows[0]['error']['code'],-32700)

if __name__=='__main__':unittest.main()
