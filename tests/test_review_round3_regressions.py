"""Regression tests for Review Round 3 findings R1-R4."""
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import tempfile
from unittest import TestCase
from unittest.mock import patch

from org_context import cli
from org_context.inventory_context import Context
from org_context.mcp_server import Session
from org_context.reference import load_demo
from org_context.workflow_refs import MAX_REFS, pipeline_metadata


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


class SnapshotConsistencyTests(TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name) / 'repo'
        self.root.mkdir()
        self.db_path = Path(tmp.name) / 'inventory.sqlite'
        self.writer = cli.connect(self.db_path)
        self.writer.execute('PRAGMA journal_mode=WAL')
        self.addCleanup(self.writer.close)

    def test_project_card_single_snapshot_under_concurrent_wal_rescan(self):
        source = self.root / 'azure-pipelines.yml'
        source.write_text('steps:\n- template: old.yml\n', encoding='utf-8')
        old_sha = hashlib.sha256(source.read_bytes()).hexdigest()
        pid = cli.scan(self.writer, self.root)['project_id']

        reader = Context(self.db_path)
        self.addCleanup(reader.close)
        interleaved = []

        def interleave(sql):
            if sql.startswith('SELECT id,path,sha256'):
                reader.db.set_trace_callback(None)
                source.write_text('steps:\n- template: new.yml\n', encoding='utf-8')
                cli.scan(self.writer, self.root)
                interleaved.append(True)

        reader.db.set_trace_callback(interleave)
        card = reader.call('project_card', {'project_id': pid})
        self.assertEqual(interleaved, [True])
        self.assertFalse(reader.db.in_transaction)

        ev = card['evidence'][0]
        # Must stay consistent with the old snapshot, never mixing new sha with old template ref.
        self.assertEqual(ev['sha256'], old_sha)
        self.assertEqual(ev['workflow_declarations']['references'], [{'kind': 'template', 'reference': 'old.yml'}])

        # Subsequent read sees the new snapshot consistently.
        new_sha = hashlib.sha256(source.read_bytes()).hexdigest()
        card2 = reader.call('project_card', {'project_id': pid})
        ev2 = card2['evidence'][0]
        self.assertEqual(ev2['sha256'], new_sha)
        self.assertEqual(ev2['workflow_declarations']['references'], [{'kind': 'template', 'reference': 'new.yml'}])
        self.assertFalse(reader.db.in_transaction)

    def test_project_card_mcp_session_single_snapshot_and_error_cleanup(self):
        source = self.root / 'azure-pipelines.yml'
        source.write_text('steps:\n- template: old.yml\n', encoding='utf-8')
        old_sha = hashlib.sha256(source.read_bytes()).hexdigest()
        pid = cli.scan(self.writer, self.root)['project_id']

        reader = Context(self.db_path)
        self.addCleanup(reader.close)
        session = _init_session(reader)

        def interleave(sql):
            if sql.startswith('SELECT id,path,sha256'):
                reader.db.set_trace_callback(None)
                source.write_text('steps:\n- template: new.yml\n', encoding='utf-8')
                cli.scan(self.writer, self.root)

        reader.db.set_trace_callback(interleave)
        res = _call_tool(session, 'project_card', {'project_id': pid})
        self.assertFalse(res['isError'])
        card = json.loads(res['content'][0]['text'])
        self.assertEqual(card['evidence'][0]['sha256'], old_sha)
        self.assertEqual(card['evidence'][0]['workflow_declarations']['references'], [{'kind': 'template', 'reference': 'old.yml'}])
        self.assertEqual(card['location']['scan_root'], str(self.root.resolve(strict=True)))
        self.assertFalse(reader.db.in_transaction)

        # Error cleans up cleanly and subsequent read succeeds.
        err = _call_tool(session, 'project_card', {'project_id': pid, 'evidence_limit': 99}, rid=3)
        self.assertTrue(err['isError'])
        self.assertFalse(reader.db.in_transaction)
        ok = _call_tool(session, 'project_card', {'project_id': pid}, rid=4)
        self.assertFalse(ok['isError'])
        self.assertEqual(json.loads(ok['content'][0]['text'])['evidence'][0]['workflow_declarations']['references'],
                         [{'kind': 'template', 'reference': 'new.yml'}])


