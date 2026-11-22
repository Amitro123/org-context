"""Local inventory of explicitly selected repository roots."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import subprocess
import tomllib

ALLOWED = ('README', 'README.md', 'README.rst', 'README.txt', 'pyproject.toml', 'requirements.txt', 'package.json')
MAX_BYTES = 256000

def digest(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()

def connect(path):
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    db.executescript('''
    CREATE TABLE IF NOT EXISTS projects(
      id TEXT PRIMARY KEY, name TEXT, root TEXT UNIQUE, commit_sha TEXT, scanned_at TEXT);
    CREATE TABLE IF NOT EXISTS evidence(
      id TEXT PRIMARY KEY, project_id TEXT REFERENCES projects(id),
      path TEXT, sha256 TEXT, kind TEXT, method TEXT, content TEXT);
    CREATE TABLE IF NOT EXISTS dependencies(
      project_id TEXT REFERENCES projects(id), ecosystem TEXT, name TEXT,
      specification TEXT, scope TEXT, evidence_id TEXT REFERENCES evidence(id),
      UNIQUE(project_id,ecosystem,name,scope,evidence_id));
    ''')
    return db

def commit(root):
    if not (root / '.git').exists():
        return None
    try:
        r = subprocess.run(['git', '-C', str(root), 'rev-parse', '--verify', 'HEAD'],
                           check=True, capture_output=True, text=True, timeout=5)
        value = r.stdout.strip()
        return value if re.fullmatch(r'[0-9a-f]{40,64}', value) else None
    except (OSError, subprocess.SubprocessError):
        return None

def python_deps(items, scope):
    for raw in items:
        if not isinstance(raw, str):
            raise ValueError('Dependency must be text')
        value = raw.strip()
        if any(x in value for x in ('://', '@', '/', '\\')):
            continue
        match = re.fullmatch(r'([A-Za-z0-9][A-Za-z0-9_.-]*)(?:\[[A-Za-z0-9_,.-]+\])?\s*([<>=!~][A-Za-z0-9.*<>=!~,+ -]+)?', value)
        if match:
            yield ('python', re.sub(r'[-_.]+', '-', match[1]).lower(), (match[2] or '').strip(), scope)

def parse(name, text):
    if name == 'requirements.txt':
        return list(python_deps(text.splitlines(), 'runtime'))
    if name == 'pyproject.toml':
        project = tomllib.loads(text).get('project', {})
        result = list(python_deps(project.get('dependencies', []), 'runtime'))
        for group, items in project.get('optional-dependencies', {}).items():
            result.extend(python_deps(items, 'optional:' + group))
        return result
    if name == 'package.json':
        doc = json.loads(text)
        result = []
        for scope in ('dependencies', 'devDependencies', 'peerDependencies', 'optionalDependencies'):
            for package, spec in doc.get(scope, {}).items():
                if isinstance(spec, str) and re.fullmatch(r'@?[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)?', package) and re.fullmatch(r'[A-Za-z0-9.*^~<>=|+ -]{0,160}', spec):
                    result.append(('npm', package.lower(), spec, scope))
        return result
    return []

def scan(db, supplied):
    path = Path(supplied).expanduser()
    if path.is_symlink():
        raise ValueError('Root symlinks are not accepted')
    root = path.resolve(strict=True)
    if not root.is_dir():
        raise ValueError('Root must be a directory')
    pid = digest(str(root))
    evidence, deps, skipped = [], [], []
    for name in ALLOWED:
        source = root / name
        if source.is_symlink():
            skipped.append({'path': name, 'reason': 'symlink'})
            continue
        if not source.is_file():
            continue
        if source.stat().st_size > MAX_BYTES:
            skipped.append({'path': name, 'reason': 'size_limit'})
            continue
        text = source.read_text(encoding='utf-8')
        extracted = parse(name, text)
        sha = digest(text)
        eid = digest(pid + ':' + name + ':' + sha)
        doc = name.startswith('README')
        evidence.append((eid, pid, name, sha, 'unverified_document' if doc else 'manifest',
                         'reference_text' if doc else 'structured_parser', text if doc else None))
        deps.extend((pid, *dep, eid) for dep in extracted)
    with db:
        db.execute('DELETE FROM dependencies WHERE project_id=?', (pid,))
        db.execute('DELETE FROM evidence WHERE project_id=?', (pid,))
        db.execute('''INSERT INTO projects VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
                    name=excluded.name, commit_sha=excluded.commit_sha, scanned_at=excluded.scanned_at''',
                   (pid, root.name, str(root), commit(root), datetime.now(timezone.utc).isoformat()))
        db.executemany('INSERT INTO evidence VALUES(?,?,?,?,?,?,?)', evidence)
        db.executemany('INSERT OR IGNORE INTO dependencies VALUES(?,?,?,?,?,?)', deps)
    return {'project_id': pid, 'name': root.name, 'evidence_count': len(evidence), 'dependency_count': len(deps), 'skipped': skipped}

def show(db, value):
    rows = db.execute('SELECT * FROM projects WHERE id=? OR name=?', (value, value)).fetchall()
    if len(rows) != 1:
        raise ValueError('Missing or ambiguous project; use full ID from list')
    result = dict(rows[0])
    for table in ('evidence', 'dependencies'):
        result[table] = [dict(r) for r in db.execute('SELECT * FROM ' + table + ' WHERE project_id=?', (result['id'],))]
    return result

def graph(db):
    nodes, edges, seen = [], [], set()
    for row in db.execute('SELECT * FROM projects ORDER BY id'):
        nodes.append({'type': 'Project', **dict(row)})
    for row in db.execute('SELECT * FROM evidence ORDER BY id'):
        e = dict(row)
        nodes.append({'type': 'Evidence', **e})
        edges.append({'source': e['project_id'], 'target': e['id'], 'type': 'HAS_EVIDENCE'})
    for row in db.execute('SELECT * FROM dependencies ORDER BY project_id,ecosystem,name,scope'):
        d = dict(row)
        tid = d['ecosystem'] + ':' + d['name']
        if tid not in seen:
            nodes.append({'id': tid, 'type': 'Package', 'ecosystem': d['ecosystem'], 'name': d['name']})
            seen.add(tid)
        edges.append({'source': d['project_id'], 'target': tid, 'type': 'DECLARES_DEPENDENCY',
                      'evidence_id': d['evidence_id'], 'scope': d['scope'], 'specification': d['specification']})
    return {'schema_version': '0.1', 'nodes': nodes, 'edges': edges}

def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--db', default='org-context.sqlite')
    sub = p.add_subparsers(dest='command', required=True)
    s = sub.add_parser('scan'); s.add_argument('paths', nargs='+')
    sub.add_parser('list')
    s = sub.add_parser('show'); s.add_argument('project')
    s = sub.add_parser('search'); s.add_argument('--technology', required=True); s.add_argument('--ecosystem', choices=['python', 'npm'])
    sub.add_parser('export')
    args = p.parse_args(argv)
    db = None
    try:
        db = connect(args.db)
        if args.command == 'scan':
            result = [scan(db, root) for root in args.paths]
        elif args.command == 'list':
            result = [dict(r) for r in db.execute('SELECT * FROM projects ORDER BY name,id')]
        elif args.command == 'show':
            result = show(db, args.project)
        elif args.command == 'export':
            result = graph(db)
        else:
            name = args.technology.lower()
            sql = 'SELECT p.name AS project_name,p.commit_sha,p.scanned_at,d.* FROM dependencies d JOIN projects p ON p.id=d.project_id WHERE ((d.ecosystem="python" AND d.name=?) OR (d.ecosystem="npm" AND d.name=?))'
            params = [re.sub(r'[-_.]+', '-', name), name]
            if args.ecosystem:
                sql += ' AND d.ecosystem=?'; params.append(args.ecosystem)
            result = [dict(r) for r in db.execute(sql + ' ORDER BY p.id,d.scope', params)]
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    except (OSError, ValueError, TypeError, AttributeError, sqlite3.Error) as exc:
        p.exit(2, 'Error: ' + str(exc) + '\n')
    finally:
        if db is not None:
            db.close()

if __name__ == '__main__':
    main()
