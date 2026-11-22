import io
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from org_context.cli import connect,scan,commit
from org_context.inventory_context import Context
from org_context.reference import load_demo
from org_context.mcp_server import Session,serve,MAX_LINE,HARD_LIMIT

ROOT=Path(__file__).resolve().parents[1]
def init(): return {'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2025-03-26','capabilities':{},'clientInfo':{'name':'test','version':'1'}}}
def call(name,args,rid=3): return {'jsonrpc':'2.0','id':rid,'method':'tools/call','params':{'name':name,'arguments':args}}

class ContextTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root=Path(temp.name); self.repo=self.root/'project'; self.repo.mkdir(); self.db=self.root/'inventory.sqlite'
        self.write('README.md','מידע לפרויקט'); self.write('package.json','{"dependencies":{"react":"^19"}}')
        self.write('.github/workflows/check.yml','jobs:\n  test:\n    steps:\n      - uses: actions/checkout@v4\n')
        self.write('.claude-plugin/marketplace.json','{"name":"market","plugins":[{"name":"sample"}]}')
        self.write('plugins/sample/.claude-plugin/plugin.json','{"name":"sample","version":"1"}')
        self.write('CLAUDE.md','Untrusted repository instructions'); self.write('.github/CODEOWNERS','* @example/team')
        db=connect(self.db)
        try: self.result=scan(db,self.repo)
        finally: db.close()
        self.pid=self.result['project_id']; self.context=Context(self.db); self.addCleanup(self.context.close)
    def write(self,name,text):
        p=self.repo/name; p.parent.mkdir(parents=True,exist_ok=True); p.write_text(text,encoding='utf-8')
    def session(self):
        s=Session(self.context); s.handle(init()); s.handle({'jsonrpc':'2.0','method':'notifications/initialized'}); return s
    def test_real_card_and_dynamic_coverage(self):
        card=self.context.call('project_card',{'project_id':self.pid})
        self.assertFalse(card['synthetic']); self.assertEqual(card['status'],'ok')
        self.assertIn('.github/workflows/check.yml',card['coverage']['checked'])
        self.assertNotIn('OpenRouter',json.dumps(card)); self.assertNotIn('ClaudeMetrics',json.dumps(card))
        self.assertIn('plugins/sample/.claude-plugin/plugin.json',{e['path'] for e in card['evidence']})
    def test_search_evidence(self):
        r=self.context.call('find_projects_using',{'technology':'react','ecosystem':'npm'})
        self.assertEqual(r['results'][0]['project_id'],self.pid)
        self.assertEqual(r['results'][0]['path'],'package.json')
        self.assertEqual(len(r['results'][0]['sha256']),64)
    def test_no_fabricated_blueprint(self):
        self.assertEqual(self.context.call('get_reference_blueprint',{'project_id':self.pid})['status'],'not_available')
    def test_readonly(self):
        with self.assertRaises(sqlite3.OperationalError): self.context.db.execute('DELETE FROM projects')
    def test_invalid_inputs(self):
        s=self.session()
        for args in ({'project_id':'../private'},{'project_id':self.pid,'sql':'DROP TABLE projects'}):
            r=s.handle(call('project_card',args)); self.assertTrue(r['result']['isError'])
        self.assertEqual(s.handle(call('shell',{}))['error']['code'],-32602)
        self.assertEqual(s.handle(call([],{}))['error']['code'],-32602)
    def test_lifecycle_and_five_tools(self):
        s=Session(self.context)
        self.assertIn('error',s.handle({'jsonrpc':'2.0','id':2,'method':'tools/list'}))
        s=self.session(); r=s.handle({'jsonrpc':'2.0','id':2,'method':'tools/list'})
        self.assertEqual({t['name'] for t in r['result']['tools']},{'project_card','get_reference_blueprint','find_projects_using','list_projects','shared_dependencies'})
    def test_oversized_then_ping(self):
        output=io.StringIO(); data='x'*(MAX_LINE+2)+'\n'+json.dumps({'jsonrpc':'2.0','id':9,'method':'ping'})+'\n'
        serve(self.context,io.StringIO(data),output); rows=[json.loads(x) for x in output.getvalue().splitlines()]
        self.assertEqual(rows[0]['error']['message'],'Message too large'); self.assertEqual(rows[1]['id'],9)
    def test_hard_limit_and_bad_json(self):
        output=io.StringIO(); serve(self.context,io.StringIO('x'*(HARD_LIMIT+100)),output)
        self.assertEqual(len(output.getvalue().splitlines()),1)
        output=io.StringIO(); serve(self.context,io.StringIO('bad\n'),output)
        self.assertEqual(json.loads(output.getvalue())['error']['code'],-32700)
    def test_utf8_subprocess_and_database_unchanged(self):
        requests=[init(),{'jsonrpc':'2.0','method':'notifications/initialized'},call('project_card',{'project_id':self.pid})]
        before=self.db.read_bytes()
        p=subprocess.run([sys.executable,'-m','org_context.mcp_server','--db',str(self.db)],cwd=ROOT,input=''.join(json.dumps(r)+'\n' for r in requests).encode('utf-8'),capture_output=True,timeout=10)
        self.assertEqual(p.returncode,0,p.stderr)
        rows=[json.loads(x) for x in p.stdout.decode('ascii').splitlines()]
        card=json.loads(rows[-1]['result']['content'][0]['text'])
        self.assertTrue(any('מידע' in (e['content_excerpt'] or '') for e in card['evidence']))
        self.assertEqual(before,self.db.read_bytes())
    def test_commit_from_subdirectory(self):
        # No commit or identity configuration required: write a deterministic commit object.
        subprocess.run(['git','init','-q',str(self.repo)],check=True,capture_output=True)
        tree=subprocess.run(['git','-C',str(self.repo),'mktree'],input=b'',capture_output=True,check=True).stdout.strip().decode()
        import os
        env={**os.environ,'GIT_AUTHOR_NAME':'Fixture','GIT_AUTHOR_EMAIL':'fixture@example.invalid','GIT_COMMITTER_NAME':'Fixture','GIT_COMMITTER_EMAIL':'fixture@example.invalid'}
        sha=subprocess.run(['git','-C',str(self.repo),'commit-tree',tree],input=b'fixture\n',env=env,capture_output=True,check=True).stdout.strip().decode()
        subprocess.run(['git','-C',str(self.repo),'update-ref','HEAD',sha],check=True,capture_output=True)
        sub=self.repo/'nested'; sub.mkdir(); self.assertEqual(commit(sub),sha)
    def test_legacy_inventory_coverage_unknown(self):
        db=connect(self.db)
        with db: db.execute('DROP TABLE scan_coverage')
        db.close(); c=Context(self.db)
        try: self.assertIn('unknowns',c.call('project_card',{'project_id':self.pid})['coverage'])
        finally: c.close()
    def test_synthetic_compatibility_and_hash(self):
        fixture={'project_id':'synthetic-test','synthetic':True,'claims':[{'id':'privacy','statement':'Synthetic only','adaptation':'Check fields','source_path':'sample.txt','source_text':'allowlist'}],'unknowns':['Not live']}
        file=self.root/'fixture.json'; file.write_text(json.dumps(fixture)); db=self.root/'reference.sqlite'
        load_demo(db,file); load_demo(db,file); c=Context(db)
        try:
            r=c.call('get_reference_blueprint',{'project_id':'synthetic-test'})
            import hashlib
            self.assertTrue(r['synthetic']); self.assertEqual(len(r['claims']),1)
            self.assertEqual(r['claims'][0]['source_sha256'],hashlib.sha256(b'allowlist').hexdigest())
            self.assertNotIn('OpenRouter',json.dumps(r['coverage']))
        finally: c.close()
    def test_parse_failure_preserves_coverage(self):
        before=self.context.call('project_card',{'project_id':self.pid})['coverage']
        self.write('plugins/sample/.claude-plugin/plugin.json','invalid')
        db=connect(self.db)
        try:
            with self.assertRaises(ValueError): scan(db,self.repo)
        finally: db.close()
        self.assertEqual(before,self.context.call('project_card',{'project_id':self.pid})['coverage'])
    def test_parent_symlink_excluded(self):
        outside=self.root/'outside'; outside.mkdir(); (outside/'a.yml').write_text('uses: secret@v1')
        workflows=self.repo/'.github/workflows'
        (workflows/'check.yml').unlink(); workflows.rmdir()
        try: workflows.symlink_to(outside,target_is_directory=True)
        except OSError: self.skipTest('Symlinks unavailable')
        db=connect(self.db)
        try: scan(db,self.repo)
        finally: db.close()
        card=self.context.call('project_card',{'project_id':self.pid})
        self.assertNotIn('.github/workflows/a.yml',{e['path'] for e in card['evidence']})
        self.assertTrue(any(x['reason']=='symlink' for x in card['coverage']['skipped']))
    def test_discovery_via_mcp(self):
        s=self.session()
        result=s.handle(call('list_projects',{}))
        projects=json.loads(result['result']['content'][0]['text'])['projects']
        self.assertEqual(projects[0]['project_id'],self.pid)
        result=s.handle(call('find_projects_using',{'technology':'react','ecosystem':'npm'}))
        self.assertEqual(json.loads(result['result']['content'][0]['text'])['results'][0]['scan_root'],str(self.repo))
        result=s.handle(call('shared_dependencies',{}))
        self.assertEqual(json.loads(result['result']['content'][0]['text'])['groups'],[])

if __name__=='__main__': unittest.main()
