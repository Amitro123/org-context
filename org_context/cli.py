"""Bounded local repository inventory; source content is untrusted data."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import subprocess
import tomllib
from .capabilities import MANIFEST_FILENAME, parse_capability_manifest
from . import skill_frontmatter
from .workflow_refs import pipeline_metadata

ALLOWED=('README','README.md','README.rst','README.txt','pyproject.toml','requirements.txt','package.json','CLAUDE.md','CODEOWNERS','.github/CODEOWNERS','docs/CODEOWNERS','.claude-plugin/plugin.json','.claude-plugin/marketplace.json',MANIFEST_FILENAME)
PIPELINE_PATTERNS=('azure-pipelines.yml','azure-pipelines.yaml','azure-pipelines-*.yml','azure-pipelines-*.yaml')
PATTERNS=('requirements-*.txt','.github/workflows/*.yml','.github/workflows/*.yaml','plugins/*/.claude-plugin/plugin.json','plugins/*/plugin.json',*PIPELINE_PATTERNS)
UNSUPPORTED=('lockfiles','nested package discovery','workspace member discovery','full YAML semantics','CODEOWNERS matching semantics','OpenAPI/IaC analysis','requirements include expansion','environment-marker evaluation','dependency-group include expansion')
MAX_BYTES=256000
MAX_FILES=128
SPEC=re.compile(r'([A-Za-z0-9][A-Za-z0-9_.-]*)(?:\[[A-Za-z0-9_,.-]+\])?\s*([<>=!~][A-Za-z0-9.*<>=!~,+ -]+)?')

class SourceFormatError(ValueError):
    """Safe, content-free error raised for malformed source declarations."""

def digest(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()

def connect(path):
    db=sqlite3.connect(path); db.row_factory=sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    existing={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    db.executescript("""
    CREATE TABLE IF NOT EXISTS projects(id TEXT PRIMARY KEY,name TEXT,root TEXT UNIQUE,commit_sha TEXT,scanned_at TEXT);
    CREATE TABLE IF NOT EXISTS evidence(id TEXT PRIMARY KEY,project_id TEXT REFERENCES projects(id),path TEXT,sha256 TEXT,kind TEXT,method TEXT,content TEXT);
    CREATE TABLE IF NOT EXISTS dependencies(project_id TEXT REFERENCES projects(id),ecosystem TEXT,name TEXT,specification TEXT,scope TEXT,evidence_id TEXT REFERENCES evidence(id),UNIQUE(project_id,ecosystem,name,scope,evidence_id,specification));
    CREATE TABLE IF NOT EXISTS scan_coverage(project_id TEXT PRIMARY KEY REFERENCES projects(id),payload TEXT NOT NULL);
    """)
    if not existing:
        db.execute("CREATE TABLE IF NOT EXISTS capabilities(project_id TEXT REFERENCES projects(id),component TEXT NOT NULL,role TEXT NOT NULL,capability_id TEXT NOT NULL,summary TEXT NOT NULL,responsibility TEXT NOT NULL,payload TEXT NOT NULL,evidence_id TEXT REFERENCES evidence(id),PRIMARY KEY(project_id,capability_id))")
    return db

def _migrate_dependency_specs(db):
    """Upgrade only the known legacy constraint, inside the scan transaction."""
    version=db.execute('PRAGMA user_version').fetchone()[0]
    if version>1: raise ValueError('Unsupported inventory schema version')
    old_key=('project_id','ecosystem','name','scope','evidence_id')
    new_key=old_key+('specification',)
    indexes=db.execute('PRAGMA index_list(dependencies)').fetchall()
    keys={tuple(r[0] for r in db.execute('SELECT name FROM pragma_index_info(?) ORDER BY seqno',(idx[1],)))
          for idx in indexes if idx[2]}
    if new_key not in keys or old_key in keys:
        columns=tuple(r[1] for r in db.execute('PRAGMA table_info(dependencies)'))
        if keys!={old_key} or len(indexes)!=1 or columns!=('project_id','ecosystem','name','specification','scope','evidence_id'):
            raise ValueError('Unsupported dependency schema; scan into a new database')
        db.execute("""CREATE TABLE dependencies_spec_upgrade(
          project_id TEXT REFERENCES projects(id),ecosystem TEXT,name TEXT,specification TEXT,
          scope TEXT,evidence_id TEXT REFERENCES evidence(id),
          UNIQUE(project_id,ecosystem,name,scope,evidence_id,specification))""")
        db.execute('INSERT INTO dependencies_spec_upgrade SELECT project_id,ecosystem,name,specification,scope,evidence_id FROM dependencies')
        db.execute('DROP TABLE dependencies')
        db.execute('ALTER TABLE dependencies_spec_upgrade RENAME TO dependencies')
    db.execute('PRAGMA user_version=1')

def workflow_actions(text,notes):
    """Lexically list `uses:` values; unparsed values are counted, never stored."""
    actions=[]
    for match in re.finditer(r'^\s*-?\s*uses:[ \t]*(.*)$',text,re.M):
        value=re.sub(r'\s+#.*$','',match[1]).strip()
        quoted=re.fullmatch(r'(["\'])(.*)\1',value)
        if quoted: value=quoted[2].strip()
        if re.fullmatch(r'[A-Za-z0-9_./@-]+',value): actions.append(value)
        elif '${{' in value: notes['dynamic_action_reference']+=1
        else: notes['unsupported_expression']+=1
    return actions

def commit(root):
    try:
        r=subprocess.run(['git','-C',str(root),'rev-parse','--verify','HEAD'],check=True,capture_output=True,text=True,timeout=5)
        value=r.stdout.strip()
        return value if re.fullmatch(r'[0-9a-f]{40,64}',value) else None
    except (OSError,subprocess.SubprocessError):
        return None

def normalize(name):
    return re.sub(r'[-_.]+','-',name).lower()

def git_root_info(root):
    try:
        r=subprocess.run(['git','-C',str(root),'rev-parse','--show-prefix'],check=True,capture_output=True,text=True,timeout=5)
        raw=r.stdout
        if raw.endswith('\r\n'): raw=raw[:-2]
        elif raw.endswith('\n'): raw=raw[:-1]
        if raw=='': return {'git_root_relation':'repository_root','git_relative_prefix':''}
        prefix=raw[:-1] if raw.endswith('/') else raw
        if prefix and len(prefix)<=256 and '..' not in prefix.split('/') and all(ord(c)>=32 for c in prefix):
            return {'git_root_relation':'subdirectory','git_relative_prefix':prefix}
        return {'git_root_relation':'subdirectory','git_relative_prefix':None}
    except (OSError,subprocess.SubprocessError):
        return {'git_root_relation':'not_git_or_unknown','git_relative_prefix':None}

def spec_item(raw,scope,notes):
    if not isinstance(raw,str): raise SourceFormatError('dependency entry is not text')
    value=raw.strip()
    if not value or value.startswith('#'): return None
    if value.startswith('-'): notes['directive']+=1; return None
    if any(x in value for x in ('://','@','/','\\')): notes['direct_reference']+=1; return None
    if ';' in value: notes['environment_marker']+=1; return None
    m=SPEC.fullmatch(value)
    if not m: notes['unsupported_expression']+=1; return None
    return ('python',normalize(m[1]),(m[2] or '').strip(),scope)

def is_requirements(name):
    return name=='requirements.txt' or (name.startswith('requirements-') and name.endswith('.txt') and '/' not in name)

def require_table(value,label):
    if not isinstance(value,dict): raise SourceFormatError(label+' must be a table/object')
    return value

def require_list(value,label):
    if not isinstance(value,list): raise SourceFormatError(label+' must be a list')
    return value

def parse_with_notes(name,text):
    notes=Counter(); structures=[]; result=[]
    if is_requirements(name):
        scope='runtime' if name=='requirements.txt' else 'requirements:'+name[len('requirements-'):-4]
        for line in text.splitlines():
            item=spec_item(re.sub(r'\s+#.*$','',line),scope,notes)
            if item: result.append(item)
    elif name=='pyproject.toml':
        doc=tomllib.loads(text)
        project=require_table(doc.get('project',{}),'project')
        for raw in require_list(project.get('dependencies',[]),'project.dependencies'):
            item=spec_item(raw,'runtime',notes)
            if item: result.append(item)
        for group,items in require_table(project.get('optional-dependencies',{}),'optional-dependencies').items():
            for raw in require_list(items,'optional dependency group'):
                item=spec_item(raw,'optional:'+group,notes)
                if item: result.append(item)
        for group,items in sorted(require_table(doc.get('dependency-groups',{}),'dependency-groups').items()):
            scope='group:'+normalize(group)
            for raw in require_list(items,'dependency group'):
                if isinstance(raw,dict):
                    if set(raw)=={'include-group'}:
                        structures.append({'structure':'dependency-group include','group':normalize(group),'reason':'include-group entries are not expanded'})
                    else: notes['unsupported_expression']+=1
                    continue
                item=spec_item(raw,scope,notes)
                if item: result.append(item)
        tool=doc.get('tool',{})
        if isinstance(tool,dict):
            if isinstance(tool.get('poetry'),dict): structures.append({'structure':'tool.poetry','reason':'Poetry-specific dependency tables are not extracted'})
            uv=tool.get('uv')
            if isinstance(uv,dict) and 'workspace' in uv: structures.append({'structure':'uv workspace','reason':'workspace members are not discovered'})
    elif name=='package.json':
        doc=json.loads(text)
        if not isinstance(doc,dict): raise SourceFormatError('package.json must be an object')
        for scope in ('dependencies','devDependencies','peerDependencies','optionalDependencies'):
            for package,spec in require_table(doc.get(scope,{}),scope).items():
                if isinstance(spec,str) and re.fullmatch(r'@?[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)?',package) and re.fullmatch(r'[A-Za-z0-9.*^~<>=|+ -]{0,160}',spec): result.append(('npm',package.lower(),spec,scope))
                elif isinstance(spec,str) and ('://' in spec or spec.startswith(('file:','link:','git','github:','workspace:'))): notes['direct_reference']+=1
                else: notes['unsupported_expression']+=1
        if 'workspaces' in doc: structures.append({'structure':'npm workspaces','reason':'workspace members are not discovered'})
    return result,notes,structures

def parse(name,text):
    return parse_with_notes(name,text)[0]

def safe(root,path):
    relative=path.relative_to(root); current=root
    for part in relative.parts:
        current=current/part
        if current.is_symlink(): return False
    return path.resolve().is_relative_to(root)

def scan(db,supplied):
    path=Path(supplied).expanduser()
    if path.is_symlink(): raise ValueError('Root symlinks are not accepted')
    root=path.resolve(strict=True)
    if not root.is_dir(): raise ValueError('Root must be a directory')
    pid=digest(str(root)); records=[]; deps=[]; caps=[]; skipped=[]; checked=[]; missing=[]; omitted=[]; structures=[]; workflow_refs={}
    candidates=[root/n for n in ALLOWED]
    for pattern in PATTERNS:
        for p in root.glob(pattern):
            candidates.append(p)
            if len(candidates)>MAX_FILES: raise ValueError('File discovery limit exceeded; narrow scan root')
    candidates=sorted(set(candidates),key=str)
    total=0
    for source in candidates:
        name=source.relative_to(root).as_posix()
        if not safe(root,source):
            skipped.append({'path':name,'reason':'symlink'}); continue
        if not source.is_file(): missing.append(name); continue
        try:
            with source.open('rb') as f: raw=f.read(MAX_BYTES+1)
            if len(raw)>MAX_BYTES:
                skipped.append({'path':name,'reason':'size_limit'}); continue
            total+=len(raw)
            if total>2_000_000: raise ValueError('Total scan size exceeded')
            text=raw.decode('utf-8'); extracted,notes,found=parse_with_notes(name,text)
            sha=hashlib.sha256(raw).hexdigest(); eid=digest(pid+':'+name+':'+sha)
            content=None; kind='manifest'; method='structured_parser'
            if name.endswith('/plugin.json') or name.endswith('/marketplace.json'):
                doc=json.loads(text)
                if not isinstance(doc,dict): raise SourceFormatError('plugin manifest must be an object')
                summary={k:doc[k] for k in ('name','version','description') if isinstance(doc.get(k),str)}
                if 'plugins' in doc:
                    if not isinstance(doc['plugins'],list): raise SourceFormatError('plugins must be a list')
                    summary['plugin_names']=[x['name'] for x in doc['plugins'] if isinstance(x,dict) and isinstance(x.get('name'),str)]
                content=json.dumps(summary,ensure_ascii=False); kind='plugin_manifest'
            elif name.startswith('.github/workflows/'):
                actions=workflow_actions(text,notes)
                content=json.dumps({'declared_action_references':actions,'semantics':'lexical_only; no YAML validation or run-state check'},ensure_ascii=False)
                kind='workflow'; method='bounded_lexical_extraction'
            elif '/' not in name and name.startswith('azure-pipelines') and name.endswith(('.yml','.yaml')):
                kind='workflow'; method='bounded_lexical_extraction'
                workflow_refs[name]=pipeline_metadata(text)
            elif name==MANIFEST_FILENAME:
                caps.extend((pid,*c,eid) for c in parse_capability_manifest(root,text,max_file_bytes=MAX_BYTES,error_cls=SourceFormatError))
                content=None; kind='capability_manifest'; method='structured_parser'
            elif name.endswith('CODEOWNERS') or name=='CLAUDE.md' or name.startswith('README'):
                content=text; kind='unverified_document'; method='reference_text'
        except SourceFormatError as exc:
            raise ValueError('Cannot process '+name+': '+str(exc)) from None
        except (OSError,ValueError,TypeError,AttributeError,KeyError,RecursionError) as exc:
            raise ValueError('Cannot process '+name+': '+type(exc).__name__) from None
        records.append((eid,pid,name,sha,kind,method,content)); checked.append(name)
        deps.extend((pid,*d,eid) for d in extracted)
        omitted.extend({'path':name,'reason':reason,'count':count} for reason,count in sorted(notes.items()))
        structures.extend({'path':name,**item} for item in found)
    skill_decls,skill_coverage=skill_frontmatter.collect_skill_declarations(root,{c[3] for c in caps},safe)
    for decl in skill_decls:
        sha=decl['frontmatter_sha256']; eid=digest(pid+':'+decl['path']+':'+sha)
        records.append((eid,pid,decl['path'],sha,skill_frontmatter.EVIDENCE_KIND,skill_frontmatter.EVIDENCE_METHOD,None))
        checked.append(decl['path'])
        payload={'source':skill_frontmatter.DECLARATION_SOURCE,
                 'entrypoints':[{'path':decl['path'],'path_status':'observed_local_file_frontmatter_only','file_sha256':None,'symbol':None,'symbol_status':None}],
                 'references':[],'out_of_scope':[]}
        caps.append((pid,decl['component'],decl['role'],decl['capability_id'],decl['summary'],decl['responsibility'],json.dumps(payload,ensure_ascii=True),eid))
    declaration_count=len(deps)
    evidence_paths={row[0]:row[2] for row in records}
    for declaration,count in Counter(deps).items():
        if count>1:
            omitted.append({'path':evidence_paths[declaration[-1]],'reason':'duplicate_declaration','count':count-1})
    deps=list(dict.fromkeys(deps))
    coverage={'dependency_schema_version':1,**git_root_info(root),'checked':checked,'skipped':skipped,'absent_candidates':missing,'patterns_checked':list(PATTERNS),'omitted_declarations':omitted,'partial_structures':structures,'unsupported':list(UNSUPPORTED),'not_checked':['runtime state','actual permissions','remote freshness'],'workflow_references_by_path':workflow_refs,'skill_frontmatter':skill_coverage}
    with db:
        if not db.in_transaction: db.execute('BEGIN')
        _migrate_dependency_specs(db)
        db.execute('CREATE TABLE IF NOT EXISTS capabilities(project_id TEXT REFERENCES projects(id),component TEXT NOT NULL,role TEXT NOT NULL,capability_id TEXT NOT NULL,summary TEXT NOT NULL,responsibility TEXT NOT NULL,payload TEXT NOT NULL,evidence_id TEXT REFERENCES evidence(id),PRIMARY KEY(project_id,capability_id))')
        db.execute('DELETE FROM capabilities WHERE project_id=?',(pid,))
        db.execute('DELETE FROM dependencies WHERE project_id=?',(pid,))
        db.execute('DELETE FROM evidence WHERE project_id=?',(pid,))
        db.execute('INSERT INTO projects VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,commit_sha=excluded.commit_sha,scanned_at=excluded.scanned_at',(pid,root.name,str(root),commit(root),datetime.now(timezone.utc).isoformat()))
        db.executemany('INSERT INTO evidence VALUES(?,?,?,?,?,?,?)',records)
        db.executemany('INSERT INTO dependencies VALUES(?,?,?,?,?,?)',deps)
        db.executemany('INSERT INTO capabilities VALUES(?,?,?,?,?,?,?,?)',caps)
        db.execute('INSERT OR REPLACE INTO scan_coverage VALUES(?,?)',(pid,json.dumps(coverage)))
    return {'project_id':pid,'name':root.name,'evidence_count':len(records),'dependency_count':len(deps),'declaration_count':declaration_count,'capability_count':len(caps),'skipped':skipped,'coverage':coverage}

def show(db,value):
    rows=db.execute('SELECT * FROM projects WHERE id=? OR name=?',(value,value)).fetchall()
    if len(rows)!=1: raise ValueError('Missing or ambiguous project; use full ID from list')
    result=dict(rows[0])
    for table in ('evidence','dependencies'): result[table]=[dict(r) for r in db.execute('SELECT * FROM '+table+' WHERE project_id=?',(result['id'],))]
    coverage=db.execute('SELECT payload FROM scan_coverage WHERE project_id=?',(result['id'],)).fetchone()
    result['scan_coverage']=json.loads(coverage[0]) if coverage else None
    return result

def graph(db,*,include_content=False):
    nodes=[{'type':'Project',**dict(r)} for r in db.execute('SELECT * FROM projects ORDER BY id')]; edges=[]; seen=set()
    evidence_columns='id,project_id,path,sha256,kind,method'+(',content' if include_content else '')
    for row in db.execute('SELECT '+evidence_columns+' FROM evidence ORDER BY id'):
        e=dict(row); nodes.append({'type':'Evidence',**e}); edges.append({'source':e['project_id'],'target':e['id'],'type':'HAS_EVIDENCE'})
    for row in db.execute('SELECT * FROM dependencies ORDER BY project_id,ecosystem,name,scope'):
        d=dict(row); tid=d['ecosystem']+':'+d['name']
        if tid not in seen: nodes.append({'id':tid,'type':'Package','ecosystem':d['ecosystem'],'name':d['name']}); seen.add(tid)
        edges.append({'source':d['project_id'],'target':tid,'type':'DECLARES_DEPENDENCY','evidence_id':d['evidence_id'],'scope':d['scope'],'specification':d['specification']})
    return {'schema_version':'0.1','nodes':nodes,'edges':edges}

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('--db',default='org-context.sqlite')
    sub=p.add_subparsers(dest='command',required=True)
    s=sub.add_parser('scan'); s.add_argument('paths',nargs='+')
    sub.add_parser('list'); s=sub.add_parser('show'); s.add_argument('project')
    s=sub.add_parser('search'); s.add_argument('--technology',required=True); s.add_argument('--ecosystem',choices=['python','npm'])
    s=sub.add_parser('export'); s.add_argument('--include-content',action='store_true',help='Include retained source bodies in this local export; may expose sensitive text')
    args=p.parse_args(argv); db=None
    try:
        db=connect(args.db)
        if args.command=='scan': result=[scan(db,x) for x in args.paths]
        elif args.command=='list': result=[dict(r) for r in db.execute('SELECT * FROM projects ORDER BY name,id')]
        elif args.command=='show': result=show(db,args.project)
        elif args.command=='export': result=graph(db,include_content=args.include_content)
        else:
            sql="SELECT p.name AS project_name,p.commit_sha,p.scanned_at,d.* FROM dependencies d JOIN projects p ON p.id=d.project_id WHERE ((d.ecosystem='python' AND d.name=?) OR (d.ecosystem='npm' AND d.name=?))"
            params=[normalize(args.technology),args.technology.lower()]
            if args.ecosystem: sql+=' AND d.ecosystem=?'; params.append(args.ecosystem)
            result=[dict(r) for r in db.execute(sql+' ORDER BY p.id,d.scope',params)]
        print(json.dumps(result,indent=2,ensure_ascii=True)); return 0
    except (OSError,ValueError,TypeError,AttributeError,sqlite3.Error) as exc: p.exit(2,'Error: '+str(exc)+'\n')
    finally:
        if db is not None: db.close()

if __name__=='__main__': main()
