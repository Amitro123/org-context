"""Differential test: the lexical CI extractor against PyYAML's reading of generated workflows.

PyYAML is a test-time oracle only (never a dependency): the test is skipped when it is missing.
For every generated workflow the install commands the extractor finds must equal the ones found in a
canonical rewrite of the commands PyYAML resolves, and decoy data (names, env, with) never leaks in.
"""
from collections import Counter
import random
import unittest
from org_context.ci_installs import ci_install_declarations

try:
    import yaml
except ImportError:  # pragma: no cover - depends on the environment
    yaml = None

SEEDS = range(400)


def extract(text):
    return [(r[0], r[1], r[2]) for r in ci_install_declarations(text, 'github', 'ci-install:x', Counter())]


def canonical(commands):
    out = ['jobs:', '  t:', '    steps:']
    for cmd in commands:
        out.append('    - run: |2')
        out.extend('        ' + line for line in cmd.split('\n'))
    return '\n'.join(out) + '\n'


class Gen:
    def __init__(self, seed):
        self.r = random.Random(seed)
        self.n = 0

    def pkg(self):
        self.n += 1
        return 'pkg%d' % self.n

    def phantom(self):
        self.n += 1
        return 'phantom%d' % self.n

    def command(self, ind):
        r, pad = self.r, ' ' * ind
        kind = r.choice(['plain', 'dq', 'sq', 'plain_multi', 'lit', 'lit_strip', 'fold', 'fold_strip', 'fold_keep',
                         'fold_ind', 'lit_ind', 'fold_blank', 'comment'])
        a, b = self.pkg(), self.pkg()
        key = ' ' * 6 + 'run:'  # the key sits at column 6 for "    - run:"
        if kind == 'plain':
            return ' pip install %s' % a
        if kind == 'dq':
            return ' "pip install %s && npm i %s"' % (a, b)
        if kind == 'sq':
            return " 'pip install %s'" % a
        if kind == 'plain_multi':
            return ' pip install\n%s  %s' % (pad, a)
        if kind in ('lit', 'lit_strip'):
            return ' |%s\n%s  echo hi\n%s  pip install %s\n%s  npm i %s' % ('-' if kind == 'lit_strip' else '', pad, pad, a, pad, b)
        if kind in ('fold', 'fold_strip', 'fold_keep'):
            chomp = {'fold': '', 'fold_strip': '-', 'fold_keep': '+'}[kind]
            return ' >%s\n%s  pip install\n%s  %s\n%s\n%s  npm i %s' % (chomp, pad, pad, a, pad, pad, b)
        if kind == 'fold_ind':
            return ' >2\n%s    pip install %s\n%s    %s' % (pad, a, pad, self.phantom())
        if kind == 'lit_ind':
            return ' |2\n%s    pip install %s\n%s    %s' % (pad, a, pad, b)
        if kind == 'fold_blank':
            return ' >-\n\n%s  pip install %s\n\n%s  npm i %s\n%s    indented %s' % (pad, a, pad, b, pad, self.phantom())
        return ' pip install %s # trailing comment pip install %s' % (a, self.phantom())

    def decoys(self, ind, count):
        """Distinct decoy fields (one per YAML key), whose text must never become a command."""
        pad = ' ' * ind
        ph = self.phantom()
        options = [
            '%sname: "An example\n%s  run: pip install %s\n%s  end"\n' % (pad, pad, ph, pad),
            '%swith:\n%s  run: pip install %s\n%s  k: "a\n%s    script: pip install %s\n%s    b"\n' % (pad, pad, ph, pad, pad, self.phantom(), pad),
            '%senv:\n%s  run: pip install %s\n' % (pad, pad, ph),
            '%sif: ${{ always() }} # run: pip install %s\n' % (pad, ph),
            '%sworking-directory: "x"\n' % pad,
        ]
        if self.r.random() < .5:
            options[0] = "%sname: 'it''s\n%s  run: pip install %s\n%s  x'\n" % (pad, pad, self.phantom(), pad)
        return self.r.sample(options, count)

    def workflow(self):
        r = self.r
        dash = r.choice([4, 6])
        out = ['name: gen', 'on: push']
        if r.random() < .5:
            out += ['env:', '  run: pip install %s' % self.phantom()]
        out += ['jobs:', '  t:', '    runs-on: ubuntu-latest', ' ' * (dash - 2) + 'steps:' if dash == 6 else '    steps:']
        for _ in range(r.randint(1, 4)):
            step_pad = ' ' * dash
            key_col = dash + 2
            fields = self.decoys(key_col, r.randint(0, 3))
            cmd = self.command(key_col)
            run = ' ' * key_col + 'run:' + cmd + ('\n' if not cmd.endswith('\n') else '')
            fields.insert(r.randint(0, len(fields)), run)
            first = fields[0]
            out.append(step_pad + '- ' + first[key_col:].rstrip('\n'))
            for f in fields[1:]:
                out.append(f.rstrip('\n'))
        return '\n'.join(out) + '\n'


@unittest.skipIf(yaml is None, 'PyYAML is the test-time oracle')
class DifferentialTests(unittest.TestCase):
    def test_extractor_matches_the_yaml_reading(self):
        for seed in SEEDS:
            text = Gen(seed).workflow()
            doc = yaml.safe_load(text)
            commands = [s['run'] for s in doc['jobs']['t']['steps'] if isinstance(s.get('run'), str) and s['run'].strip()]
            got = extract(text)
            self.assertEqual(got, extract(canonical(commands)), 'seed %d\n%s' % (seed, text))
            self.assertFalse([g for g in got if g[1].startswith('phantom')], 'seed %d\n%s' % (seed, text))


if __name__ == '__main__':
    unittest.main()
