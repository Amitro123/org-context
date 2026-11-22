import json
from pathlib import Path
import tempfile
import unittest
from org_context.cli import connect,scan,show

class PythonCoverageTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.root=Path(temp.name);self.repo=self.root/'service';self.repo.mkdir()
        self.db=connect(':memory:');self.addCleanup(self.db.close)
    def write(self,name,text):
        p=self.repo/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(text,encoding='utf-8')
    def result(self): return show(self.db,'service')
    def test_requirements_variants_have_scope_and_evidence(self):
        self.write('requirements.txt','httpx>=0.27\n');self.write('requirements-dev.txt','pytest>=8\nmcp>=2.0\n')
        r=scan(self.db,self.repo);data=self.result();ev={e['path']:e['id'] for e in data['evidence']}
        self.assertIn('requirements-dev.txt',r['coverage']['checked'])
        found={(d['name'],d['scope']):d['evidence_id'] for d in data['dependencies']}
        self.assertEqual(found[('httpx','runtime')],ev['requirements.txt'])
        self.assertEqual(found[('mcp','requirements:dev')],ev['requirements-dev.txt'])
        self.assertNotIn(('mcp','runtime'),found)
    def test_dependency_groups_keep_group_scope(self):
        self.write('pyproject.toml','[project]\ndependencies=["pydantic>=2"]\n[dependency-groups]\nDev_Tools=["pytest>=8",{include-group="lint"}]\nlint=["ruff"]\n')
        r=scan(self.db,self.repo);found={(d['name'],d['scope']) for d in self.result()['dependencies']}
        self.assertEqual(found,{('pydantic','runtime'),('pytest','group:dev-tools'),('ruff','group:lint')})
        self.assertIn('dependency-group include',{s['structure'] for s in r['coverage']['partial_structures']})
    def test_omissions_are_counted_without_content(self):
        self.write('requirements.txt','-r base.txt\n--index-url https://secret@example.test\npkg @ https://example.test/x\nrequests>=2; python_version<"3.12"\nnot a requirement\nokay>=1 # comment\n')
        r=scan(self.db,self.repo);rows={x['reason']:x['count'] for x in r['coverage']['omitted_declarations']}
        self.assertEqual(rows,{'directive':2,'direct_reference':1,'environment_marker':1,'unsupported_expression':1})
        self.assertEqual([d['name'] for d in self.result()['dependencies']],['okay'])
        self.assertNotIn('secret',json.dumps(r['coverage']))
    def test_package_json_omissions_and_workspaces(self):
        self.write('package.json',json.dumps({'workspaces':['packages/*'],'dependencies':{'react':'^19','local':'file:../x','odd':True},'devDependencies':{'vitest':'^2'}}))
        r=scan(self.db,self.repo);cov=r['coverage']
        self.assertEqual({d['name'] for d in self.result()['dependencies']},{'react','vitest'})
        self.assertEqual({x['reason'] for x in cov['omitted_declarations']},{'direct_reference','unsupported_expression'})
        self.assertIn('npm workspaces',{x['structure'] for x in cov['partial_structures']})
    def test_uv_and_poetry_are_reported_not_ignored_silently(self):
        self.write('pyproject.toml','[tool.uv.workspace]\nmembers=["packages/*"]\n[tool.poetry]\nname="x"\n')
        structures={x['structure'] for x in scan(self.db,self.repo)['coverage']['partial_structures']}
        self.assertEqual(structures,{'uv workspace','tool.poetry'})
    def test_errors_name_file_without_source_content(self):
        self.write('pyproject.toml','SECRET_VALUE = = 1\n')
        with self.assertRaises(ValueError) as caught: scan(self.db,self.repo)
        message=str(caught.exception);self.assertIn('pyproject.toml',message);self.assertNotIn('SECRET_VALUE',message)
        (self.repo/'pyproject.toml').unlink();(self.repo/'requirements-dev.txt').write_bytes(b'\xff\xfe')
        with self.assertRaises(ValueError) as caught: scan(self.db,self.repo)
        self.assertIn('requirements-dev.txt',str(caught.exception))
    def test_malformed_group_shape_names_file_and_keeps_snapshot(self):
        self.write('requirements.txt','httpx\n');scan(self.db,self.repo)
        self.write('pyproject.toml','[dependency-groups]\ndev="not-a-list"\n')
        with self.assertRaises(ValueError) as caught: scan(self.db,self.repo)
        self.assertIn('pyproject.toml',str(caught.exception));self.assertEqual([d['name'] for d in self.result()['dependencies']],['httpx'])
    def test_requirements_discovery_is_root_only_and_symlink_safe(self):
        self.write('nested/requirements-dev.txt','hidden\n');target=self.root/'private.txt';target.write_text('private\n')
        try:(self.repo/'requirements-test.txt').symlink_to(target)
        except OSError:self.skipTest('Symlinks unavailable')
        r=scan(self.db,self.repo);self.assertEqual(self.result()['dependencies'],[])
        self.assertIn({'path':'requirements-test.txt','reason':'symlink'},r['skipped'])
    def test_pattern_and_absent_dependency_group_are_visible(self):
        r=scan(self.db,self.repo);self.assertIn('requirements-*.txt',r['coverage']['patterns_checked'])
        self.assertIn('dependency-group include expansion',r['coverage']['unsupported'])

if __name__=='__main__':unittest.main()
