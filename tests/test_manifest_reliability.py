"""Manifest scan reliability: oversized manifests, reference read budget and path identity."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock
from org_context import capabilities, cli


def manifest(entry='src/a.py', refs=()):
    return {
        'schema_version': 1, 'component': 'svc', 'role': 'service',
        'capabilities': [{
            'id': 'cap-one', 'summary': 'Does one thing', 'responsibility': 'Owns one thing',
            'entrypoints': [{'path': entry}], 'references': list(refs), 'out_of_scope': [],
        }],
    }


class Base(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.repo = self.root / 'service'
        self.repo.mkdir()
        self.db_path = self.root / 'inventory.sqlite'
        self.db = cli.connect(self.db_path)
        self.addCleanup(self.db.close)

    def write(self, rel, data):
        p = self.repo.joinpath(*rel.split('/'))
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data if isinstance(data, bytes) else data.encode('utf-8'))
        return p

    def write_manifest(self, doc, pad=0):
        text = json.dumps(doc) + ' ' * pad
        return self.write('org-context.manifest.json', text)

    def state(self):
        return {t: sorted(repr(tuple(r)) for r in self.db.execute('SELECT * FROM ' + t))
                for t in ('projects', 'evidence', 'dependencies', 'capabilities', 'scan_coverage')}

    def count(self, table):
        try:
            return self.db.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0]
        except Exception:
            return 0


class OversizedManifestTests(Base):  # R6
    def test_oversized_first_scan_fails_and_stores_nothing(self):
        self.write('src/a.py', 'x = 1\n')
        self.write_manifest(manifest(), pad=cli.MAX_BYTES)
        with self.assertRaisesRegex(ValueError, 'size limit'):
            cli.scan(self.db, self.repo)
        self.assertEqual(self.count('capabilities'), 0)
        self.assertEqual(self.count('projects'), 0)

    def test_oversized_rescan_preserves_previous_snapshot(self):
        self.write('src/a.py', 'x = 1\n')
        self.write('requirements.txt', 'requests==2.0\n')
        self.write_manifest(manifest())
        cli.scan(self.db, self.repo)
        before = self.state()
        self.assertEqual(self.count('capabilities'), 1)
        self.write_manifest(manifest(), pad=cli.MAX_BYTES)
        with self.assertRaises(ValueError):
            cli.scan(self.db, self.repo)
        self.assertEqual(self.state(), before)
        self.assertEqual(self.count('capabilities'), 1)

    def test_corrected_manifest_scans_again(self):
        self.write('src/a.py', 'x = 1\n')
        self.write_manifest(manifest(), pad=cli.MAX_BYTES)
        with self.assertRaises(ValueError):
            cli.scan(self.db, self.repo)
        self.write_manifest(manifest())
        self.assertEqual(cli.scan(self.db, self.repo)['capability_count'], 1)

    def test_removing_the_manifest_clears_capabilities(self):
        self.write('src/a.py', 'x = 1\n')
        path = self.write_manifest(manifest())
        cli.scan(self.db, self.repo)
        path.unlink()
        self.assertEqual(cli.scan(self.db, self.repo)['capability_count'], 0)

    def test_other_oversized_files_are_still_skipped(self):
        self.write('requirements.txt', 'a' * (cli.MAX_BYTES + 1))
        res = cli.scan(self.db, self.repo)
        self.assertIn({'path': 'requirements.txt', 'reason': 'size_limit'}, res['skipped'])


class ReferenceBudgetTests(Base):  # R7
    def test_references_count_against_the_scan_budget(self):
        refs = []
        for i in range(9):
            refs.append('docs/r%d.md' % i)
            self.write('docs/r%d.md' % i, b'x' * 250_000)
        self.write('src/a.py', 'x = 1\n')
        self.write_manifest(manifest(refs=refs))
        with self.assertRaisesRegex(ValueError, 'budget'):
            cli.scan(self.db, self.repo)
        self.assertEqual(self.count('capabilities'), 0)

    def test_failed_budget_scan_preserves_previous_snapshot(self):
        self.write('src/a.py', 'x = 1\n')
        self.write_manifest(manifest())
        cli.scan(self.db, self.repo)
        before = self.state()
        refs = []
        for i in range(9):
            refs.append('docs/r%d.md' % i)
            self.write('docs/r%d.md' % i, b'x' * 250_000)
        self.write_manifest(manifest(refs=refs))
        with self.assertRaises(ValueError):
            cli.scan(self.db, self.repo)
        self.assertEqual(self.state(), before)

    def test_repeated_references_are_read_once(self):
        self.write('src/a.py', b'x' * 200_000)
        self.write_manifest(manifest(refs=['src/a.py'] * 5))
        real_open = Path.open
        reads = []

        def spy(self_, *a, **k):
            if self_.name == 'a.py':
                reads.append(1)
            return real_open(self_, *a, **k)
        with mock.patch.object(Path, 'open', spy):
            res = cli.scan(self.db, self.repo)
        self.assertEqual(res['capability_count'], 1)
        self.assertEqual(len(reads), 1)

    def test_references_within_budget_still_work(self):
        self.write('src/a.py', 'x = 1\n')
        self.write('docs/r.md', 'ref\n')
        self.write_manifest(manifest(refs=['docs/r.md']))
        cli.scan(self.db, self.repo)
        payload = json.loads(self.db.execute('SELECT payload FROM capabilities').fetchone()[0])
        self.assertEqual(payload['references'][0]['file_sha256'], hashlib.sha256(b'ref\n').hexdigest())


class PathIdentityTests(Base):  # R8
    def test_leading_space_path_is_rejected_not_aliased(self):
        self.write(' code.py', 'EXPECTED\n')
        self.write('code.py', 'DIFFERENT\n')
        self.write_manifest(manifest(entry=' code.py'))
        with self.assertRaisesRegex(ValueError, 'whitespace'):
            cli.scan(self.db, self.repo)

    def test_trailing_space_and_reference_paths_are_rejected(self):
        self.write('code.py', 'x\n')
        self.write_manifest(manifest(entry='code.py '))
        with self.assertRaises(ValueError):
            cli.scan(self.db, self.repo)
        self.write_manifest(manifest(entry='code.py', refs=[' code.py']))
        with self.assertRaises(ValueError):
            cli.scan(self.db, self.repo)

    def test_inner_spaces_keep_their_identity(self):
        self.write('my dir/code.py', 'ONE\n')
        self.write('my  dir/code.py', 'TWO\n')
        self.write_manifest(manifest(entry='my  dir/code.py'))
        cli.scan(self.db, self.repo)
        payload = json.loads(self.db.execute('SELECT payload FROM capabilities').fetchone()[0])
        self.assertEqual(payload['entrypoints'][0]['file_sha256'], hashlib.sha256(b'TWO\n').hexdigest())

    def test_display_text_is_still_trimmed(self):
        self.write('code.py', 'x\n')
        doc = manifest(entry='code.py')
        doc['capabilities'][0]['summary'] = '  Does one thing  '
        self.write_manifest(doc)
        cli.scan(self.db, self.repo)
        self.assertEqual(self.db.execute('SELECT summary FROM capabilities').fetchone()[0], 'Does one thing')


if __name__ == '__main__':
    unittest.main()
