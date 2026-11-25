"""Tests for optional capability manifest scanning and read-only find_capabilities."""
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import tempfile
from unittest import TestCase

from org_context import cli
from org_context.inventory_context import Context
from org_context.mcp_server import Session


def _init_session(ctx):
    session = Session(ctx)
    session.handle({
        'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
        'params': {'protocolVersion': '2025-03-26', 'capabilities': {}, 'clientInfo': {'name': 't', 'version': '1'}},
    })
    session.handle({'jsonrpc': '2.0', 'method': 'notifications/initialized'})
    return session


def _call_tool(session, name, arguments, rid=2):
    return session.handle({
        'jsonrpc': '2.0', 'id': rid, 'method': 'tools/call',
        'params': {'name': name, 'arguments': arguments},
    })['result']


def _sample_manifest(cap_id='ground-truth-validation', ep_path='src/example/ground_truth.py',
                     ref_path='docs/contract.md', role='evaluation', component='example-eval'):
    return {
        'schema_version': 1,
        'component': component,
        'role': role,
        'capabilities': [
            {
                'id': cap_id,
                'summary': 'Locate the implementation that checks evaluation ground truth',
                'responsibility': 'Validate ground-truth inputs used by evaluation',
                'entrypoints': [
                    {'path': ep_path, 'symbol': 'validate_ground_truth'},
                ],
                'references': [ref_path] if ref_path else [],
                'out_of_scope': ['Authorize publication', 'Prove deployed runtime state'],
            }
        ],
    }


