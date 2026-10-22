"""Opt-in prompt/config search; adoption always requires an explicit --yes."""
import contextlib
import difflib
import io
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
from argparse import Namespace
from unittest.mock import patch

from lab import core, rloop

FILES = ['harness.json', *[f'prompts/{n}.md' for n in ('critic', 'replan', 'plan')]]


def meta_init(a):
    root = core.ROOT / 'meta'
    if (root / 'loop.json').exists():
        raise ValueError('meta already initialized')
    for name in FILES:
        if not (core.ROOT / name).is_file():
            raise FileNotFoundError(core.ROOT / name)
    for name in FILES:
        p = root / 'candidate' / name
        p.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(core.ROOT / name, p)
    frozen = ['eval_convergence.py', 'holdout.txt', 'prompts/improve.md']
    frozen += [str(p.relative_to(root)) for p in sorted((root / 'tasks').rglob('*')) if p.is_file()]
    print(core.rloop('init', root, '--q', 'M1', '--eval', 'python3 eval_convergence.py --split dev',
                     '--frozen', *frozen, '--mutable', *['candidate/' + n for n in FILES], '--min-delta', .1))


def _feedback(root):
    rows = [r for r in rloop.rows(root) if r['q'] == 'M1']
    if not rows:
        return '(no evaluation yet)'
    text = (root / 'runs' / f"{rows[-1]['n']}.log").read_text()
    try:
        return json.dumps(core.last_json(text), ensure_ascii=False)
    except ValueError:
        return text[-12000:]


def _propose(root, instruction):
    try:
        from lab import roles
    except ImportError:
        return core.invoke('codex', instruction, root / 'candidate', write=True)
    # The shared API takes stage names, not role names; program maps to researcher.
    return roles.call('program', instruction, root / 'candidate', run=root, write=True)


def meta_improve(a):
    root = core.ROOT / 'meta'
    cfg = rloop.load(root)['M1']
    rloop.check_frozen(root, cfg)
    if not rloop.best(root, 'M1', 'max'):
        print(core.rloop('run', root, '--q', 'M1', '-m', 'baseline'))
        with (root / 'LESSONS.md').open('a') as f:
            f.write(f"- {core.now()} baseline: {_feedback(root)}\n")
    if not rloop.best(root, 'M1', 'max'):
        raise RuntimeError('meta baseline did not pass')
    for _ in range(getattr(a, 'iters', 3)):
        core.rloop('revert', root, '--q', 'M1')
        candidate = root / 'candidate'
        before = {p.relative_to(candidate): p.read_bytes() for p in candidate.rglob('*') if p.is_file()}
        instruction = (root / 'prompts/improve.md').read_text().replace('{{mutable}}', ', '.join(FILES))
        instruction = instruction.replace('{{feedback}}', _feedback(root))
        outcome, reply = 'error', ''
        try:
            with patch.dict(os.environ, {'LAB_HARNESS': str(candidate / 'harness.json'),
                                         'LAB_PROMPTS': str(candidate / 'prompts')}):
                reply = _propose(root, instruction)
            after = {p.relative_to(candidate): p.read_bytes() for p in candidate.rglob('*') if p.is_file()}
            if any(str(p) not in FILES for p in before.keys() | after.keys() if before.get(p) != after.get(p)):
                raise ValueError('proposer touched files outside the mutable set')
            rloop.check_frozen(root, cfg)
            n = len(rloop.rows(root))
            print(core.rloop('run', root, '--q', 'M1', '-m', reply.strip()[:150], check=False))
            rows = rloop.rows(root)
            outcome = rows[-1]['status'] if len(rows) > n else 'error'
        finally:
            if outcome != 'keep':
                for p in candidate.rglob('*'):
                    if p.is_file() and p.relative_to(candidate) not in before:
                        p.unlink()
                for p, data in before.items():
                    (candidate / p).parent.mkdir(parents=True, exist_ok=True)
                    (candidate / p).write_bytes(data)
                core.rloop('revert', root, '--q', 'M1')
            with (root / 'LESSONS.md').open('a') as f:
                lesson = ' '.join(reply.splitlines())[:300]
                f.write(f'- {core.now()} {outcome}: {lesson}\n')
        if outcome == 'error':
            raise RuntimeError('meta evaluator failed to record a trial')


