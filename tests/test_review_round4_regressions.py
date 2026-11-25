"""Regression tests for Review Round 4 findings (R2 follow-up and R5)."""
import json
from pathlib import Path
import tempfile
from unittest import TestCase

from org_context import cli
from org_context.inventory_context import Context
from org_context.mcp_server import Session
from org_context.workflow_refs import MAX_REFS, pipeline_metadata


def _init_session(ctx):
    session = Session(ctx)
    session.handle({
        'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
        'params': {'protocolVersion': '2025-03-26', 'capabilities': {}, 'clientInfo': {'name': 't', 'version': '1'}},
    })
    session.handle({'jsonrpc': '2.0', 'method': 'notifications/initialized'})
    return session


def _scan_and_get_azure_declarations(yaml_text):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / 'repo'
        root.mkdir()
        (root / 'azure-pipelines.yml').write_text(yaml_text, encoding='utf-8')
        db_path = Path(tmp) / 'inventory.sqlite'
        db = cli.connect(db_path)
        try:
            pid = cli.scan(db, root)['project_id']
        finally:
            db.close()
        ctx = Context(db_path)
        try:
            session = _init_session(ctx)
            res = session.handle({
                'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call',
                'params': {'name': 'project_card', 'arguments': {
                    'project_id': pid, 'evidence_path': 'azure-pipelines.yml',
                }},
            })['result']
            assert not res['isError'], res
            payload = res['content'][0]['text']
            card = json.loads(payload)
            return card['evidence'][0]['workflow_declarations'], payload
        finally:
            ctx.close()


class QuotedParameterContextTests(TestCase):
    def test_plain_single_and_double_quoted_parameters_exclude_nested_sections(self):
        for param_key in ('parameters', "'parameters'", '"parameters"'):
            for nested_section in ('steps', 'jobs', 'stages', 'variables'):
                with self.subTest(param_key=param_key, nested_section=nested_section):
                    yaml_text = (
                        'steps:\n'
                        '- template: steps/real.yml\n'
                        f'  {param_key}:\n'
                        f'    {nested_section}:\n'
                        '    - template: data/not-used.yml\n'
                        '- template: steps/adjacent-real.yml\n'
                    )
                    meta = pipeline_metadata(yaml_text)
                    self.assertEqual(meta['references'], [
                        {'kind': 'template', 'reference': 'steps/real.yml'},
                        {'kind': 'template', 'reference': 'steps/adjacent-real.yml'},
                    ])
                    self.assertEqual(meta['omitted_or_over_limit'], 1)
                    self.assertNotIn('data/not-used.yml', json.dumps(meta))

    def test_quoted_parameters_regression_through_scan_and_mcp(self):
        yaml_text = (
            'steps:\n'
            '- template: steps/real.yml\n'
            '  "parameters":\n'
            '    steps:\n'
            '    - template: data/not-used.yml\n'
        )
        decls, payload = _scan_and_get_azure_declarations(yaml_text)
        self.assertEqual(decls['references'], [{'kind': 'template', 'reference': 'steps/real.yml'}])
        self.assertEqual(decls['omitted_or_over_limit'], 1)
        self.assertNotIn('data/not-used.yml', payload)

    def test_unknown_quoted_or_arbitrary_mapping_key_does_not_inherit_trusted_context(self):
        yaml_text = (
            'steps:\n'
            '- template: steps/real.yml\n'
            '  "custom object":\n'
            '    steps:\n'
            '    - template: data/from-custom.yml\n'
            '  custom.dotted:\n'
            '    jobs:\n'
            '    - template: data/from-dotted.yml\n'
        )
        meta = pipeline_metadata(yaml_text)
        self.assertEqual(meta['references'], [{'kind': 'template', 'reference': 'steps/real.yml'}])
        self.assertEqual(meta['omitted_or_over_limit'], 2)
        self.assertNotIn('from-custom', json.dumps(meta))
        self.assertNotIn('from-dotted', json.dumps(meta))


