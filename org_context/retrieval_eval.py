"""Offline retrieval regression runner, not an LLM or MCP transport evaluation."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import sys
from time import perf_counter
from types import SimpleNamespace

TOOLS={'list_projects','find_projects_using','shared_dependencies'}
MAX_CASE_BYTES=128000

def validate(doc):
    if not isinstance(doc,dict) or set(doc)!={'schema_version','cases'} or type(doc['schema_version']) is not int or doc['schema_version']!=1:
        raise ValueError('Expected schema_version 1 and cases')
    cases=doc['cases']
    if not isinstance(cases,list) or not 1<=len(cases)<=100: raise ValueError('Expected 1-100 cases')
    seen=set()
    for c in cases:
        if not isinstance(c,dict) or set(c)!={'id','tool','arguments','checks'}: raise ValueError('Invalid case fields')
        cid=c['id']
        if not isinstance(cid,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',cid) or cid in seen: raise ValueError('Invalid/duplicate case ID')
        seen.add(cid)
        if not isinstance(c['tool'],str) or c['tool'] not in TOOLS or not isinstance(c['arguments'],dict): raise ValueError('Unsupported tool or arguments')
        if not isinstance(c['checks'],list) or not 1<=len(c['checks'])<=20: raise ValueError('Expected 1-20 checks per case')
        for chk in c['checks']:
            if not isinstance(chk,dict) or set(chk)-{'path','op','expected','field'} or not {'path','op','expected'}<=set(chk): raise ValueError('Invalid check fields')
            if not isinstance(chk['path'],str) or len(chk['path'])>512 or (chk['path'] and not chk['path'].startswith('/')): raise ValueError('Invalid JSON pointer')
            if chk['op'] not in ('equals','count','distinct_count'): raise ValueError('Unsupported check operation')
            if chk['op'] in ('count','distinct_count') and (type(chk['expected']) is not int or chk['expected']<0): raise ValueError('Expected nonnegative integer count')
            if chk['op']=='distinct_count' and (not isinstance(chk.get('field'),str) or not 1<=len(chk['field'])<=100): raise ValueError('distinct_count requires field')
    return cases

def pointer(value,path):
    if path=='': return value
    for token in path[1:].split('/'):
        if re.search(r'~(?![01])',token): raise ValueError('Invalid JSON pointer escape')
        token=token.replace('~1','/').replace('~0','~')
        if isinstance(value,dict): value=value[token]
        elif isinstance(value,list):
            if not re.fullmatch(r'0|[1-9][0-9]*',token): raise ValueError('Invalid array index')
            value=value[int(token)]
        else: raise ValueError('Pointer crosses a scalar')
    return value

def canonical(value): return json.dumps(value,sort_keys=True,ensure_ascii=True,separators=(',',':'))

def check(result,chk):
    try:
        actual=pointer(result,chk['path'])
        if chk['op']=='equals': return canonical(actual)==canonical(chk['expected'])
        if not isinstance(actual,list): return False
        if chk['op']=='count': return len(actual)==chk['expected']
        values={canonical(row[chk['field']]) for row in actual}
        return len(values)==chk['expected']
    except (ValueError,KeyError,IndexError,TypeError): return False

def truncated(value,depth=0):
    if depth>30: return True
    if isinstance(value,dict):
        return any((k in ('has_more','truncated') or k.endswith('_truncated')) and v is True for k,v in value.items()) or any(truncated(v,depth+1) for v in value.values())
    if isinstance(value,list): return any(truncated(v,depth+1) for v in value)
    return False

def evaluate(context,doc,dispatch):
    cases=validate(doc); results=[]
    # Hold one SQLite read snapshot across cases; discovery SAVEPOINTs can nest.
    context.db.execute('BEGIN')
    try:
        for c in cases:
            start=perf_counter()
            try:
                answer=dispatch(context,c['tool'],c['arguments'])
                passed=[check(answer,chk) for chk in c['checks']]
                status='INCONCLUSIVE' if truncated(answer) else ('PASS' if all(passed) else 'FAIL')
                result={'id':c['id'],'status':status,'checks_passed':passed}
            except Exception as exc:
                # Never serialize tool arguments, returned source content or exception messages.
                result={'id':c['id'],'status':'ERROR','error_type':type(exc).__name__}
            result['elapsed_ms']=round((perf_counter()-start)*1000,3); results.append(result)
    finally:
        context.db.rollback()
    return {'evaluation_kind':'retrieval_regression_direct_dispatch','not_evaluated':['LLM answer quality','MCP transport','Cursor/Windows','runtime truth','source completeness'],
      'cases':results,'counts':{s:sum(r['status']==s for r in results) for s in ('PASS','FAIL','INCONCLUSIVE','ERROR')},
      'warning':'Expected answers must be independently reviewed for the exact input snapshot; passing tests do not establish agent benefit'}

def read_cases(path):
    with Path(path).open('rb') as f: raw=f.read(MAX_CASE_BYTES+1)
    if len(raw)>MAX_CASE_BYTES: raise ValueError('Case file too large')
    return json.loads(raw),hashlib.sha256(raw).hexdigest()

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--db',required=True);p.add_argument('--cases',required=True);p.add_argument('--output')
    a=p.parse_args(argv);db=None
    try:
        dbpath=Path(a.db).resolve(strict=True);casepath=Path(a.cases).resolve(strict=True)
        if a.output:
            # New reports only: never overwrite a DB, cases or another existing artifact.
            output=Path(a.output)
            if output.exists() or output.is_symlink() or output.resolve() in (dbpath,casepath): raise ValueError('Output must be a new file')
        doc,sha=read_cases(casepath);validate(doc)
        db=sqlite3.connect(dbpath.as_uri()+'?mode=ro',uri=True);db.row_factory=sqlite3.Row;db.execute('PRAGMA query_only=ON')
        tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {'projects','dependencies','evidence'}<=tables: raise ValueError('Expected a scanned inventory database')
        from .discovery import call
        report=evaluate(SimpleNamespace(db=db,tables=tables),doc,call);report['case_file_sha256']=sha
        text=json.dumps(report,indent=2,ensure_ascii=True)+'\n'
        if a.output:
            with Path(a.output).open('x',encoding='utf-8') as f:f.write(text)
        else:sys.stdout.write(text)
        return 0 if report['counts']['PASS']==len(report['cases']) else 1
    except (OSError,ValueError,sqlite3.Error,RecursionError):
        sys.stderr.write('Retrieval evaluation setup failed; check database, case schema and output location.\n');return 2
    finally:
        if db is not None:db.close()

if __name__=='__main__':raise SystemExit(main())
