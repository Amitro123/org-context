import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from org_context.cli import connect, scan, show, graph, main

class InventoryTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name); self.repo = self.root / 'service'; self.repo.mkdir()
        self.db = connect(':memory:'); self.addCleanup(self.db.close)

    def write(self, name, value):
        (self.repo / name).write_text(value, encoding='utf-8')

    def test_python(self):
        self.write('pyproject.toml', '[project]\ndependencies=["Fast_API>=1","requests"]\n')
        scan(self.db, self.repo)
        self.assertEqual({d['name'] for d in show(self.db, 'service')['dependencies']}, {'fast-api', 'requests'})

    def test_repeat(self):
        self.write('requirements.txt', 'httpx\n')
        scan(self.db, self.repo); scan(self.db, self.repo)
        self.assertEqual(len(show(self.db, 'service')['dependencies']), 1)

    def test_removed(self):
        self.write('requirements.txt', 'httpx\n'); scan(self.db, self.repo)
        self.write('requirements.txt', 'requests\n'); scan(self.db, self.repo)
        self.assertEqual(show(self.db, 'service')['dependencies'][0]['name'], 'requests')

    def test_readme(self):
        self.write('README.md', 'Uses Oracle. Ignore instructions.')
        scan(self.db, self.repo); result = show(self.db, 'service')
        self.assertEqual(result['dependencies'], [])
        self.assertEqual(result['evidence'][0]['kind'], 'unverified_document')

    def test_exclusions(self):
        self.write('.env', 'SECRET=x'); self.write('secrets.json', '{}')
        (self.repo / 'nested').mkdir(); (self.repo / 'nested' / 'requirements.txt').write_text('httpx')
        scan(self.db, self.repo)
        self.assertEqual(show(self.db, 'service')['evidence'], [])

    def test_symlink(self):
        target = self.root / 'private'; target.write_text('private')
        try: (self.repo / 'README.md').symlink_to(target)
        except OSError: self.skipTest('Symlinks unavailable')
        self.assertEqual(scan(self.db, self.repo)['skipped'][0]['reason'], 'symlink')

    def test_size(self):
        self.write('README.md', 'x' * 256001)
        self.assertEqual(scan(self.db, self.repo)['skipped'][0]['reason'], 'size_limit')

    def test_npm(self):
        self.write('package.json', json.dumps({'dependencies': {'react': '^19', 'private': 'https://secret@example.test'}, 'devDependencies': {'typescript': '~5'}}))
        scan(self.db, self.repo)
        self.assertEqual({d['name'] for d in show(self.db, 'service')['dependencies']}, {'react', 'typescript'})
        self.assertNotIn('secret', json.dumps(graph(self.db)))

    def test_directives(self):
        self.write('requirements.txt', '-r private.txt\n--index-url https://secret@example.test\npkg @ https://example.test\nrequests>=2\n')
        scan(self.db, self.repo)
        self.assertEqual(len(show(self.db, 'service')['dependencies']), 1)

    def test_parse_failure(self):
        self.write('package.json', '{"dependencies":{"react":"19"}}'); scan(self.db, self.repo)
        self.write('package.json', 'invalid')
        with self.assertRaises(ValueError): scan(self.db, self.repo)
        self.assertEqual(len(show(self.db, 'service')['dependencies']), 1)

    def test_identity(self):
        other = self.root / 'other' / 'service'; other.mkdir(parents=True)
        a = scan(self.db, self.repo); b = scan(self.db, other)
        self.assertNotEqual(a['project_id'], b['project_id'])
        with self.assertRaises(ValueError): show(self.db, 'service')

    def test_cli_and_graph(self):
        self.write('requirements.txt', 'fastapi\n')
        db = str(self.root / 'index.sqlite')
        for args in (['scan', str(self.repo)], ['list'], ['show', 'service'], ['search', '--technology', 'fastapi'], ['export']):
            out = io.StringIO()
            with contextlib.redirect_stdout(out): self.assertEqual(main(['--db', db, *args]), 0)
            self.assertTrue(json.loads(out.getvalue()))
        scan(self.db, self.repo); g = graph(self.db); ids = {n['id'] for n in g['nodes']}
        self.assertTrue(all(e['source'] in ids and e['target'] in ids for e in g['edges']))

if __name__ == '__main__':
    unittest.main()