class ConditionalSiblingAncestryTests(TestCase):
    def test_conditional_sibling_after_parameters_under_jobs_steps_stages(self):
        for section in ('jobs', 'steps', 'stages'):
            with self.subTest(section=section):
                full_yaml = (
                    f'{section}:\n'
                    f'- template: {section}/first.yml\n'
                    '  parameters:\n'
                    '    mode: fast\n'
                    '- ${{ if true }}:\n'
                    f'  - template: {section}/second.yml\n'
                )
                reordered_yaml = (
                    f'{section}:\n'
                    '- ${{ if true }}:\n'
                    f'  - template: {section}/second.yml\n'
                    f'- template: {section}/first.yml\n'
                    '  parameters:\n'
                    '    mode: fast\n'
                )
                alone_yaml = (
                    f'{section}:\n'
                    '- ${{ if true }}:\n'
                    f'  - template: {section}/second.yml\n'
                )
                full = pipeline_metadata(full_yaml)
                reordered = pipeline_metadata(reordered_yaml)
                alone = pipeline_metadata(alone_yaml)

                self.assertEqual(full['references'], [
                    {'kind': 'template', 'reference': f'{section}/first.yml'},
                    {'kind': 'template', 'reference': f'{section}/second.yml'},
                ])
                self.assertEqual(full['omitted_or_over_limit'], 0)
                self.assertEqual(
                    {r['reference'] for r in reordered['references']},
                    {f'{section}/first.yml', f'{section}/second.yml'},
                )
                self.assertEqual(reordered['omitted_or_over_limit'], 0)
                self.assertEqual(alone['references'], [{'kind': 'template', 'reference': f'{section}/second.yml'}])
                self.assertEqual(alone['omitted_or_over_limit'], 0)

    def test_conditional_sibling_regression_through_scan_and_mcp(self):
        yaml_text = (
            'jobs:\n'
            '- template: jobs/first.yml\n'
            '  parameters:\n'
            '    mode: fast\n'
            '- ${{ if true }}:\n'
            '  - template: jobs/second.yml\n'
        )
        decls, _ = _scan_and_get_azure_declarations(yaml_text)
        self.assertEqual(decls['references'], [
            {'kind': 'template', 'reference': 'jobs/first.yml'},
            {'kind': 'template', 'reference': 'jobs/second.yml'},
        ])
        self.assertEqual(decls['omitted_or_over_limit'], 0)

    def test_conditional_inside_parameter_object_remains_excluded(self):
        yaml_text = (
            'jobs:\n'
            '- template: jobs/first.yml\n'
            '  parameters:\n'
            '    extra:\n'
            '    - ${{ if true }}:\n'
            '      - template: data/cond-in-param.yml\n'
            '    "nested":\n'
            '    - ${{ each x in parameters.items }}:\n'
            '      steps:\n'
            '      - template: data/each-in-param.yml\n'
            '- ${{ if true }}:\n'
            '  - template: jobs/second.yml\n'
        )
        meta = pipeline_metadata(yaml_text)
        self.assertEqual(meta['references'], [
            {'kind': 'template', 'reference': 'jobs/first.yml'},
            {'kind': 'template', 'reference': 'jobs/second.yml'},
        ])
        self.assertEqual(meta['omitted_or_over_limit'], 2)
        self.assertNotIn('cond-in-param', json.dumps(meta))
        self.assertNotIn('each-in-param', json.dumps(meta))

    def test_block_scalar_dynamic_omissions_and_max_refs_preserved(self):
        yaml_text = (
            'steps:\n'
            '- "script": |\n'
            '    - template: fake/in-quoted-script.yml\n'
            '- ${{ if true }}:\n'
            '  - template: ${{ parameters.dynamic }}\n'
            '  - template: steps/valid.yml\n'
        )
        meta = pipeline_metadata(yaml_text)
        self.assertEqual(meta['references'], [{'kind': 'template', 'reference': 'steps/valid.yml'}])
        self.assertEqual(meta['omitted_or_over_limit'], 1)
        self.assertNotIn('in-quoted-script', json.dumps(meta))

        many = 'jobs:\n- ${{ if true }}:\n' + ''.join(f'  - template: jobs/j{i}.yml\n' for i in range(MAX_REFS + 2))
        capped = pipeline_metadata(many)
        self.assertEqual(len(capped['references']), MAX_REFS)
        self.assertEqual(capped['omitted_or_over_limit'], 2)