class TemplateContextTests(TestCase):
    def test_parameter_object_default_template_is_not_classified_as_include(self):
        yaml_text = (
            'parameters:\n'
            '- name: data\n'
            '  type: object\n'
            '  default:\n'
            '  - template: data/not-used.yml\n'
            'steps:\n'
            '- script: echo ok\n'
        )
        meta = pipeline_metadata(yaml_text)
        self.assertEqual(meta['references'], [])
        self.assertEqual(meta['omitted_or_over_limit'], 1)
        self.assertNotIn('data/not-used.yml', json.dumps(meta))

    def test_nested_parameter_object_with_steps_and_block_scalars_are_not_includes(self):
        yaml_text = (
            'parameters:\n'
            '- name: cfg\n'
            '  type: object\n'
            '  default:\n'
            '    steps:\n'
            '    - template: data/nested-param.yml\n'
            'steps:\n'
            '- script: |\n'
            '    - template: data/in-script.yml\n'
            '- template: real/step.yml\n'
        )
        meta = pipeline_metadata(yaml_text)
        self.assertEqual(meta['references'], [{'kind': 'template', 'reference': 'real/step.yml'}])
        self.assertEqual(meta['omitted_or_over_limit'], 1)
        self.assertNotIn('nested-param', json.dumps(meta))
        self.assertNotIn('in-script', json.dumps(meta))

    def test_supported_template_contexts_preserved(self):
        yaml_text = (
            'extends:\n'
            '  template: base/root.yml\n'
            'variables:\n'
            '- template: vars/common.yml\n'
            'stages:\n'
            '- template: stages/build.yml@templates\n'
            '- stage: Deploy\n'
            '  jobs:\n'
            '  - template: jobs/lint.yml\n'
            '  - deployment: Ship\n'
            '    strategy:\n'
            '      runOnce:\n'
            '        deploy:\n'
            '          steps:\n'
            '          - template: steps/deploy.yml\n'
            '          - template: ${{ parameters.dynamic }}\n'
        )
        meta = pipeline_metadata(yaml_text)
        self.assertEqual(meta['references'], [
            {'kind': 'template', 'reference': 'base/root.yml'},
            {'kind': 'template', 'reference': 'vars/common.yml'},
            {'kind': 'template', 'reference': 'stages/build.yml@templates'},
            {'kind': 'template', 'reference': 'jobs/lint.yml'},
            {'kind': 'template', 'reference': 'steps/deploy.yml'},
        ])
        self.assertEqual(meta['omitted_or_over_limit'], 1)
        # Verify MAX_REFS cap still holds
        many = 'steps:\n' + ''.join(f'- template: s/t{i}.yml\n' for i in range(MAX_REFS + 3))
        capped = pipeline_metadata(many)
        self.assertEqual(len(capped['references']), MAX_REFS)
        self.assertEqual(capped['omitted_or_over_limit'], 3)


class EvidencePaginationTests(TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name) / 'repo'
        self.root.mkdir()
        wf = self.root / '.github' / 'workflows'
        wf.mkdir(parents=True)
        for i in range(21):
            (wf / f'{i:02}.yml').write_text('jobs: {}\n', encoding='utf-8')
        (self.root / 'azure-pipelines.yml').write_text(
            'stages:\n- template: checks/eval.yaml@templates\n', encoding='utf-8')
        (self.root / 'README.md').write_text('README_BODY_SENTINEL', encoding='utf-8')
        self.db_path = Path(tmp.name) / 'inventory.sqlite'
        db = cli.connect(self.db_path)
        try:
            self.pid = cli.scan(db, self.root)['project_id']
        finally:
            db.close()
        self.ctx = Context(self.db_path)
        self.addCleanup(self.ctx.close)

    def test_mcp_evidence_pagination_and_filtering_beyond_card_limit(self):
        session = _init_session(self.ctx)
        # Verify tool schema exposes evidence paging/filter controls while keeping 5 default tools.
        tools = session.handle({'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'})['result']['tools']
        self.assertEqual(len(tools), 6)
        card_tool = next(t for t in tools if t['name'] == 'project_card')
        for prop in ('evidence_offset', 'evidence_limit', 'evidence_kind', 'evidence_path'):
            self.assertIn(prop, card_tool['inputSchema']['properties'])

        # Page through all 23 evidence records via MCP.
        collected = []
        offset = 0
        rid = 10
        while offset is not None:
            res = _call_tool(session, 'project_card', {'project_id': self.pid, 'evidence_offset': offset, 'evidence_limit': 10}, rid=rid)
            rid += 1
            self.assertFalse(res['isError'])
            raw_text = res['content'][0]['text']
            self.assertNotIn('README_BODY_SENTINEL', raw_text)
            page = json.loads(raw_text)
            self.assertEqual(page['evidence_offset'], offset)
            self.assertEqual(page['evidence_limit'], 10)
            self.assertEqual(page['evidence_order'], 'path, id')
            collected.extend(page['evidence'])
            offset = page['evidence_next_offset']
            if offset is None:
                self.assertFalse(page['evidence_has_more'])
                self.assertFalse(page['evidence_truncated'])
            else:
                self.assertTrue(page['evidence_has_more'])
                self.assertTrue(page['evidence_truncated'])

        paths = [e['path'] for e in collected]
        self.assertEqual(len(paths), 23)
        self.assertEqual(len(set(paths)), 23)
        self.assertEqual(paths, sorted(paths))
        azure = next(e for e in collected if e['path'] == 'azure-pipelines.yml')
        self.assertEqual(azure['workflow_declarations']['references'], [{'kind': 'template', 'reference': 'checks/eval.yaml@templates'}])
        readme = next(e for e in collected if e['path'] == 'README.md')
        self.assertTrue(readme['content_withheld'])
        self.assertIsNone(readme['content_excerpt'])

        # Direct filter by path / kind retrieves Azure metadata in one MCP call.
        filtered_res = _call_tool(session, 'project_card', {
            'project_id': self.pid, 'evidence_kind': 'workflow', 'evidence_path': 'azure-pipelines.yml',
        }, rid=rid)
        self.assertFalse(filtered_res['isError'])
        filtered = json.loads(filtered_res['content'][0]['text'])
        self.assertEqual([e['path'] for e in filtered['evidence']], ['azure-pipelines.yml'])
        self.assertFalse(filtered['evidence_has_more'])
        self.assertIsNone(filtered['evidence_next_offset'])
        self.assertEqual(filtered['evidence'][0]['workflow_declarations']['references'],
                         [{'kind': 'template', 'reference': 'checks/eval.yaml@templates'}])

    def test_evidence_pagination_validation_and_blueprint_rejection(self):
        session = _init_session(self.ctx)
        bad_calls = [
            {'project_id': self.pid, 'evidence_offset': -1},
            {'project_id': self.pid, 'evidence_offset': True},
            {'project_id': self.pid, 'evidence_offset': 100001},
            {'project_id': self.pid, 'evidence_limit': 0},
            {'project_id': self.pid, 'evidence_limit': 21},
            {'project_id': self.pid, 'evidence_limit': False},
            {'project_id': self.pid, 'evidence_kind': 'secret'},
            {'project_id': self.pid, 'evidence_path': ''},
            {'project_id': self.pid, 'evidence_path': 'x' * 257},
            {'project_id': self.pid, 'include_content': True},
        ]
        for idx, args in enumerate(bad_calls, start=20):
            with self.subTest(args=args):
                res = _call_tool(session, 'project_card', args, rid=idx)
                self.assertTrue(res['isError'])

        # get_reference_blueprint must reject evidence pagination controls.
        bp_res = _call_tool(session, 'get_reference_blueprint', {'project_id': self.pid, 'evidence_offset': 0}, rid=50)
        self.assertTrue(bp_res['isError'])

        # Synthetic reference must reject evidence pagination controls.
        with tempfile.TemporaryDirectory() as tmp:
            fixture = {
                'project_id': 'synthetic-test',
                'synthetic': True,
                'claims': [{'id': 'c1', 'statement': 's', 'adaptation': 'a', 'source_path': 'p.txt', 'source_text': 't'}],
                'unknowns': ['Not live'],
            }
            fp = Path(tmp) / 'f.json'
            fp.write_text(json.dumps(fixture), encoding='utf-8')
            ref_db = Path(tmp) / 'ref.sqlite'
            load_demo(ref_db, fp)
            ref_ctx = Context(ref_db)
            try:
                with self.assertRaises(ValueError):
                    ref_ctx.call('project_card', {'project_id': 'synthetic-test', 'evidence_offset': 0})
            finally:
                ref_ctx.close()


