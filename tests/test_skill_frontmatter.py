"""Tests for indexing Claude Code skill/agent frontmatter as declared capabilities."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
from unittest import TestCase, mock

from org_context import cli, skill_frontmatter as sf
from org_context.capabilities import find_capabilities
from org_context.inventory_context import Context
from org_context.mcp_server import Session
from org_context.skill_frontmatter import FrontmatterError, parse_frontmatter


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


def _sample_manifest(cap_id='ground-truth-validation'):
    return {
        'schema_version': 1, 'component': 'example-eval', 'role': 'evaluation',
        'capabilities': [{
            'id': cap_id,
            'summary': 'Locate the implementation that checks evaluation ground truth',
            'responsibility': 'Validate ground-truth inputs used by evaluation',
            'entrypoints': [{'path': 'src/example/ground_truth.py', 'symbol': 'validate_ground_truth'}],
        }],
    }


def _skill(name, description, when_to_use=None, body='Body text.\n'):
    lines = ['---', f'name: {name}', f'description: {description}']
    if when_to_use is not None:
        lines.append(f'when_to_use: {when_to_use}')
    lines.append('---')
    return '\n'.join(lines) + '\n' + body


class ParseFrontmatterTests(TestCase):
    def parse(self, text):
        data = text.encode('utf-8') if isinstance(text, str) else text
        return parse_frontmatter(data)

    def reason(self, text):
        with self.assertRaises(FrontmatterError) as cm:
            self.parse(text)
        return cm.exception.reason

    def test_plain_scalar_and_comment(self):
        out = self.parse('---\nname: alpha # comment\ndescription: Does a: thing\n---\nbody')
        self.assertEqual(out['name'], 'alpha')
        self.assertEqual(out['description'], 'Does a: thing')
        self.assertEqual(out['when_to_use'], '')

    def test_multiline_plain(self):
        out = self.parse('---\nname: a\ndescription: first line\n  second   line\n   third\n---\n')
        self.assertEqual(out['description'], 'first line second line third')

    def test_double_quoted(self):
        out = self.parse('---\nname: a\ndescription: "Say \\"hi\\" caf\\u00e9 and é"\n---\n')
        self.assertEqual(out['description'], 'Say "hi" café and é')

    def test_double_quoted_multiline(self):
        out = self.parse('---\nname: a\ndescription: "one\n  two"\n---\n')
        self.assertEqual(out['description'], 'one two')

    def test_single_quoted(self):
        out = self.parse("---\nname: a\ndescription: 'it''s fine'\n---\n")
        self.assertEqual(out['description'], "it's fine")

    def test_block_scalars(self):
        out = self.parse('---\nname: a\ndescription: >-\n  folded\n  text\nwhen_to_use: |\n  line one\n  line two\n---\n')
        self.assertEqual(out['description'], 'folded text')
        self.assertEqual(out['when_to_use'], 'line one line two')
        out = self.parse('---\nname: a\ndescription: |-\n  kept\n\n  more\n---\n')
        self.assertEqual(out['description'], 'kept more')

    def test_crlf_and_bom(self):
        out = self.parse(b'\xef\xbb\xbf---\r\nname: a\r\ndescription: crlf text\r\n---\r\nbody')
        self.assertEqual(out['description'], 'crlf text')
        self.assertEqual(out['frontmatter_sha256'],
                         hashlib.sha256(b'\xef\xbb\xbf---\r\nname: a\r\ndescription: crlf text\r\n---\r\n').hexdigest())

    def test_ignored_nested_metadata_and_other_keys(self):
        out = self.parse('---\nname: a\nmetadata:\n  author: x\n  tags: [a, b]\n  nested: {k: v}\n'
                         'allowed-tools: Read, Grep\n# comment\n\ndescription: d\n---\n')
        self.assertEqual(out, {'name': 'a', 'description': 'd', 'when_to_use': '',
                               'frontmatter_sha256': out['frontmatter_sha256']})

    def test_closing_dots(self):
        self.assertEqual(self.parse('---\nname: a\ndescription: d\n...\n')['name'], 'a')

    def test_never_returns_body(self):
        out = self.parse('---\nname: a\ndescription: d\n---\nBODY_SECRET_SENTINEL\n')
        self.assertNotIn('BODY_SECRET_SENTINEL', json.dumps(out))

    def test_body_not_decoded(self):
        out = self.parse(b'---\nname: a\ndescription: d\n---\n\xff\xfe invalid utf8')
        self.assertEqual(out['name'], 'a')

    def test_skip_reasons(self):
        cases = {
            'no frontmatter here\n': 'no_frontmatter',
            '': 'no_frontmatter',
            '---\nname: a\ndescription: d\n': 'malformed_frontmatter',
            '---\nname: a\ndescription: {a: b}\n---\n': 'malformed_frontmatter',
            '---\nname: a\ndescription: [a]\n---\n': 'malformed_frontmatter',
            '---\nname: a\ndescription: "unterminated\n---\n': 'malformed_frontmatter',
            '---\nname: a\ndescription: "bad \\x escape"\n---\n': 'malformed_frontmatter',
            "---\nname: a\ndescription: 'open\n---\n": 'malformed_frontmatter',
            '---\nname: a\ndescription: |2\n  x\n---\n': 'malformed_frontmatter',
            '---\nname: a\nname: b\ndescription: d\n---\n': 'malformed_frontmatter',
            '---\nname: a\n\tdescription: d\n---\n': 'malformed_frontmatter',
            '---\nnot a key line\n---\n': 'malformed_frontmatter',
            '---\ndescription: d\n---\n': 'missing_name',
            '---\nname: Bad Name\ndescription: d\n---\n': 'invalid_name',
            '---\nname: a\n---\n': 'missing_description',
            '---\nname: a\ndescription:\n---\n': 'missing_description',
            '---\nname: a\ndescription: ' + 'x' * 1025 + '\n---\n': 'invalid_field',
            '---\nname: a\ndescription: d\nwhen_to_use: ' + 'x' * 1025 + '\n---\n': 'invalid_field',
            '---\nname: a\ndescription: evil \u202e text\n---\n': 'invalid_field',
            '---\nname: a\ndescription: "ctl \\u0001"\n---\n': 'invalid_field',
        }
        for text, expected in cases.items():
            with self.subTest(text=text[:60]):
                self.assertEqual(self.reason(text), expected)
        self.assertEqual(self.reason(b'---\nname: a\ndescription: \xff\n---\n'), 'malformed_frontmatter')

    def test_exactly_1024_ok(self):
        self.assertEqual(len(self.parse('---\nname: a\ndescription: ' + 'x' * 1024 + '\n---\n')['description']), 1024)

    def test_size_and_line_limits(self):
        big = b'---\nname: a\ndescription: d\n' + (b'# ' + b'p' * 1000 + b'\n') * 30
        self.assertEqual(self.reason(big[:sf.FRONTMATTER_MAX_BYTES + 1]), 'frontmatter_too_large')
        many = '---\nname: a\ndescription: d\n' + '# c\n' * 200 + '---\n'
        self.assertEqual(self.reason(many), 'frontmatter_too_large')
        ok = '---\nname: a\ndescription: d\n' + '# c\n' * 198 + '---\n'
        self.assertEqual(self.parse(ok)['name'], 'a')


class SkillScanTests(TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        self.db_path = self.base / 'inventory.sqlite'
        self.db = cli.connect(self.db_path)
        self.addCleanup(self.db.close)
        self.root = self.base / 'market'
        self.root.mkdir()

    def _write(self, rel, text, root=None):
        p = (root or self.root).joinpath(*rel.split('/'))
        p.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(text, bytes):
            p.write_bytes(text)
        else:
            p.write_text(text, encoding='utf-8')
        return p

    def _ctx(self):
        ctx = Context(self.db_path)
        self.addCleanup(ctx.close)
        return ctx

    def _find(self, args):
        return find_capabilities(self._ctx(), args)

    def _fixture(self):
        self._write('plugins/alpha/skills/redactor/SKILL.md',
                    _skill('redactor', 'Lints for private data before publishing',
                           when_to_use='Use before pushing zanzibar content'))
        self._write('skills/reviewer/SKILL.md', _skill('reviewer', 'Reviews pull requests carefully'))
        self._write('agents/triage.md', _skill('triage', 'Sorts incoming issues by severity'))

    def test_skills_found_with_source_and_component(self):  # AC 1
        self._fixture()
        res = cli.scan(self.db, self.root)
        self.assertEqual(res['capability_count'], 3)
        out = self._find({'query': 'private data'})
        self.assertEqual(len(out['results']), 1)
        item = out['results'][0]
        self.assertEqual(item['capability_id'], 'redactor')
        self.assertEqual(item['role'], 'skill')
        self.assertEqual(item['component'], 'alpha')
        self.assertEqual(item['declaration_source'], 'declared_skill_frontmatter')
        self.assertEqual(item['entrypoints'][0]['path'], 'plugins/alpha/skills/redactor/SKILL.md')
        self.assertEqual(item['entrypoints'][0]['path_status'], 'observed_local_file_frontmatter_only')
        self.assertIsNone(item['entrypoints'][0]['file_sha256'])
        self.assertEqual(item['manifest_evidence']['kind'], 'skill_frontmatter')
        self.assertIn('the skill body was not read', item['declaration_semantics'])
        self.assertIn('not runtime verification', item['declaration_semantics'])
        out = self._find({'query': 'pull requests'})
        self.assertEqual([r['capability_id'] for r in out['results']], ['reviewer'])
        self.assertEqual(out['results'][0]['entrypoints'][0]['path'], 'skills/reviewer/SKILL.md')
        self.assertEqual(out['results'][0]['component'], self.root.name)
        self.assertIn('does not prove', self._find({'query': 'nonexistent_xyz'})['unknowns'][0])

    def test_agent_role_filter(self):  # AC 2
        self._fixture()
        cli.scan(self.db, self.root)
        out = self._find({'query': 'severity'})
        self.assertEqual(out['results'][0]['role'], 'agent')
        self.assertEqual(out['results'][0]['component'], self.root.name)
        out = self._find({'query': 's', 'role': 'agent'})
        self.assertEqual([r['capability_id'] for r in out['results']], ['triage'])
        out = self._find({'query': 's', 'role': 'skill'})
        self.assertEqual(sorted(r['capability_id'] for r in out['results']), ['redactor', 'reviewer'])

    def test_when_to_use_searchable(self):  # AC 3
        self._fixture()
        cli.scan(self.db, self.root)
        out = self._find({'query': 'zanzibar'})
        self.assertEqual([r['capability_id'] for r in out['results']], ['redactor'])
        self.assertEqual(out['results'][0]['responsibility'], 'Use before pushing zanzibar content')
        out = self._find({'query': 'pull requests'})
        self.assertEqual(out['results'][0]['responsibility'], '')

    def test_malformed_files_skipped_and_counted(self):  # AC 4
        self._fixture()
        bad = {
            'skills/nofm/SKILL.md': ('no frontmatter\n', 'no_frontmatter'),
            'skills/empty/SKILL.md': ('', 'no_frontmatter'),
            'skills/unclosed/SKILL.md': ('---\nname: unclosed\ndescription: d\n', 'malformed_frontmatter'),
            'skills/flow/SKILL.md': ('---\nname: flow\ndescription: {a: b}\n---\n', 'malformed_frontmatter'),
            'skills/noname/SKILL.md': ('---\ndescription: d\n---\n', 'missing_name'),
            'skills/badname/SKILL.md': ('---\nname: Bad Name\ndescription: d\n---\n', 'invalid_name'),
            'skills/nodesc/SKILL.md': ('---\nname: nodesc\n---\n', 'missing_description'),
            'skills/long/SKILL.md': ('---\nname: long\ndescription: ' + 'x' * 1025 + '\n---\n', 'invalid_field'),
            'skills/bidi/SKILL.md': ('---\nname: bidi\ndescription: a\u202eb\n---\n', 'invalid_field'),
        }
        for rel, (text, _) in bad.items():
            self._write(rel, text)
        res = cli.scan(self.db, self.root)
        cov = res['coverage']['skill_frontmatter']
        self.assertEqual(cov['indexed'], 3)
        self.assertEqual(res['capability_count'], 3)
        listed = {s['path']: s['reason'] for s in cov['skipped']}
        for rel, (_, reason) in bad.items():
            self.assertEqual(listed[rel], reason, rel)
        self.assertEqual(cov['skipped_count'], len(bad))
        self.assertEqual(cov['skipped_by_reason'], {
            'invalid_field': 2, 'invalid_name': 1, 'malformed_frontmatter': 2,
            'missing_description': 1, 'missing_name': 1, 'no_frontmatter': 2})
        for s in cov['skipped']:
            self.assertEqual(set(s), {'path', 'reason'})
        # Skill skips do not leak into the main coverage skipped list.
        self.assertEqual(res['coverage']['skipped'], [])

    def test_byte_bound_and_file_limit(self):  # AC 5
        self._write('skills/huge/SKILL.md', '---\nname: huge\ndescription: d\n' + ('# ' + 'p' * 1000 + '\n') * 40 + '---\n')
        for i in range(70):
            self._write(f'skills/s{i:03d}/SKILL.md', _skill(f's{i:03d}', f'skill number {i}'))
        reads = {}
        real_read = os.read
        real_open = os.open
        opened = []
        root_text = str(self.root)

        def spy_read(fd, n):
            data = real_read(fd, n)
            if fd in reads:
                reads[fd] += len(data)
            return data

        def spy_open(path, flags, *a, **k):
            fd = real_open(path, flags, *a, **k)
            if str(path).startswith(root_text):
                opened.append(str(path))
                reads[fd] = 0
            return fd

        def spy_close(fd):
            if fd in reads:
                total_by_file.append(reads.pop(fd))
            return real_close(fd)

        total_by_file = []
        real_close = os.close

        with mock.patch.object(sf.os, 'read', spy_read), mock.patch.object(sf.os, 'open', spy_open), \
                mock.patch.object(sf.os, 'close', spy_close):
            res = cli.scan(self.db, self.root)
        cov = res['coverage']['skill_frontmatter']
        self.assertEqual(len(opened), 64)
        self.assertEqual(len(total_by_file), 64)
        self.assertEqual(max(total_by_file), sf.FRONTMATTER_MAX_BYTES + 1)  # the huge file hit the cap
        self.assertLessEqual(max(total_by_file), sf.FRONTMATTER_MAX_BYTES + 1)
        listed = {s['path']: s['reason'] for s in cov['skipped']}
        self.assertEqual(listed['skills/huge/SKILL.md'], 'frontmatter_too_large')
        self.assertEqual(cov['candidates_found'], 71)
        self.assertEqual(cov['indexed'], 63)
        self.assertEqual(cov['skipped_by_reason'], {'file_limit': 7, 'frontmatter_too_large': 1})

    def test_seventy_valid_skills(self):  # AC 5
        for i in range(70):
            self._write(f'skills/s{i:03d}/SKILL.md', _skill(f's{i:03d}', f'skill number {i}'))
        cov = cli.scan(self.db, self.root)['coverage']['skill_frontmatter']
        self.assertEqual(cov['indexed'], 64)
        self.assertEqual(cov['skipped_count'], 6)
        self.assertEqual(cov['skipped_by_reason'], {'file_limit': 6})
        self.assertFalse(cov['truncated'])

    def test_candidate_cap_truncates(self):
        with mock.patch.object(sf, 'MAX_SKILL_CANDIDATES', 5):
            for i in range(8):
                self._write(f'skills/s{i}/SKILL.md', _skill(f's{i}', 'd'))
            cov = cli.scan(self.db, self.root)['coverage']['skill_frontmatter']
        self.assertTrue(cov['truncated'])
        self.assertEqual(cov['candidates_found'], 5)

    def test_bodies_never_stored(self):  # AC 6
        text = _skill('secretive', 'Keeps secrets safe', body='BODY_SECRET_SENTINEL\nmore BODY_SECRET_SENTINEL\n')
        path = self._write('skills/secretive/SKILL.md', text)
        res = cli.scan(self.db, self.root)
        self.assertNotIn('BODY_SECRET_SENTINEL', json.dumps(res))
        raw = sqlite3.connect(self.db_path)
        self.addCleanup(raw.close)
        for table in ('evidence', 'capabilities', 'scan_coverage'):
            cols = [r[1] for r in raw.execute(f'PRAGMA table_info({table})')]
            for col in cols:
                hit = raw.execute(f"SELECT COUNT(*) FROM {table} WHERE CAST({col} AS TEXT) LIKE '%BODY_SECRET_SENTINEL%'").fetchone()[0]
                self.assertEqual(hit, 0, f'{table}.{col}')
        content, sha = raw.execute("SELECT content, sha256 FROM evidence WHERE kind='skill_frontmatter'").fetchone()
        self.assertIsNone(content)
        block = path.read_bytes()[:path.read_bytes().index(b'---\n', 4) + 4]
        self.assertEqual(sha, hashlib.sha256(block).hexdigest())
        ctx = self._ctx()
        card = ctx.call('project_card', {'project_id': res['project_id'], 'evidence_kind': 'skill_frontmatter'}, include_content=True)
        self.assertNotIn('BODY_SECRET_SENTINEL', json.dumps(card))
        session = _init_session(ctx)
        found = _call_tool(session, 'find_capabilities', {'query': 'secrets'})['content'][0]['text']
        self.assertIn('secretive', found)
        self.assertNotIn('BODY_SECRET_SENTINEL', found)

    def test_symlinks_skipped_and_not_opened(self):  # AC 8
        outside = self.base / 'outside'
        self._write('SKILL.md', _skill('outsider', 'outside dir skill'), root=outside)
        secret = self._write('secret.md', _skill('secretlink', 'symlinked file'))
        (self.root / 'skills').mkdir()
        (self.root / 'skills' / 'evil').symlink_to(outside, target_is_directory=True)
        (self.root / 'skills' / 'x').mkdir()
        (self.root / 'skills' / 'x' / 'SKILL.md').symlink_to(Path('..') / '..' / 'secret.md')
        opened = []
        real_open = os.open

        def spy_open(path, *a, **k):
            if str(path).startswith(str(self.base)):
                opened.append(str(path))
            return real_open(path, *a, **k)

        with mock.patch.object(sf.os, 'open', spy_open):
            res = cli.scan(self.db, self.root)
        cov = res['coverage']['skill_frontmatter']
        listed = {s['path']: s['reason'] for s in cov['skipped']}
        self.assertEqual(listed, {'skills/evil/SKILL.md': 'symlink', 'skills/x/SKILL.md': 'symlink'})
        self.assertEqual(opened, [])
        self.assertNotIn(str(secret), opened)
        self.assertEqual(cov['indexed'], 0)

    def test_duplicates(self):  # AC 9
        self._write('org-context.manifest.json', json.dumps(_sample_manifest(cap_id='reviewer')))
        self._write('src/example/ground_truth.py', 'x = 1\n')
        self._write('skills/reviewer/SKILL.md', _skill('reviewer', 'skill reviewer'))
        self._write('plugins/b/skills/dup/SKILL.md', _skill('dup', 'second by path'))
        self._write('plugins/a/skills/dup/SKILL.md', _skill('dup', 'first by path'))
        res = cli.scan(self.db, self.root)
        cov = res['coverage']['skill_frontmatter']
        self.assertEqual(res['capability_count'], 2)
        listed = {s['path']: s['reason'] for s in cov['skipped']}
        self.assertEqual(listed, {'skills/reviewer/SKILL.md': 'duplicate_capability_id',
                                  'plugins/b/skills/dup/SKILL.md': 'duplicate_capability_id'})
        out = self._find({'query': 'reviewer'})
        self.assertEqual(len(out['results']), 1)
        self.assertEqual(out['results'][0]['declaration_source'], 'capability_manifest')
        out = self._find({'query': 'dup'})
        self.assertEqual(out['results'][0]['component'], 'a')
        self.assertEqual(out['results'][0]['summary'], 'first by path')

    def test_repo_without_skills_unchanged(self):  # AC 10
        self._write('README.md', 'plain repo')
        self._write('pyproject.toml', '[project]\nname="x"\ndependencies=["requests>=2"]\n')
        self._write('org-context.manifest.json', json.dumps(_sample_manifest()))
        self._write('src/example/ground_truth.py', 'x = 1\n')
        res = cli.scan(self.db, self.root)
        cov = res['coverage']
        self.assertEqual(cov['patterns_checked'], list(cli.PATTERNS))
        self.assertEqual(cov['checked'], ['README.md', 'org-context.manifest.json', 'pyproject.toml'])
        self.assertEqual(res['capability_count'], 1)
        self.assertEqual(res['evidence_count'], 3)
        self.assertEqual(res['dependency_count'], 1)
        skill = cov['skill_frontmatter']
        self.assertEqual(skill['indexed'], 0)
        self.assertEqual(skill['skipped'], [])
        self.assertEqual(skill['candidates_found'], 0)
        self.assertEqual(skill['patterns_checked'], ['skills/*/SKILL.md', 'plugins/*/skills/*/SKILL.md', 'agents/*.md'])
        self.assertEqual(skill['fields_read'], ['name', 'description', 'when_to_use'])
        out = self._find({'query': 'ground truth'})
        self.assertEqual(out['results'][0]['declaration_source'], 'capability_manifest')
        self.assertIn('manifest_declaration', out['results'][0]['declaration_semantics'])

    def test_invalid_manifest_still_fails_root(self):  # AC 11
        self._write('skills/reviewer/SKILL.md', _skill('reviewer', 'Reviews code'))
        first = cli.scan(self.db, self.root)
        self.assertEqual(first['capability_count'], 1)
        self._write('skills/other/SKILL.md', _skill('other', 'Another'))
        self._write('org-context.manifest.json', '{not json')
        with self.assertRaises(ValueError):
            cli.scan(self.db, self.root)
        ids = [r[0] for r in self.db.execute('SELECT capability_id FROM capabilities')]
        self.assertEqual(ids, ['reviewer'])

    def test_rescan_removes_deleted_skill(self):  # AC 12
        self._fixture()
        cli.scan(self.db, self.root)
        (self.root / 'skills' / 'reviewer' / 'SKILL.md').unlink()
        cli.scan(self.db, self.root)
        self.assertEqual(self._find({'query': 'pull requests'})['results'], [])
        paths = [r[0] for r in self.db.execute("SELECT path FROM evidence WHERE kind='skill_frontmatter'")]
        self.assertNotIn('skills/reviewer/SKILL.md', paths)
        ids = sorted(r[0] for r in self.db.execute('SELECT capability_id FROM capabilities'))
        self.assertEqual(ids, ['redactor', 'triage'])

    def test_project_card_evidence_kind(self):  # AC 13
        self._fixture()
        pid = cli.scan(self.db, self.root)['project_id']
        card = self._ctx().call('project_card', {'project_id': pid, 'evidence_kind': 'skill_frontmatter'})
        paths = [e['path'] for e in card['evidence']]
        self.assertEqual(paths, ['agents/triage.md', 'plugins/alpha/skills/redactor/SKILL.md', 'skills/reviewer/SKILL.md'])
        for e in card['evidence']:
            self.assertFalse(e['has_retained_body'])
            self.assertEqual(e['kind'], 'skill_frontmatter')
            self.assertEqual(e['support_status'], 'source_declaration')

    def test_mcp_card_schema_accepts_kind(self):  # AC 13
        self._fixture()
        pid = cli.scan(self.db, self.root)['project_id']
        session = _init_session(self._ctx())
        res = _call_tool(session, 'project_card', {'project_id': pid, 'evidence_kind': 'skill_frontmatter'})
        self.assertFalse(res['isError'])
        self.assertIn('skills/reviewer/SKILL.md', res['content'][0]['text'])
