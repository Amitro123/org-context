"""CI install commands in workflow step shell text become ci-install:<path> declarations."""
from collections import Counter
import json
from pathlib import Path
import tempfile
import unittest
from org_context import ci_installs, cli
from org_context.ci_installs import ci_install_declarations
from org_context.inventory_context import Context
from org_context.workflow_refs import pipeline_metadata

GH='.github/workflows/ci.yml'
SCOPE='ci-install:'+GH

def extract(text,flavor='github'):
    notes=Counter()
    return ci_install_declarations(text,flavor,'ci-install:x',notes),notes

def names(rows):
    return [(r[0],r[1],r[2]) for r in rows]

def steps(*runs):
    return 'jobs:\n  t:\n    steps:\n'+''.join('      - run: %s\n'%r for r in runs)

class ExtractorTests(unittest.TestCase):
    def test_command_forms(self):
        rows,_=extract(steps('pipx install black','uv pip install ruff','uv tool install httpie','pip3 install x','python3 -m pip install y','python -m pip install z','pip install w','npm install q','npm i r'))
        self.assertEqual(names(rows),[('python','black',''),('python','ruff',''),('python','httpie',''),('python','x',''),('python','y',''),('python','z',''),('python','w',''),('npm','q',''),('npm','r','')])
        self.assertTrue(all(r[3]=='ci-install:x' for r in rows))

    def test_specifier_forms(self):
        rows,_=extract(steps('python -m pip install "pytest==8.3"','npm install -g @anthropic-ai/claude-code@2.1.312','pip install Foo_Bar[extra]>=1','npx -y cowsay@1.5 hello','npm i --global Pkg@^1.2'))
        self.assertEqual(names(rows),[('python','pytest','==8.3'),('npm','@anthropic-ai/claude-code','2.1.312'),('python','foo-bar','>=1'),('npm','cowsay','1.5'),('npm','pkg','^1.2')])

    def test_npx_forms(self):
        rows,notes=extract(steps('npx -p tsx@4 tsx script.ts','npx -- cowsay hi','npx -c echo','npx --package=lint-x lint-x','npx --yes prettier --write .'))
        self.assertEqual(names(rows),[('npm','tsx','4'),('npm','lint-x',''),('npm','prettier','')])
        self.assertEqual(sum(notes.values()),0)

    def test_option_values_are_consumed(self):
        rows,notes=extract(steps('pip install -U --target out --index-url https://h/simple -q pkg --no-cache-dir','npm i -g --registry https://r/ tool','uv tool install --with extra-pkg --python 3.12 tool2','pipx install --python python3.12 tool3'))
        self.assertEqual(names(rows),[('python','pkg',''),('npm','tool',''),('python','tool2',''),('python','tool3','')])
        self.assertEqual(sum(notes.values()),0)

    def test_skip_and_count_reasons(self):
        rows,notes=extract(steps('pip install ${{ matrix.pkg }}','pip install -r req.txt','pip install ./local','pip install $PKG','pip install https://h/x.whl','pip install git+https://h/r.git','pip install -e .','pip install --requirement=a.txt -c c.txt','npm i file:../x','pip install "pkg; python_version<3"','pip install pk*g','npm i "@bad name"','npm i x@"y'))
        self.assertEqual(rows,[])
        self.assertEqual(dict(notes),{'ci_install_dynamic':2,'ci_install_requirements_file':3,'ci_install_local_or_url':5,'ci_install_unsupported':4})

    def test_no_package_installs_store_nothing(self):
        rows,notes=extract(steps('npm install','npm ci','pip install --upgrade pip','echo pip install','yarn add x','uvx ruff','npm add y','poetry install'))
        self.assertEqual(names(rows),[('python','pip','')])
        self.assertEqual(sum(notes.values()),0)

    def test_segments_sudo_env_and_comments(self):
        rows,_=extract(steps('sudo -H true && sudo pip install a; CI=1 FOO=bar npm i b || echo x | pip install c # pip install no','"pip install d"',"'npm i e' # c"))
        self.assertEqual([r[1] for r in rows],['a','b','c','d','e'])

    def test_block_scalars_and_continuations(self):
        text=('jobs:\n  t:\n    steps:\n      - run: |\n          echo hi\n          pip install a \\\n            b==2\n\n'
              '          # pip install commented\n          run: pip install z\n      - name: pip install notshell\n'
              '        run: >-\n          npm i folded\n      - uses: x/y@v1\n        with:\n          run: pip install inner\n'
              '    defaults:\n      run:\n        shell: bash\n')
        rows,_=extract(text)
        self.assertEqual(names(rows),[('python','a',''),('python','b','==2'),('python','z',''),('npm','folded',''),('python','inner','')])

    def test_flavor_keys(self):
        text='steps:\n- script: pip install a\n- bash: npm i b\n- pwsh: pip install c\n- run: pip install d\n'
        self.assertEqual([r[1] for r in extract(text,'azure')[0]],['a','b','c'])
        self.assertEqual([r[1] for r in extract(text,'github')[0]],['d'])

    def test_limits(self):
        rows,notes=extract(steps(*('pip install p%d'%i for i in range(60))))
        self.assertEqual(len(rows),50);self.assertEqual(notes,Counter({'ci_install_over_limit':10}))
        rows,notes=extract(steps('pip install x '+'a'*5000,'echo '+'b'*5000))
        self.assertEqual(rows,[]);self.assertEqual(notes,Counter({'ci_install_unsupported':1}))
        long_name,long_spec=extract(steps('pip install '+'n'*101,'npm i x@'+'1'*81))
        self.assertEqual(long_name,[]);self.assertEqual(long_spec['ci_install_unsupported'],2)
        many='jobs:\n  t:\n    steps:\n      - run: |\n          pip install a'+' \\\n           x'*30+'\n'
        rows,_=extract(many)
        self.assertLessEqual(len(rows),31);self.assertEqual(rows[0][1],'a')

    def test_never_raises(self):
        for text in ('','run:','run: |','run: "','- run: \'pip install','run: pip install "a b"','\x00run: pip\x00 install','run: '+'\\'*10000,'run: |\n'+'  pip install \\\n'*100,'run: pip install [x]','run: npm i @','run: npx -p'):
            rows,_=extract(text)
            self.assertIsInstance(rows,list)

    def test_module_executes_nothing(self):
        source=Path(ci_installs.__file__).read_text(encoding='utf-8')
        for word in ('subprocess','os.system','import os','eval(','exec(','open('):
            self.assertNotIn(word,source)

