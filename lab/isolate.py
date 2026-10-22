"""Containment for model subprocesses: throwaway input folder, scrubbed env, audit-hook guard for python children."""
import contextlib
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

SECRET = ('KEY', 'TOKEN', 'SECRET', 'PASSWORD', 'CREDENTIAL', 'AUTH')
OWN = {'codex': ('OPENAI_API_KEY', 'CODEX_API_KEY'),  # the CLI's own login variables survive; cmd: children get none
       'claude': ('ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN', 'CLAUDE_CODE_OAUTH_TOKEN')}


def scrub(env, keep=()):
    return {k: v for k, v in env.items() if k in keep or not any(w in k.upper() for w in SECRET)}


@contextlib.contextmanager
def workspace(files, write=False):
    """Temp folder holding only `files` (paths, or {name: path}); read-only copies unless write. Removed on exit."""
    items = files.items() if isinstance(files, dict) else [(Path(f).name, f) for f in files]
    ws = Path(tempfile.mkdtemp(prefix='lab-ws-')).resolve()
    try:
        for name, src in items:
            dst = ws / name
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            if not write:
                dst.chmod(0o444)
        yield ws
    finally:
        for p in ws.rglob('*'):  # read-only files block rmtree on Windows
            p.chmod(0o700)
        shutil.rmtree(ws, ignore_errors=True)


@contextlib.contextmanager
def run(provider, cwd, files=None, write=False):
    """Yield (working folder, scrubbed env) for one provider call. files=None keeps the caller's cwd (the stage's own work folder).
    The env is passed to the subprocess; os.environ is never touched, so concurrent calls stay concurrent."""
    with contextlib.ExitStack() as st:
        ws = st.enter_context(workspace(files, write)) if files is not None else Path(cwd)
        tmp = Path(tempfile.mkdtemp(prefix='lab-tmp-', dir=ws if files is not None else None))
        st.callback(shutil.rmtree, tmp, True)
        yield ws, scrub(os.environ, OWN.get(provider.split(':')[0], ())) | dict.fromkeys(('TMP', 'TEMP', 'TMPDIR'), str(tmp))


PRELUDE = '''import json, os, site, sys, sysconfig, time
_root = os.path.normcase(os.path.realpath(%r))
_log = os.path.join(_root, '.denied.jsonl')
_ok = {sys.prefix, sys.base_prefix, sys.exec_prefix, sys.base_exec_prefix, os.devnull, *sysconfig.get_paths().values(), *%r}
try:
    _ok |= {site.getusersitepackages(), *site.getsitepackages()}
except Exception:
    pass
_ok = [os.path.normcase(os.path.realpath(p)) for p in _ok | {_root}]
_busy = False
def _hook(ev, a):
    global _busy
    if _busy or ev not in ('open', 'os.listdir', 'os.scandir') or not a or not isinstance(a[0], (str, bytes, os.PathLike)):
        return
    _busy = True
    try:
        p = os.path.normcase(os.path.realpath(os.fsdecode(a[0])))
        if any(p == d or p.startswith(d.rstrip(os.sep) + os.sep) for d in _ok):
            return
        with open(_log, 'a') as f:
            f.write(json.dumps(dict(utc=time.strftime('%%Y-%%m-%%dT%%H:%%M:%%SZ', time.gmtime()), op=ev, path=os.fsdecode(a[0]))) + '\\n')
    finally:
        _busy = False
    raise PermissionError('lab isolate: ' + p + ' is outside the work folder')
sys.addaudithook(_hook)
'''


def guard_code(root, allow=()):
    """sitecustomize source that refuses open/listdir/scandir outside root, the interpreter's own dirs and `allow`."""
    return PRELUDE % (str(Path(root).resolve()), [str(Path(a).resolve()) for a in allow])


def python_cmd(cmd, root, allow=()):
    """(cmd, env_extra) running a python evaluator command under guard_code. {python} becomes sys.executable;
    non-python commands come back unchanged with no env. The caller merges env_extra into the child env."""
    cmd = cmd.replace('{python}', f'"{sys.executable}"')
    if not re.match(r'\s*("[^"]*python[^"]*"|python3?(\.exe)?)(\s|$)', cmd, re.I):
        return cmd, {}
    g = Path(root) / '.guard'
    g.mkdir(exist_ok=True)
    (g / 'sitecustomize.py').write_text(guard_code(root, [*allow, g]))
    prev = os.environ.get('PYTHONPATH')
    return cmd, {'PYTHONPATH': str(g.resolve()) + (os.pathsep + prev if prev else '')}


