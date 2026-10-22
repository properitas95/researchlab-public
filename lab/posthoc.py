"""Small dev-only analyses; inspect generated scripts before executing them."""
import ast
import json
import subprocess
import tempfile
from pathlib import Path

from lab import core, roles
from lab.ablate import context, fixture, interpreter
from lab.rloop import kill_tree


def rejection(text, folder):
    if 'holdout' in text.lower():
        return 'forbidden holdout text'
    try:
        tree = ast.parse(text)
    except SyntaxError as e:
        return str(e)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = fn.id if isinstance(fn, ast.Name) else fn.attr if isinstance(fn, ast.Attribute) else ''
        if name not in ('open', 'write_text', 'write_bytes'):
            continue
        args = list(node.args)
        path = next((k.value for k in node.keywords if k.arg == 'file'), args[0] if args else None)
        if isinstance(fn, ast.Attribute) and name in ('write_text', 'write_bytes', 'open'):
            receiver = fn.value
            if not isinstance(receiver, ast.Name) or receiver.id not in ('io', 'builtins'):
                path = receiver.args[0] if isinstance(receiver, ast.Call) and receiver.args else None
                args = [path, *args]
        mode = next((k.value for k in node.keywords if k.arg == 'mode'), args[1] if len(args) > 1 else None)
        if name == 'open' and (mode is None or isinstance(mode, ast.Constant)
                               and isinstance(mode.value, str) and not any(c in mode.value for c in 'wax+')):
            continue
        if not isinstance(path, ast.Constant) or not isinstance(path.value, str):
            return 'write path must be a literal inside posthoc'
        p = Path(path.value)
        if '..' in p.parts or not (folder / p).resolve().is_relative_to(folder.resolve()):
            return 'write outside posthoc'
    return None


def run_py(folder, py, script, timeout):
    """Run script in folder; returns (returncode, stdout, stderr), returncode None on timeout (process tree killed)."""
    p = subprocess.Popen([core.win_py(py), '-B', str(script)], cwd=folder, stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, text=True, start_new_session=True)
    try:
        out, err = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        kill_tree(p)
        out, err = p.communicate()
        return None, out, err
    return p.returncode, out, err


def execute(folder, py, script):
    rc, out, err = run_py(folder, py, script, 300)
    if rc is None:
        return {'valid': False, 'error': 'timeout 300s', 'stdout': out, 'stderr': err}
    result = {'valid': False, 'returncode': rc, 'stdout': out, 'stderr': err}
    try:
        value = json.loads(out.strip().splitlines()[-1])
        if not isinstance(value, dict) or rc:
            raise ValueError('expected successful JSON object output')
        result.update(valid=True, result=value)
    except (ValueError, IndexError) as e:
        result['error'] = str(e)
    return result


def posthoc(a):
    d, c, exp, cfg, _, best = context(a)
    if best is None:
        raise ValueError('no kept snapshot for posthoc')
    folder = (d / 'posthoc').resolve()
    folder.mkdir(exist_ok=True)
    before = {p: p.read_bytes() for p in folder.rglob('*.py')}
    old = folder / 'manifest.json'
    prior = {r['script'] for r in json.loads(old.read_text())['scripts']} if old.exists() else set()
    snap = exp / 'runs' / best['n']
    instr = core.prompt('posthoc', snapshot=snap, question=c['q'], experiment=exp,
                        mutable=json.dumps(cfg['mutable']), evaluator=cfg['eval'])
    (folder / 'reply.md').write_text(roles.call("posthoc", instr, cwd=folder, run=d, write=True))
    scripts = [p for p in sorted(folder.rglob('*.py')) if p not in before or p.read_bytes() != before[p]
               or str(p.relative_to(folder)) in prior]
    records = []
    py = interpreter(exp, cfg['eval'])
    # shortcut: static checks are a crude screen, not an OS sandbox for hostile Python.
    for i, p in enumerate(scripts):
        reason = ('script limit exceeded' if i >= 3 else 'script symlink escapes posthoc'
                  if not p.resolve().is_relative_to(folder) else rejection(p.read_text(), folder))
        records.append({'script': str(p.relative_to(folder)), 'valid': False, 'error': reason})
    for row in records:
        if row['error'] is None:
            try:
                row.update(execute(folder, py, folder / row['script']))
            except OSError as e:
                row['error'] = str(e)
    manifest = {'snapshot': str(snap), 'interpreter': py, 'scripts': records}
    (folder / 'manifest.json').write_text(json.dumps(manifest, indent=1) + '\n')
    core.event(d, 'posthoc', f'{sum(r["valid"] for r in records)}/{len(records)} valid analyses')


def selftest():
    with tempfile.TemporaryDirectory() as t:
        d, exp, a = fixture(t)
        folder = d / 'posthoc'
        folder.mkdir()
        good = 'import json\nprint(json.dumps({"dev":1}))\n'
        bad = 'open("executed", "w").write("bad")\n# HoLdOuT\n'
        escape = 'open("../escaped", "w").write("bad")\n'
        (folder / 'fake.py').write_text('from pathlib import Path\n' + '\n'.join(
            f'Path({name!r}).write_text({code!r})' for name, code in
            [('a.py', good), ('b.py', bad), ('c.py', escape)]))
        posthoc(a)
        m = json.loads((folder / 'manifest.json').read_text())
        assert [r['valid'] for r in m['scripts']] == [True, False, False], m
        assert not (folder / 'executed').exists() and not (d / 'escaped').exists()
        assert not (exp / '.rloop').exists()
        posthoc(a)
        assert len(json.loads((folder / 'manifest.json').read_text())['scripts']) == 3
        assert rejection('open("/tmp/out", mode="w")', folder)
        assert rejection('p.open("w")', folder)
        assert rejection('from pathlib import Path\nPath("../out").write_text("x")', folder)
        assert rejection('open("ok", "w").write("x")', folder) is None
    print('posthoc selftest ok')