class ScanCase(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.root=Path(temp.name);self.repo=self.root/'service';self.repo.mkdir()
        self.path=self.root/'inventory.sqlite';self.db=cli.connect(self.path);self.addCleanup(self.db.close)
    def write(self,name,text):
        p=self.repo/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(text,encoding='utf-8')
    def find(self,technology):
        context=Context(self.path);self.addCleanup(context.close)
        return context.call('find_projects_using',{'technology':technology})['results']
    def omitted(self,result,path):
        return {x['reason']:x['count'] for x in result['coverage']['omitted_declarations'] if x['path']==path}
    def dump(self):
        return json.dumps([[list(r) for r in self.db.execute('SELECT * FROM '+t)] for t in ('dependencies','evidence','scan_coverage')])

class ScanTests(ScanCase):
    def test_find_projects_using_github_ci_installs(self):
        self.write(GH,steps('pip install pytest','npm i -g pkg@1.2.3'))
        result=cli.scan(self.db,self.repo)
        rows=self.find('pytest')
        self.assertEqual(len(rows),1)
        self.assertEqual((rows[0]['project_id'],rows[0]['ecosystem'],rows[0]['scope'],rows[0]['specification'],rows[0]['path']),(result['project_id'],'python',SCOPE,'',GH))
        rows=self.find('pkg')
        self.assertEqual([(r['ecosystem'],r['scope'],r['specification']) for r in rows],[('npm',SCOPE,'1.2.3')])
        self.assertIn('CI script execution and indirect installs',result['coverage']['unsupported'])

    def test_specifiers_end_to_end(self):
        self.write(GH,steps('python -m pip install "pytest==8.3"','npm install -g @anthropic-ai/claude-code@2.1.312','pip install Foo_Bar[extra]>=1','npx -y cowsay@1.5 hello'))
        cli.scan(self.db,self.repo)
        self.assertEqual([r['specification'] for r in self.find('pytest')],['==8.3'])
        self.assertEqual([r['specification'] for r in self.find('@anthropic-ai/claude-code')],['2.1.312'])
        self.assertEqual([(r['name'],r['specification']) for r in self.find('Foo_Bar')],[('foo-bar','>=1')])
        self.assertEqual([r['specification'] for r in self.find('cowsay')],['1.5'])
        self.assertEqual(self.find('hello'),[])

    def test_python_command_variants_end_to_end(self):
        self.write(GH,steps('pipx install black','uv pip install ruff','uv tool install httpie','pip3 install x','python3 -m pip install y'))
        cli.scan(self.db,self.repo)
        for tech in ('black','ruff','httpie','x','y'):
            self.assertEqual([(r['ecosystem'],r['scope']) for r in self.find(tech)],[('python',SCOPE)],tech)

    def test_dynamic_requirements_and_local_are_counted(self):
        self.write(GH,steps('pip install ${{ matrix.pkg }}','pip install -r req.txt','pip install ./local'))
        result=cli.scan(self.db,self.repo)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM dependencies').fetchone()[0],0)
        self.assertEqual(self.omitted(result,GH),{'ci_install_dynamic':1,'ci_install_requirements_file':1,'ci_install_local_or_url':1})
        self.write(GH,steps('pip install $PKG','pip install https://h/p.whl','pip install git+https://h/r.git'))
        result=cli.scan(self.db,self.repo)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM dependencies').fetchone()[0],0)
        self.assertEqual(self.omitted(result,GH),{'ci_install_dynamic':1,'ci_install_local_or_url':2})

    def test_block_body_end_to_end(self):
        self.write(GH,'jobs:\n  t:\n    steps:\n      - run: |\n          echo hi\n          pip install a \\\n            b==2\n          run: pip install z\n      - name: pip install notshell\n')
        cli.scan(self.db,self.repo)
        got=sorted((r[0],r[1]) for r in self.db.execute('SELECT name,specification FROM dependencies'))
        self.assertEqual(got,[('a',''),('b','==2'),('z','')])

    def test_nothing_is_executed(self):
        marker=self.repo/'MARKER'
        self.write(GH,steps('pip install evil; touch MARKER','pip install evil2 && touch "%s"'%marker))
        cli.scan(self.db,self.repo)
        self.assertFalse(marker.exists());self.assertFalse((self.root/'MARKER').exists())
        self.assertEqual([r['name'] for r in self.find('evil')],['evil'])

    def test_workflow_evidence_content_unchanged(self):
        self.write(GH,'jobs:\n  t:\n    steps:\n      - uses: actions/checkout@v4\n      - run: pip install pytest\n')
        result=cli.scan(self.db,self.repo)
        content=json.loads(next(e for e in cli.show(self.db,result['project_id'])['evidence'] if e['kind']=='workflow')['content'])
        self.assertEqual(set(content),{'declared_action_references','semantics'})
        self.assertEqual(content['declared_action_references'],['actions/checkout@v4'])

AZURE="""trigger:
- main
resources:
  repositories:
  - repository: templates
    type: git
    name: Shared/templates
steps:
- template: steps/build.yml@templates
- script: pip install pytest
  displayName: test
- bash: |
    echo build
    npm i -g pkg@2
- pwsh: python -m pip install tox
- task: Thing@1
defaults:
  run:
    shell: bash
"""

class AzureTests(ScanCase):
    def test_azure_steps_and_pipeline_metadata_unchanged(self):
        self.write('azure-pipelines.yml',AZURE)
        result=cli.scan(self.db,self.repo)
        got=sorted(tuple(r) for r in self.db.execute('SELECT ecosystem,name,specification,scope FROM dependencies'))
        scope='ci-install:azure-pipelines.yml'
        self.assertEqual(got,[('npm','pkg','2',scope),('python','pytest','',scope),('python','tox','',scope)])
        # The same pipeline without any install commands yields byte-identical metadata.
        plain=AZURE.replace('pip install pytest','echo a').replace('npm i -g pkg@2','echo b').replace('python -m pip install tox','echo c')
        self.assertEqual(result['coverage']['workflow_references_by_path']['azure-pipelines.yml'],pipeline_metadata(AZURE))
        self.assertEqual(json.dumps(pipeline_metadata(AZURE),sort_keys=True),json.dumps(pipeline_metadata(plain),sort_keys=True))
        self.assertEqual(self.omitted(result,'azure-pipelines.yml'),{})

    def test_empty_run_mapping_stores_nothing(self):
        self.write('azure-pipelines.yml','defaults:\n  run:\n    shell: bash\nsteps:\n- script:\n')
        cli.scan(self.db,self.repo)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM dependencies').fetchone()[0],0)

class HygieneAndLimitTests(ScanCase):
    def test_secrets_never_stored(self):
        self.write(GH,steps('pip install --index-url https://u:SENTINEL1@h/simple pkg','pip install $SENTINEL2','pip install ${{ secrets.SENTINEL3 }}','pip install https://u:SENTINEL4@h/x.whl','npm i --registry=https://SENTINEL5@r/ tool'))
        result=cli.scan(self.db,self.repo)
        self.assertEqual([r['name'] for r in self.find('pkg')],['pkg'])
        context=Context(self.path);self.addCleanup(context.close)
        outputs=[self.dump(),json.dumps(result),json.dumps(context.call('project_card',{'project_id':result['project_id']})),json.dumps(self.find('pkg')),json.dumps(self.find('tool'))]
        for text in outputs:
            for i in range(1,6): self.assertNotIn('SENTINEL%d'%i,text)

    def test_limits_and_duplicates(self):
        self.write(GH,steps(*('pip install p%d'%i for i in range(60))))
        result=cli.scan(self.db,self.repo)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM dependencies').fetchone()[0],50)
        self.assertEqual(self.omitted(result,GH),{'ci_install_over_limit':10})
        self.write(GH,steps('pip install x '+'a'*5000))
        result=cli.scan(self.db,self.repo)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM dependencies').fetchone()[0],0)
        self.assertEqual(self.omitted(result,GH),{'ci_install_unsupported':1})
        self.write(GH,steps('pip install pytest','pip install pytest','pip install pytest'))
        result=cli.scan(self.db,self.repo)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM dependencies').fetchone()[0],1)
        self.assertEqual(self.omitted(result,GH),{'duplicate_declaration':2})

    def test_root_requirements_unchanged_beside_ci_installs(self):
        self.write('requirements.txt','pytest>=8\n')
        self.write(GH,steps('pip install pytest'))
        cli.scan(self.db,self.repo)
        self.assertEqual(sorted((r['scope'],r['specification']) for r in self.find('pytest')),[('ci-install:'+GH,''),('runtime','>=8')])

if __name__=='__main__':
    unittest.main()