def collect(root, run_dir):
    """Move root/.denied.jsonl into run_dir/events.jsonl; returns the number of refusals."""
    from lab import core  # lazy: rloop.py imports this module as a script, where the package is not on the path
    f = Path(root) / '.denied.jsonl'
    if not f.exists():
        return 0
    rows = [json.loads(ln) for ln in f.read_text().splitlines() if ln.strip()]
    f.unlink()
    for r in rows:
        core.event(Path(run_dir), 'isolate', f"denied {r['op']} {r['path']}", denied_utc=r['utc'])
    return len(rows)


def selftest():
    import subprocess
    with tempfile.TemporaryDirectory() as t:
        t = Path(t).resolve()
        outside = t / 'outside' / 'secret.txt'
        outside.parent.mkdir()
        outside.write_text('secret')
        inp = t / 'in.txt'
        inp.write_text('data')
        py = f'"{sys.executable}"'
        fake = t / 'fake.py'
        fake.write_text('import json, os, sys\nsys.stdin.read()\n'
                        'print(json.dumps(dict(ls=sorted(os.listdir(".")), env=sorted(os.environ), tmp=os.environ["TMP"],'
                        ' cwd=os.getcwd(), up=os.path.exists("../outside/secret.txt"), abs=os.path.exists(sys.argv[1]))))\n')
        # workspace holds only the allowed files and is gone afterwards
        with workspace([inp, fake]) as ws:
            assert sorted(p.name for p in ws.iterdir()) == ['fake.py', 'in.txt']
        assert not ws.exists()
        # scrub: secrets go (any case), CLI essentials stay, own login var stays for its CLI only
        env = {'API_KEY': 1, 'my_token': 1, 'AWS_SECRET_ACCESS_KEY': 1, 'DB_PASSWORD': 1, 'SSH_AUTH_SOCK': 1,
               'PATH': 1, 'SystemRoot': 1, 'LANG': 1, 'OPENAI_API_KEY': 1}
        assert sorted(scrub(env)) == ['LANG', 'PATH', 'SystemRoot']
        assert 'OPENAI_API_KEY' in scrub(env, OWN['codex'])
        # fake cmd provider through run(): sees only the workspace, no key, TMP inside it
        os.environ['API_KEY'] = 'x'
        try:
            with run('cmd:x', t, [inp, fake]) as (ws, senv):
                r = subprocess.run(f'{py} fake.py {outside}', shell=True, cwd=ws, input='', capture_output=True, text=True, encoding="utf-8", errors="replace", env=senv)
            seen = json.loads(r.stdout)
            assert [x for x in seen['ls'] if not x.startswith('lab-tmp-')] == ['fake.py', 'in.txt'], seen
            assert 'API_KEY' not in seen['env'] and 'PATH' in seen['env'] and not seen['up']
            assert Path(seen['tmp']).parent == ws, seen
            assert os.environ['API_KEY'] == 'x'  # never swapped
        finally:
            del os.environ['API_KEY']
        # audit-hook guard: inside root works, outside refused and logged, then collected as events
        root = t / 'root'
        root.mkdir()
        (root / 'ok.txt').write_text('fine')
        (root / 'eval.py').write_text(f'print(open("ok.txt").read())\nimport os\ntry:\n    open({str(outside)!r})\n'
                                      f'    print("LEAK")\nexcept PermissionError:\n    print("blocked")\n'
                                      f'try:\n    os.listdir({str(outside.parent)!r})\n    print("LEAK")\nexcept PermissionError:\n    print("blocked")\n')
        cmd, genv = python_cmd('{python} eval.py', root)
        r = subprocess.run(cmd, shell=True, cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace", env=os.environ | genv)
        assert r.stdout.split() == ['fine', 'blocked', 'blocked'], r.stdout + r.stderr
        assert python_cmd('make test', root) == ('make test', {})
        events = t / 'run'
        events.mkdir()
        assert collect(root, events) == 2 and not (root / '.denied.jsonl').exists()
        rows = [json.loads(ln) for ln in (events / 'events.jsonl').read_text().splitlines()]
        assert len(rows) == 2 and all(x['stage'] == 'isolate' and str(outside) in x['msg'] or str(outside.parent) in x['msg'] for x in rows)
    print('isolate selftest ok (workspace, env scrub, cmd cwd, audit-hook guard, denial events)')
