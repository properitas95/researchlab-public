"""Dev-only component removals, measured outside the search ledger."""
import csv
import json
import math
import shlex
import shutil
import tempfile
from pathlib import Path
from types import SimpleNamespace

from lab import core, roles, rloop as rl
from lab.experiment import _ctx


def context(a):
    d, c, exp, q = _ctx(a)
    exp = exp.resolve()
    cfg = rl.load(exp)[q]
    rows = [r for r in rl.rows(exp) if r['q'] == q]
    if not (exp / 'results.tsv').exists():
        raise ValueError('search results missing')
    kept = [r for r in rows if r['status'] == 'keep']
    base = kept[0] if kept else None
    return d, c, exp, cfg, base, rl.best(exp, q, cfg['direction'])


def interpreter(exp, command):
    first = shlex.split(command)[0]
    return str((exp / first).absolute()) if '/' in first and not Path(first).is_absolute() else first


def copy_row(exp, cfg, row):
    names = cfg['mutable']
    if any(Path(p).is_absolute() or '..' in Path(p).parts for p in names):
        raise ValueError('mutable files must be inside experiment')
    if set(names) & set(cfg['frozen']):
        raise ValueError('mutable files overlap frozen files')
    return {p: (exp / 'runs' / row['n'] / p).read_text() for p in names}


def copy_exp(exp, work, cfg):
    if work.exists():
        shutil.rmtree(work)
    shutil.copytree(exp, work, ignore=shutil.ignore_patterns('runs', '.rloop', '__pycache__'))
    for name in cfg['frozen']:
        p = Path(name)
        if not p.is_absolute() and '..' in p.parts:
            target = work / p
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(exp / p, target)


def ablate(a):
    d, c, exp, cfg, base, best = context(a)
    if best is None or best['n'] == base['n']:
        msg = 'no winner, ablations skipped'
        (d / 'ablations.md').write_text(msg + '\n')
        core.event(d, 'ablate', msg)
        return
    if 'holdout' in cfg['eval'].lower():
        raise ValueError('dev evaluator must not select holdout')
    rl.check_frozen(exp, cfg)
    winner, baseline = copy_row(exp, cfg, best), copy_row(exp, cfg, base)
    cmd = shlex.split(cfg['eval'])
    cmd[0] = interpreter(exp, cfg['eval'])
    with tempfile.TemporaryDirectory(prefix='ablate-') as t:
        depth = max([len(Path(p).parts) for p in cfg['frozen']] + [1])
        work = Path(t).joinpath(*(['work'] * depth), 'exp')
        copy_exp(exp, work, cfg)
        instr = core.prompt('ablate', question=c['q'], winner=json.dumps(winner), baseline=json.dumps(baseline))
        proposals = core.last_json(roles.call("ablate", instr, cwd=work, run=d, write=False))
        if not isinstance(proposals, list):
            raise ValueError('expected ablation array')
        cases = [{'name': 'winner', 'why': f"reference #{best['n']}", 'code': winner},
                 {'name': 'baseline', 'why': f"reference #{base['n']}", 'code': baseline}]
        seen = {'winner', 'baseline'}
        for p in proposals[:4]:
            if (not isinstance(p, dict) or not isinstance(p.get('name'), str) or not p['name'].strip()
                    or p['name'] in seen or not isinstance(p.get('why'), str)
                    or not isinstance(p.get('code'), dict) or set(p['code']) != set(winner)
                    or not all(isinstance(v, str) for v in p['code'].values())):
                raise ValueError('invalid ablation: unique name, why and full mutable files required')
            seen.add(p['name'])
            cases.append(p)
        rows = []
        for p in cases:
            copy_exp(exp, work, cfg)  # reset caches and model edits before every measurement
            for name, code in p['code'].items():
                (work / name).write_text(code)
            score, _, status = rl.execute(work, shlex.join(cmd), cfg.get('timeout', 600), Path(t) / 'eval.log', cfg.get('guard_root'))
            if status != 'ok' or score is None or not math.isfinite(score):
                score = None
            rows.append({'name': p['name'], 'score': score, 'why': p['why'] + (f' [{status}]' if score is None else '')})
    for row in rows:
        for label, ref in zip(('winner', 'baseline'), rows[:2]):
            row['delta_vs_' + label] = (row['score'] - ref['score']
                                       if row['score'] is not None and ref['score'] is not None else None)
    cols = ['name', 'score', 'delta_vs_winner', 'delta_vs_baseline', 'why']
    with (exp / 'ablations.tsv').open('w') as f:
        w = csv.DictWriter(f, cols, delimiter='\t'); w.writeheader(); w.writerows(rows)
    cell = lambda v: str(v if v is not None else 'NA').replace('|', '\\|').replace('\n', ' ')
    table = ['| ' + ' | '.join(cols) + ' |', '| ' + ' | '.join(['---'] * len(cols)) + ' |']
    table += ['| ' + ' | '.join(cell(r[k]) for k in cols) + ' |' for r in rows]
    (d / 'ablations.md').write_text('\n'.join(table) + '\n')
    core.event(d, 'ablate', f'{len(rows) - 2} ablations; dev references #{best["n"]}, #{base["n"]}')


