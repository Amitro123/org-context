"""Read-only discovery/comparison over declared inventory, not runtime topology."""
from contextlib import contextmanager
import re

PAGING={'offset':{'type':'integer','minimum':0,'maximum':100000},'limit':{'type':'integer','minimum':1,'maximum':50}}
DISCOVERY_TOOLS=[
 {'name':'list_projects','description':'Discover scanned project IDs, names, local scan roots and snapshot versions. Entries are scan roots, not necessarily distinct repositories.','inputSchema':{'type':'object','properties':PAGING,'additionalProperties':False}},
 {'name':'shared_dependencies','description':'Compare package declarations across scanned projects, with evidence and specification differences. Differences do not establish installed-version conflicts.','inputSchema':{'type':'object','properties':{**PAGING,'ecosystem':{'type':'string','enum':['python','npm']},'project_ids':{'type':'array','minItems':2,'maxItems':10,'uniqueItems':True,'items':{'type':'string','pattern':'^[0-9a-f]{64}$'}}},'additionalProperties':False}}]
NAMES={'list_projects','shared_dependencies','find_projects_using'}
SNAPSHOT_LIMITS={
    'freshness':'unknown',
    'identity':'local scan-root ID; moves or different machines can change it',
    'coverage_scope':'explicit scanned roots and supported declarations only',
    'not_verified':['working-tree freshness','remote state','runtime state','authorization'],
}
DISCOVERY_LIMITS={
    **SNAPSHOT_LIMITS,
    'unsupported_for_dependency_discovery':['lockfiles','nested package and workspace discovery','requirements and dependency-group include expansion','environment-marker evaluation','Poetry tables'],
    'coverage_details':'Use project_card for recorded parser limits, omissions, and unsupported structures. No match does not prove absence.',
}

@contextmanager
def snapshot(db):
    db.execute('SAVEPOINT org_context_discovery')
    try: yield
    except BaseException:
        db.execute('ROLLBACK TO org_context_discovery')
        db.execute('RELEASE org_context_discovery')
        raise
    else: db.execute('RELEASE org_context_discovery')

def paging(args,default_limit=20):
    offset=args.get('offset',0); limit=args.get('limit',default_limit)
    if type(offset) is not int or not 0<=offset<=100000 or type(limit) is not int or not 1<=limit<=50:
        raise ValueError('offset must be 0..100000 and limit 1..50')
    return offset,limit