class GitRootPrefixWhitespaceTests(TestCase):
    def test_git_root_info_preserves_whitespace_and_handles_edge_cases(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / 'mono'
            repo.mkdir()
            subprocess.run(['git', 'init', '-q', str(repo)], check=True)
            self.assertEqual(cli.git_root_info(repo), {'git_root_relation': 'repository_root', 'git_relative_prefix': ''})

            ordinary = repo / 'components' / 'eval'
            ordinary.mkdir(parents=True)
            self.assertEqual(cli.git_root_info(ordinary), {'git_root_relation': 'subdirectory', 'git_relative_prefix': 'components/eval'})

            leading_space = repo / ' eval'
            leading_space.mkdir(parents=True)
            self.assertEqual(cli.git_root_info(leading_space), {'git_root_relation': 'subdirectory', 'git_relative_prefix': ' eval'})

            # Whitespace-only directory name is valid on POSIX filesystems; test directly where supported.
            space_only = repo / ' '
            try:
                space_only.mkdir()
            except OSError:
                space_only = None
            if space_only is not None and space_only.is_dir():
                self.assertEqual(cli.git_root_info(space_only), {'git_root_relation': 'subdirectory', 'git_relative_prefix': ' '})

        # Cross-platform deterministic parsing checks (including POSIX whitespace-only and invalid prefixes).
        cases = [
            ('\n', {'git_root_relation': 'repository_root', 'git_relative_prefix': ''}),
            ('\r\n', {'git_root_relation': 'repository_root', 'git_relative_prefix': ''}),
            (' eval/\n', {'git_root_relation': 'subdirectory', 'git_relative_prefix': ' eval'}),
            (' /\n', {'git_root_relation': 'subdirectory', 'git_relative_prefix': ' '}),
            ('/\n', {'git_root_relation': 'subdirectory', 'git_relative_prefix': None}),
            ('\x07bad/\n', {'git_root_relation': 'subdirectory', 'git_relative_prefix': None}),
            ('a' * 260 + '/\n', {'git_root_relation': 'subdirectory', 'git_relative_prefix': None}),
        ]
        for stdout, expected in cases:
            with self.subTest(stdout=repr(stdout)):
                fake = subprocess.CompletedProcess(['git'], 0, stdout=stdout, stderr='')
                with patch('org_context.cli.subprocess.run', return_value=fake):
                    self.assertEqual(cli.git_root_info(Path('.')), expected)