def fixture(t):
    d, exp = Path(t) / 'r', Path(t) / 'r' / 'exp'
    exp.mkdir(parents=True)
    (exp / 'eval.py').write_text('import json\nprint(json.dumps({"score":float(open("x.txt").read())}))\n')
    (exp / 'x.txt').write_text('99')
    (exp / 'PROTOCOL.md').write_text('dev only\n')
    cfg = dict(eval='python3 eval.py', holdout='NEVER', frozen=['eval.py'],
               mutable=['x.txt'], direction='max', timeout=5, protocol='toy')
    cfg['frozen_hash'] = rl.sha(cfg['frozen'], exp)
    (exp / 'loop.json').write_text(json.dumps({'Q1': cfg}))
    for n, value in [('1', '0'), ('2', '2')]:
        p = exp / 'runs' / n
        p.mkdir(parents=True)
        (p / 'x.txt').write_text(value)
        rl.append(exp, dict(zip(rl.COLS, [n, 't', 'Q1', 'toy', 'v0', 'code', value, 'keep', '0', 'trial'])))
    core.save_cfg(d, {'exp_dir': str(exp), 'provider': 'cmd:python3 fake.py'})
    return d, exp, SimpleNamespace(run=str(d), force=False)


def selftest():
    with tempfile.TemporaryDirectory() as t:
        d, exp, a = fixture(t)
        (exp / 'fake.py').write_text('import json,sys\nsys.stdin.read()\nprint(json.dumps([{"name":"remove", "why":"remove gain", "code":{"x.txt":"1"}}]))\n')
        (d / 'helper.py').write_text('value = 0\n')
        (exp / 'eval.py').write_text('import sys\nsys.path.insert(0, "..")\nimport helper\n' + (exp / 'eval.py').read_text())
        cfg = rl.load(exp)
        cfg['Q1']['frozen'].append('../helper.py')
        cfg['Q1']['frozen_hash'] = rl.sha(cfg['Q1']['frozen'], exp)
        (exp / 'loop.json').write_text(json.dumps(cfg))
        original = (exp / 'results.tsv').read_bytes()
        ablate(a)
        with (exp / 'ablations.tsv').open() as f:
            rows = list(csv.DictReader(f, delimiter='\t'))
        assert [r['score'] for r in rows] == ['2.0', '0.0', '1.0'], rows
        assert rows[2]['delta_vs_winner'] == '-1.0' and rows[2]['delta_vs_baseline'] == '1.0'
        assert (exp / 'results.tsv').read_bytes() == original and (exp / 'x.txt').read_text() == '99'
        assert not (exp / '.rloop').exists()
        (exp / 'results.tsv').write_text('\n'.join(original.decode().splitlines()[:2]) + '\n')
        (exp / 'fake.py').write_text('raise AssertionError("must not invoke")\n')
        ablate(a)
        assert (d / 'ablations.md').read_text().strip() == 'no winner, ablations skipped'
    print('ablate selftest ok')