def call(context,name,args):
    if name not in NAMES or not isinstance(args,dict): raise ValueError('Invalid discovery call')
    allowed={'offset','limit'} if name=='list_projects' else ({'technology','ecosystem','offset','limit'} if name=='find_projects_using' else {'offset','limit','ecosystem','project_ids'})
    if set(args)-allowed: raise ValueError('Unexpected arguments')
    if name=='find_projects_using':
        technology=args.get('technology'); eco=args.get('ecosystem'); offset,limit=paging(args,50)
        if not isinstance(technology,str) or not 1<=len(technology)<=160: raise ValueError('technology must be 1..160 characters')
        if eco is not None and eco not in ('python','npm'): raise ValueError('Unsupported ecosystem')
        if not {'projects','dependencies','evidence'}<=context.tables:
            return {**DISCOVERY_LIMITS,'results':[],'unknowns':['No scanned inventory is available']}
        sql="SELECT p.id AS project_id,p.name AS project_name,p.root AS scan_root,p.commit_sha,p.scanned_at,d.ecosystem,d.name,d.scope,d.specification,e.id AS evidence_id,e.path,e.sha256 FROM dependencies d JOIN projects p ON p.id=d.project_id JOIN evidence e ON e.id=d.evidence_id WHERE ((d.ecosystem='python' AND d.name=?) OR (d.ecosystem='npm' AND d.name=?))"
        params=[re.sub(r'[-_.]+','-',technology.lower()),technology.lower()]
        if eco: sql+=' AND d.ecosystem=?';params.append(eco)
        rows=[dict(r) for r in context.db.execute(sql+' ORDER BY p.id,d.ecosystem,d.name,d.scope,e.id,d.specification LIMIT ? OFFSET ?',params+[limit+1,offset])]
        has_more=len(rows)>limit
        return {**DISCOVERY_LIMITS,'results':rows[:limit],'offset':offset,'truncated':has_more,'has_more':has_more,'next_offset':offset+limit if has_more else None,'semantics':'declared_dependency; not runtime usage','unknowns':['No result does not prove absence outside scanned coverage']}
    offset,limit=paging(args)
    if name=='list_projects':
        if 'projects' not in context.tables: return {**SNAPSHOT_LIMITS,'projects':[],'has_more':False,'next_offset':None,'unknowns':['No scanned inventory; synthetic references are not listed']}
        rows=[dict(r) for r in context.db.execute('SELECT id AS project_id,name,root AS scan_root,commit_sha,scanned_at FROM projects ORDER BY id LIMIT ? OFFSET ?',(limit+1,offset))]
        return {**SNAPSHOT_LIMITS,'projects':rows[:limit],'offset':offset,'has_more':len(rows)>limit,'next_offset':offset+limit if len(rows)>limit else None,'unit':'scan_root, not necessarily distinct repository'}
    eco=args.get('ecosystem'); ids=args.get('project_ids')
    if eco is not None and eco not in ('python','npm'): raise ValueError('Unsupported ecosystem')
    if ids is not None and (not isinstance(ids,list) or not 2<=len(ids)<=10 or not all(isinstance(x,str) and re.fullmatch(r'[0-9a-f]{64}',x) for x in ids) or len(set(ids))!=len(ids)):
        raise ValueError('project_ids must contain 2..10 distinct full scanned IDs')
    if not {'projects','dependencies','evidence'}<=context.tables: return {**DISCOVERY_LIMITS,'groups':[],'has_more':False,'next_offset':None,'unknowns':['No scanned inventory']}
    where=[]; params=[]
    if eco: where.append('d.ecosystem=?');params.append(eco)
    if ids: where.append('d.project_id IN ('+','.join('?' for _ in ids)+')');params.extend(ids)
    condition=' AND '.join(where) or '1=1'
    joins=' FROM dependencies d JOIN projects p ON p.id=d.project_id JOIN evidence e ON e.id=d.evidence_id '
    with snapshot(context.db):
        if ids:
            found={r[0] for r in context.db.execute('SELECT id FROM projects WHERE id IN ('+','.join('?' for _ in ids)+')',ids)}
            if found!=set(ids): raise ValueError('Unknown project ID in comparison; discover IDs using list_projects')
        groups=[dict(r) for r in context.db.execute('SELECT d.ecosystem,d.name,COUNT(DISTINCT d.project_id) AS project_count,COUNT(DISTINCT d.specification) AS specification_count'+joins+'WHERE '+condition+' GROUP BY d.ecosystem,d.name HAVING COUNT(DISTINCT d.project_id)>=2 ORDER BY d.ecosystem,d.name LIMIT ? OFFSET ?',params+[limit+1,offset])]
        result=[]
        for group in groups[:limit]:
            rows=[dict(r) for r in context.db.execute('SELECT p.id AS project_id,p.name AS project_name,p.root AS scan_root,p.commit_sha,p.scanned_at,d.scope,d.specification,e.id AS evidence_id,e.path,e.sha256'+joins+'WHERE '+condition+' AND d.ecosystem=? AND d.name=? ORDER BY p.id,d.scope,e.id LIMIT 51',params+[group['ecosystem'],group['name']])]
            result.append({**group,'has_different_specifications':group['specification_count']>1,'declarations':rows[:50],'declarations_truncated':len(rows)>50})
    return {**DISCOVERY_LIMITS,'groups':result,'offset':offset,'has_more':len(groups)>limit,'next_offset':offset+limit if len(groups)>limit else None,'semantics':'At least two distinct scanned project IDs; duplicate declarations do not increase project_count. Specification differences are not installed versions or incompatibility.','unknowns':['Coverage depends on imported scans; no runtime or repository deduplication is performed']}