class InlineCommentContextTests(TestCase):
    def test_reproduction_a_comment_on_parameters_through_scan_and_mcp(self):
        yaml_text = (
            'steps:\n'
            '- template: steps/real.yml\n'
            '  parameters: # configuration\n'
            '    steps:\n'
            '    - template: data/not-used.yml\n'
            '- template: steps/after.yml\n'
        )
        decls, payload = _scan_and_get_azure_declarations(yaml_text)
        self.assertEqual(decls['references'], [
            {'kind': 'template', 'reference': 'steps/real.yml'},
            {'kind': 'template', 'reference': 'steps/after.yml'},
        ])
        self.assertEqual(decls['omitted_or_over_limit'], 1)
        self.assertNotIn('data/not-used.yml', payload)

    def test_reproduction_b_comment_on_root_section_preserves_valid_template(self):
        for section in ('steps', 'jobs', 'stages', 'variables'):
            with self.subTest(section=section):
                yaml_text = f'{section}: # pipeline {section}\n- template: {section}/real.yml\n'
                meta = pipeline_metadata(yaml_text)
                self.assertEqual(meta['references'], [{'kind': 'template', 'reference': f'{section}/real.yml'}])
                self.assertEqual(meta['omitted_or_over_limit'], 0)

    def test_comment_invariance_matrix_for_quoted_and_unquoted_parameters(self):
        for param_key in ('parameters', "'parameters'", '"parameters"'):
            for nested_section in ('steps', 'jobs', 'stages', 'variables'):
                for comment in ('', ' # configuration'):
                    with self.subTest(param_key=param_key, nested_section=nested_section, comment=comment):
                        yaml_text = (
                            'steps: # root\n'
                            '- template: steps/real.yml\n'
                            f'  {param_key}:{comment}\n'
                            f'    {nested_section}:{comment}\n'
                            '    - template: data/not-used.yml\n'
                            '- template: steps/after.yml\n'
                        )
                        meta = pipeline_metadata(yaml_text)
                        self.assertEqual(meta['references'], [
                            {'kind': 'template', 'reference': 'steps/real.yml'},
                            {'kind': 'template', 'reference': 'steps/after.yml'},
                        ])
                        self.assertEqual(meta['omitted_or_over_limit'], 1)
                        self.assertNotIn('data/not-used.yml', json.dumps(meta))

    def test_conditional_keys_with_comments_and_quoted_hash_scalars(self):
        yaml_text = (
            'jobs: # top-level jobs\n'
            '- template: jobs/first.yml\n'
            '  parameters: # job params\n'
            '    label: "# not a comment"\n'
            '    note: \'# also not a comment\' # trailing\n'
            '    steps: # nested param steps\n'
            '    - template: data/not-used.yml\n'
            '- ${{ if true }}: # conditional sibling\n'
            '  - template: jobs/second.yml # valid ref\n'
            '  - template: "dir/file #1.yml" # invalid filename with hash\n'
        )
        meta = pipeline_metadata(yaml_text)
        self.assertEqual(meta['references'], [
            {'kind': 'template', 'reference': 'jobs/first.yml'},
            {'kind': 'template', 'reference': 'jobs/second.yml'},
        ])
        self.assertEqual(meta['omitted_or_over_limit'], 2)
        self.assertNotIn('not-used', json.dumps(meta))
        self.assertNotIn('file #1', json.dumps(meta))

        # Block scalar with inline comment must still skip its body.
        block_yaml = (
            'steps: # steps\n'
            '- script: | # shell block\n'
            '    - template: fake/in-block.yml\n'
            '- template: steps/real.yml\n'
        )
        block_meta = pipeline_metadata(block_yaml)
        self.assertEqual(block_meta['references'], [{'kind': 'template', 'reference': 'steps/real.yml'}])
        self.assertEqual(block_meta['omitted_or_over_limit'], 0)
        self.assertNotIn('in-block', json.dumps(block_meta))