def meta_adopt(a):
    root = core.ROOT / 'meta'
    cfg = rloop.load(root)['M1']
    rloop.check_frozen(root, cfg)
    best = rloop.best(root, 'M1', 'max')
    if best is None:
        raise ValueError('no kept meta candidate')
    source = root / 'runs' / str(best['n']) / 'candidate'
    copies = {n: (source / n).read_text() for n in FILES}
    for name, text in copies.items():
        live = core.ROOT / name
        print(''.join(difflib.unified_diff(live.read_text().splitlines(True), text.splitlines(True),
                                         fromfile=name, tofile='best/' + name)), end='')
    if getattr(a, 'yes', False):
        for name, text in copies.items():
            (core.ROOT / name).write_text(text)
        core.event(root, 'meta-adopt', f"adopted M1 trial {best['n']}", trial=best['n'])


def selftest():
    from meta import eval_convergence
    eval_convergence.selftest()
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp); root = home / 'meta'; root.mkdir()
        for name in FILES:
            p = home / name; p.parent.mkdir(parents=True, exist_ok=True); p.write_text('live\n')
            p = root / 'runs/1/candidate' / name; p.parent.mkdir(parents=True, exist_ok=True); p.write_text('best\n')
        (root / 'fake.py').write_text('import sys; assert sys.stdin.read(); print("ok")')
        assert core.invoke('cmd:python3 fake.py', 'selftest', root).strip() == 'ok'
        with patch.object(core, 'ROOT', home), patch.object(rloop, 'load', return_value={'M1': {}}), patch.object(
                rloop, 'check_frozen'), patch.object(rloop, 'best', return_value={'n': '1'}):
            with contextlib.redirect_stdout(io.StringIO()) as out:
                meta_adopt(Namespace(yes=False))
            assert '+best' in out.getvalue()
            assert all((home / n).read_text() == 'live\n' for n in FILES)
            assert not (root / 'events.jsonl').exists()
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp); root = home / 'meta'; root.mkdir()
        for name in FILES:
            p = home / name; p.parent.mkdir(parents=True, exist_ok=True); p.write_text('0')
        (root / 'holdout.txt').write_text('')
        (root / 'tasks').mkdir(); (root / 'prompts').mkdir()
        (root / 'prompts/improve.md').write_text('TASK: test {{feedback}}')
        (root / 'eval_convergence.py').write_text(
            "import json,pathlib\nprint(json.dumps({'score':float(pathlib.Path('candidate/harness.json').read_text()),'pass':True}))")
        (root / 'fake.py').write_text(
            "import sys,pathlib\nassert sys.stdin.read()\npathlib.Path('candidate/harness.json').write_text('0.5')\nprint('IDEA: test')")
        def propose(d, instruction):
            return core.invoke('cmd:python3 fake.py', instruction, d, write=True)
        def loop(*args, check=True):
            with patch.object(sys, 'argv', ['rloop.py', *map(str, args)]), patch.object(rloop.os, 'nice'), contextlib.redirect_stdout(io.StringIO()) as out:
                rloop.main()
            return out.getvalue()
        with patch.object(core, 'ROOT', home), patch(__name__ + '._propose', propose), patch.object(core, 'rloop', loop):
            with contextlib.redirect_stdout(io.StringIO()):
                meta_init(Namespace())
                meta_improve(Namespace(iters=2))
            assert [r['status'] for r in rloop.rows(root)] == ['keep', 'keep', 'discard']
            assert (root / 'candidate/harness.json').read_text() == '0.5'
            assert len((root / 'LESSONS.md').read_text().splitlines()) == 3
            assert not list((root / '.rloop').glob('holdout*'))
    print('meta selftest ok')
