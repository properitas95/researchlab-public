"""Frozen design replay. Never execute copied evaluators or open research data."""
import argparse
import contextlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lab import core, critic, autofreeze

SKIP = 'meta replay: data omitted; evaluator execution disabled'
BOUNDARY = ('\nREPLAY ONLY: all external data are deliberately absent. Review the supplied source. '
            'Do not execute any code, read external paths, fetch data, freeze, or run holdout. '
            'Missing replay data is not a design defect. Wrong scores and leakage remain defects.\n')


def score(trace, paperwork):
    first, last = trace[0], trace[-1]
    return (1.0 if last == 0 else max(0, 1 - last / first) * .5) - .1 * paperwork


def replay(task, candidate):
    with tempfile.TemporaryDirectory(prefix='meta-replay-') as tmp:
        run, exp = Path(tmp).resolve() / 'run', Path(tmp).resolve() / 'exp'
        run.mkdir(); exp.mkdir()
        for p in task.iterdir():
            if p.is_file() and p.name != 'task.json':
                shutil.copy2(p, (run if p.name in ('brief.md', 'pick.json') else exp) / p.name)
        core.save_cfg(run, {'exp_dir': str(exp)})
        env = {'LAB_HARNESS': str(candidate / 'harness.json'), 'LAB_PROMPTS': str(candidate / 'prompts')}
        totals = dict(chars_in=0, chars_out=0)
        def routed(stage):
            def invoke(provider, instruction, cwd, model=None, write=False, timeout=1800):
                from lab import roles
                return roles.call(stage, instruction + BOUNDARY, cwd, run=run, write=write, timeout=timeout)
            return SimpleNamespace(**(vars(core) | {'invoke': invoke}))
        def disabled(*args, **kwargs):
            raise OSError(SKIP)
        inputs = critic._inputs
        def local_inputs(d):
            folder, files = inputs(d)
            return folder, [p for p in files if p.is_relative_to(run) or p.is_relative_to(exp)]
        def review(fn):
            j = fn(SimpleNamespace(run=str(run)))
            if 'findings' in j:
                j['findings'] = [f for f in j['findings'] if not (
                    f['id'] == 'dev-evaluation' and f.get('evidence') == SKIP)]
                for severity, key in [('blocker', 'blockers'), ('major', 'majors')]:
                    j[key] = sum(f['severity'] == severity for f in j['findings'])
                j['verdict'] = 'fix_first' if j['blockers'] or j['majors'] else 'freeze_ok'
                (run / 'critique.json').write_text(json.dumps(j))
                report = run / 'CRITIQUE.md'
                if report.exists():
                    report.write_text('\n'.join(line for line in report.read_text().splitlines()
                                                if SKIP not in line) + '\n')
            excerpts.append((run / 'CRITIQUE.md').read_text()[-6000:] if (run / 'CRITIQUE.md').exists() else '')
            return j['blockers'] + j['majors']
        excerpts, created = [], set()
        with patch.dict(os.environ, env), patch.object(critic, '_inputs', local_inputs), patch.object(critic, 'core', routed('critic')), patch.object(
                autofreeze, 'core', routed('replan')), patch.object(critic, 'subprocess',
                SimpleNamespace(run=disabled, TimeoutExpired=subprocess.TimeoutExpired)):
            trace = [review(critic.critic)]
            for _ in range(3):
                if trace[-1] == 0:
                    break
                before = {str(p.relative_to(exp)) for p in exp.rglob('*') if p.is_file()}
                trace.append(review(autofreeze.replan))  # replan already calls critic
                created |= {str(p.relative_to(exp)) for p in exp.rglob('*') if p.is_file()} - before
                if trace[-1] >= trace[-2]:
                    break
        calls = run / 'calls.jsonl'
        for line in calls.read_text().splitlines() if calls.exists() else []:
            j = json.loads(line)
            for key in totals:
                totals[key] += j[key]
        return dict(score=score(trace, len(created)), trace=trace, paperwork=len(created),
                    critiques=excerpts, **totals)


def evaluate(root, split):
    held = set((root / 'holdout.txt').read_text().split())
    tasks = [p for p in sorted((root / 'tasks').iterdir()) if (p / 'task.json').is_file()
             and ((p.name in held) == (split == 'holdout'))]
    if not tasks:
        return {'score': 0, 'pass': False, 'why': f'no {split} tasks yet'}
    results = {p.name: replay(p, root / 'candidate') for p in tasks}
    return dict(score=sum(x['score'] for x in results.values()) / len(results),
                **{'pass': True}, tasks=results,
                **{k: sum(x[k] for x in results.values()) for k in ('chars_in', 'chars_out')})


def selftest():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp); task = root / 'task'; task.mkdir()
        (task / 'brief.md').write_text('Replay'); (task / 'pick.json').write_text('{}')
        (root / 'fake.py').write_text('import sys; print(sys.stdin.read())')
        def check(a):
            exp = Path(core.cfg(Path(a.run))['exp_dir'])
            n = int((exp / 'n').read_text()) if (exp / 'n').exists() else 0
            text = core.invoke('cmd:python3 fake.py', json.dumps({'blockers': 0, 'majors': max(0, 2-n)}), root)
            return json.loads(text)
        def fix(a):
            exp = Path(core.cfg(Path(a.run))['exp_dir'])
            p = exp / 'n'; p.write_text(str(int(p.read_text()) + 1 if p.exists() else 1))
            return check(a)
        with patch.object(critic, 'critic', check), patch.object(autofreeze, 'replan', fix):
            j = replay(task, root)
            assert j['trace'] == [2, 1, 0] and j['score'] == .9 and j['paperwork'] == 1, j
        with patch.object(critic, 'critic', check), patch.object(autofreeze, 'replan', lambda a: {'blockers': 3, 'majors': 0}):
            j = replay(task, root)
            assert j['trace'] == [2, 3] and j['score'] == 0, j
        assert score([4, 2], 1) == .15
        (task / 'PROTOCOL.md').write_text('Mutable file: candidate.py')
        (task / 'candidate.py').write_text('x=0')
        (task / 'evaluate.py').write_text("import sys\nif sys.argv[-1]=='dev': pass\nraise RuntimeError('MUST NOT RUN')")
        fake = """import sys,json,pathlib
s=sys.stdin.read()
assert 'REPLAY ONLY' in s
if 'TASK: replan' in s:
    pathlib.Path('candidate.py').write_text('x=1')
    print('revised')
else:
    bad = '1: x=0' in s
    print(json.dumps(dict(verdict='fix_first' if bad else 'freeze_ok',findings=[
        dict(id='bad-score',severity='major',claim='wrong score',evidence='candidate',fix='correct it')
    ] if bad else [])))
"""
        def call(stage, instruction, cwd, **kw):
            p = Path(cwd) / 'fake.py'; p.write_text(fake)
            try:
                out = core.invoke('cmd:python3 fake.py', instruction, cwd, write=kw['write'])
            finally:
                p.unlink()
            with (kw['run'] / 'calls.jsonl').open('a') as f:
                f.write(json.dumps(dict(chars_in=len(instruction), chars_out=len(out))) + '\n')
            return out
        with patch.dict(sys.modules, {'lab.roles': SimpleNamespace(call=call)}):
            j = replay(task, root)
            assert j['trace'] == [1, 0] and j['score'] == 1 and j['chars_out'] > 0, j
    print('eval_convergence selftest ok')


if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('--split', choices=('dev', 'holdout'), required=True)
    a = ap.parse_args()
    with contextlib.redirect_stdout(sys.stderr):
        result = evaluate(Path(__file__).resolve().parent, a.split)
    print(json.dumps(result))
