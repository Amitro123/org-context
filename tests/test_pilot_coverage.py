import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from org_context import cli
from org_context.discovery import call
from org_context.inventory_context import Context
from org_context.mcp_server import Session
from org_context.workflow_refs import MAX_REFS, pipeline_metadata


class PilotCoverageTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name) / 'repo'
        self.root.mkdir()
        db_path = self.root.parent / 'inventory.sqlite'
        self.db = cli.connect(db_path)
        self.addCleanup(self.db.close)
        self.ctx = Context(db_path)
        self.addCleanup(self.ctx.close)

    def test_default_mcp_shows_bounded_ci_metadata_without_body(self):
        (self.root / 'azure-pipelines.yml').write_text(
            "resources:\n"
            "  repositories:\n"
            "  - repository: templates\n"
            "    type: git\n"
            "    name: Example/shared_pipelines\n"
            "variables:\n"
            "  template: FAKE_VARIABLE_SENTINEL.yml\n"
            "stages:\n"
            "- template: 'checks/eval.yaml@templates' # declaration\n"
            "- template: ${{ variables.BAD_DYNAMIC_SENTINEL }}\n"
            "- script: |\n"
            "    template: FAKE_SCRIPT_SENTINEL.yml\n"
            "    BODY_SENTINEL\n", encoding='utf-8')
        (self.root / 'azure-pipelines-extra.yaml').write_text(
            'extends:\n  template: checks/base.yml\n', encoding='utf-8')
        ignored = self.root / 'nested'
        ignored.mkdir()
        (ignored / 'azure-pipelines.yml').write_text(
            '- template: IGNORED_NESTED.yml\n', encoding='utf-8')
        (self.root / 'README.md').write_text('README_BODY_SENTINEL', encoding='utf-8')
        pid = cli.scan(self.db, self.root)['project_id']
        session = Session(self.ctx)
        session.handle({'jsonrpc':'2.0','id':1,'method':'initialize',
                        'params':{'protocolVersion':'2025-03-26','capabilities':{},'clientInfo':{}}})
        session.handle({'jsonrpc':'2.0','method':'notifications/initialized'})
        result = session.handle({'jsonrpc':'2.0','id':2,'method':'tools/call',
                                 'params':{'name':'project_card','arguments':{'project_id':pid}}})['result']
        self.assertFalse(result['isError'])
        payload = result['content'][0]['text']
        self.assertNotIn('SENTINEL', payload)
        self.assertNotIn('IGNORED_NESTED', payload)
        card = json.loads(payload)
        self.assertEqual(card['freshness'], 'unknown')
        self.assertIn('full YAML semantics', card['coverage']['unsupported'])
        self.assertEqual(card['coverage']['workflow_reference_metadata'], 'recorded')
        evidence = {row['path']:row for row in card['evidence']}
        self.assertTrue(all(row['content_excerpt'] is None for row in evidence.values()))
        declarations = evidence['azure-pipelines.yml']['workflow_declarations']
        self.assertEqual(declarations['references'], [
            {'kind':'repository','alias':'templates','reference':'Example/shared_pipelines'},
            {'kind':'template','reference':'checks/eval.yaml@templates'},
        ])
        self.assertEqual(declarations['omitted_or_over_limit'], 1)
        self.assertIn('not YAML validation', declarations['semantics'])
        self.assertEqual(evidence['azure-pipelines-extra.yaml']['workflow_declarations']['references'],
                         [{'kind':'template','reference':'checks/base.yml'}])

    def test_discovery_limits_visible_even_when_no_declaration_matches(self):
        cli.scan(self.db, self.root)
        for tool, args in [('list_projects', {}), ('find_projects_using', {'technology':'pydantic'}),
                           ('shared_dependencies', {})]:
            with self.subTest(tool=tool):
                result = call(self.ctx, tool, args)
                self.assertEqual(result['freshness'], 'unknown')
                self.assertIn('explicit scanned roots', result['coverage_scope'])
                self.assertIn('runtime state', result['not_verified'])
                if tool != 'list_projects':
                    self.assertIn('lockfiles', result['unsupported_for_dependency_discovery'])
                    self.assertIn('does not prove absence', result['coverage_details'])

    def test_old_snapshot_does_not_imply_workflow_reference_coverage(self):
        pid = cli.scan(self.db, self.root)['project_id']
        payload = self.db.execute('SELECT payload FROM scan_coverage').fetchone()[0]
        coverage = json.loads(payload)
        del coverage['workflow_references_by_path']
        with self.db:
            self.db.execute('UPDATE scan_coverage SET payload=?', (json.dumps(coverage),))
        card = self.ctx.call('project_card', {'project_id':pid})
        self.assertEqual(card['coverage']['workflow_reference_metadata'], 'unknown; rescan required')

    def test_lexical_extraction_counts_and_withholds_unmatched_values(self):
        refs = pipeline_metadata('steps:\n  - bash: |\n      template: example/hidden.yml\n'
                                 '  - template: https://PRIVATE_SENTINEL.example/path.yml\n'
                                 + ''.join(f'  - template: example/action{i}.yml@fixtures\n'
                                           for i in range(MAX_REFS + 2)))
        self.assertEqual(len(refs['references']), MAX_REFS)
        self.assertEqual(refs['omitted_or_over_limit'], 3)
        self.assertNotIn('hidden', json.dumps(refs))
        self.assertNotIn('PRIVATE_SENTINEL', json.dumps(refs))

    def test_monorepo_git_root_relation_and_legacy_fallback(self):
        # Non-git directory
        non_git_pid = cli.scan(self.db, self.root)['project_id']
        non_git_card = self.ctx.call('project_card', {'project_id': non_git_pid})
        self.assertEqual(non_git_card['coverage']['git_root_relation'], 'not_git_or_unknown')
        self.assertIsNone(non_git_card['coverage']['git_relative_prefix'])

        # Real git monorepo with component subdirectory
        mono = self.root.parent / 'monorepo'
        sub = mono / 'components' / 'eval'
        sub.mkdir(parents=True)
        (mono / 'README.md').write_text('monorepo root', encoding='utf-8')
        (sub / 'pyproject.toml').write_text('[project]\nname = "eval"\ndependencies = ["pydantic>=2"]\n', encoding='utf-8')
        subprocess.run(['git', '-C', str(mono), 'init'], check=True, capture_output=True)
        root_pid = cli.scan(self.db, mono)['project_id']
        sub_pid = cli.scan(self.db, sub)['project_id']
        root_card = self.ctx.call('project_card', {'project_id': root_pid})
        sub_card = self.ctx.call('project_card', {'project_id': sub_pid})
        self.assertEqual(root_card['coverage']['git_root_relation'], 'repository_root')
        self.assertEqual(root_card['coverage']['git_relative_prefix'], '')
        self.assertEqual(sub_card['coverage']['git_root_relation'], 'subdirectory')
        self.assertEqual(sub_card['coverage']['git_relative_prefix'], 'components/eval')

        # Legacy snapshot without git_root_relation
        payload = self.db.execute('SELECT payload FROM scan_coverage WHERE project_id=?', (sub_pid,)).fetchone()[0]
        coverage = json.loads(payload)
        del coverage['git_root_relation']
        with self.db:
            self.db.execute('UPDATE scan_coverage SET payload=? WHERE project_id=?', (json.dumps(coverage), sub_pid))
        legacy_card = self.ctx.call('project_card', {'project_id': sub_pid})
        self.assertEqual(legacy_card['coverage']['git_root_relation'], 'unknown; rescan required')