class CapabilityManifestTests(TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        self.db_path = self.base / 'inventory.sqlite'
        self.db = cli.connect(self.db_path)
        self.addCleanup(self.db.close)

    def _write(self, root, rel, text):
        p = root.joinpath(*rel.split('/'))
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding='utf-8')
        return p

    def test_discovery_end_to_end_monorepo_provenance_and_metadata_only(self):
        mono = self.base / 'mono'
        sub = mono / 'modules' / 'evaluation'
        sub.mkdir(parents=True)
        subprocess.run(['git', 'init', '-q', str(mono)], check=True)

        src = self._write(sub, 'src/example/ground_truth.py', 'BODY_SECRET_SENTINEL = True\n')
        ref = self._write(sub, 'docs/contract.md', 'CONTRACT_BODY_SENTINEL\n')
        manifest_path = self._write(
            sub, 'org-context.manifest.json', json.dumps(_sample_manifest())
        )
        manifest_sha = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
        src_sha = hashlib.sha256(src.read_bytes()).hexdigest()
        ref_sha = hashlib.sha256(ref.read_bytes()).hexdigest()

        scan_res = cli.scan(self.db, sub)
        self.assertEqual(scan_res['capability_count'], 1)
        pid = scan_res['project_id']

        ctx = Context(self.db_path)
        self.addCleanup(ctx.close)
        session = _init_session(ctx)

        res = _call_tool(session, 'find_capabilities', {'query': 'ground truth'})
        self.assertFalse(res['isError'])
        raw = res['content'][0]['text']
        self.assertNotIn('SENTINEL', raw)
        payload = json.loads(raw)

        self.assertEqual(payload['freshness'], 'unknown')
        self.assertFalse(payload['has_more'])
        self.assertIsNone(payload['next_offset'])
        self.assertEqual(len(payload['results']), 1)
        item = payload['results'][0]
        self.assertEqual(item['project_id'], pid)
        self.assertEqual(item['git_root_relation'], 'subdirectory')
        self.assertEqual(item['git_relative_prefix'], 'modules/evaluation')
        self.assertEqual(item['component'], 'example-eval')
        self.assertEqual(item['role'], 'evaluation')
        self.assertEqual(item['capability_id'], 'ground-truth-validation')
        # Manifest hash is distinct from referenced file hash.
        self.assertEqual(item['manifest_evidence']['sha256'], manifest_sha)
        self.assertEqual(item['manifest_evidence']['kind'], 'capability_manifest')
        ep = item['entrypoints'][0]
        self.assertEqual(ep['path'], 'src/example/ground_truth.py')
        self.assertEqual(ep['path_status'], 'observed_local_file')
        self.assertEqual(ep['file_sha256'], src_sha)
        self.assertNotEqual(ep['file_sha256'], manifest_sha)
        self.assertEqual(ep['symbol'], 'validate_ground_truth')
        self.assertEqual(ep['symbol_status'], 'declared_only_not_resolved')
        self.assertEqual(item['references'][0], {
            'path': 'docs/contract.md',
            'path_status': 'observed_local_file',
            'file_sha256': ref_sha,
        })
        self.assertIn('not runtime verification', item['declaration_semantics'])

        # No-match response explicitly states coverage limits.
        no_match = json.loads(_call_tool(session, 'find_capabilities', {'query': 'nonexistent_xyz'}, rid=3)['content'][0]['text'])
        self.assertEqual(no_match['results'], [])
        self.assertTrue(any('does not prove' in u for u in no_match['unknowns']))

    def test_missing_entrypoint_and_optional_manifest_absence(self):
        # Repo without manifest scans normally with capability_count == 0.
        plain = self.base / 'plain'
        plain.mkdir()
        self._write(plain, 'README.md', 'plain repo')
        self.assertEqual(cli.scan(self.db, plain)['capability_count'], 0)

        # Repo with manifest pointing to a missing file reports missing_file explicitly.
        with_missing = self.base / 'missing_ep'
        with_missing.mkdir()
        self._write(
            with_missing,
            'org-context.manifest.json',
            json.dumps(_sample_manifest(ep_path='src/does_not_exist.py', ref_path='docs/missing.md')),
        )
        cli.scan(self.db, with_missing)
        ctx = Context(self.db_path)
        self.addCleanup(ctx.close)
        session = _init_session(ctx)
        payload = json.loads(_call_tool(session, 'find_capabilities', {'query': 'ground truth'})['content'][0]['text'])
        ep = payload['results'][0]['entrypoints'][0]
        self.assertEqual(ep['path_status'], 'missing_file')
        self.assertIsNone(ep['file_sha256'])
        self.assertEqual(ep['symbol_status'], 'declared_only_not_resolved')
        self.assertEqual(payload['results'][0]['references'][0]['path_status'], 'missing_file')

    def test_invalid_manifest_fails_closed_and_preserves_previous_snapshot(self):
        repo = self.base / 'repo'
        repo.mkdir()
        self._write(repo, 'src/example/ground_truth.py', 'x = 1\n')
        self._write(repo, 'docs/contract.md', 'doc\n')
        self._write(repo, 'org-context.manifest.json', json.dumps(_sample_manifest()))
        pid = cli.scan(self.db, repo)['project_id']

        bad_manifests = [
            '{bad json',
            json.dumps({**_sample_manifest(), 'schema_version': 2}),
            json.dumps({**_sample_manifest(), 'extra_field': True}),
            json.dumps(_sample_manifest(ep_path='../outside.py')),
            json.dumps(_sample_manifest(ep_path='/etc/passwd')),
            json.dumps(_sample_manifest(cap_id='INVALID_UPPER')),
            json.dumps({**_sample_manifest(), 'capabilities': []}),
            json.dumps({**_sample_manifest(), 'capabilities': [
                _sample_manifest(cap_id='dup-id')['capabilities'][0],
                _sample_manifest(cap_id='dup-id')['capabilities'][0],
            ]}),
        ]
        for bad in bad_manifests:
            with self.subTest(bad=bad[:40]):
                self._write(repo, 'org-context.manifest.json', bad)
                with self.assertRaises(ValueError):
                    cli.scan(self.db, repo)
                # Previous valid snapshot must remain intact.
                ctx = Context(self.db_path)
                try:
                    session = _init_session(ctx)
                    res = json.loads(_call_tool(session, 'find_capabilities', {'query': 'ground truth'})['content'][0]['text'])
                    self.assertEqual(len(res['results']), 1)
                    self.assertEqual(res['results'][0]['project_id'], pid)
                finally:
                    ctx.close()

    def test_pagination_deterministic_and_legacy_db_compatibility(self):
        repo = self.base / 'paged'
        repo.mkdir()
        caps = []
        for i in range(25):
            caps.append({
                'id': f'cap-{i:02d}',
                'summary': f'Shared governance capability {i:02d}',
                'responsibility': 'Handle governance task',
                'entrypoints': [{'path': 'src/mod.py'}],
            })
        self._write(repo, 'src/mod.py', 'pass\n')
        self._write(repo, 'org-context.manifest.json', json.dumps({
            'schema_version': 1, 'component': 'gov', 'role': 'governance', 'capabilities': caps,
        }))
        cli.scan(self.db, repo)

        ctx = Context(self.db_path)
        self.addCleanup(ctx.close)
        session = _init_session(ctx)

        seen = []
        offset = 0
        rid = 10
        while offset is not None:
            page = json.loads(_call_tool(session, 'find_capabilities', {
                'query': 'governance', 'role': 'governance', 'offset': offset, 'limit': 10,
            }, rid=rid)['content'][0]['text'])
            rid += 1
            seen.extend(item['capability_id'] for item in page['results'])
            offset = page['next_offset']
        self.assertEqual(len(seen), 25)
        self.assertEqual(len(set(seen)), 25)
        self.assertEqual(seen, sorted(seen))

        # Legacy DB without capabilities table opens and returns explicit unknown.
        legacy_path = self.base / 'legacy.sqlite'
        ldb = sqlite3.connect(legacy_path)
        ldb.executescript(
            'CREATE TABLE projects(id TEXT PRIMARY KEY,name TEXT,root TEXT UNIQUE,commit_sha TEXT,scanned_at TEXT);'
            'CREATE TABLE evidence(id TEXT PRIMARY KEY,project_id TEXT,path TEXT,sha256 TEXT,kind TEXT,method TEXT,content TEXT);'
            'CREATE TABLE dependencies(project_id TEXT,ecosystem TEXT,name TEXT,specification TEXT,scope TEXT,evidence_id TEXT);'
        )
        ldb.commit()
        ldb.close()
        lctx = Context(legacy_path)
        self.addCleanup(lctx.close)
        lsess = _init_session(lctx)
        lres = json.loads(_call_tool(lsess, 'find_capabilities', {'query': 'anything'})['content'][0]['text'])
        self.assertEqual(lres['results'], [])
        self.assertTrue(any('rescan required' in u for u in lres['unknowns']))

    def test_find_capabilities_argument_validation(self):
        ctx = Context(self.db_path)
        self.addCleanup(ctx.close)
        session = _init_session(ctx)
        for idx, bad_args in enumerate([
            {},
            {'query': '   '},
            {'query': '---'},
            {'query': 'ok', 'limit': 0},
            {'query': 'ok', 'limit': 21},
            {'query': 'ok', 'offset': -1},
            {'query': 'ok', 'project_id': '../bad'},
            {'query': 'ok', 'extra': 1},
        ], start=20):
            with self.subTest(bad_args=bad_args):
                res = _call_tool(session, 'find_capabilities', bad_args, rid=idx)
                self.assertTrue(res['isError'])
