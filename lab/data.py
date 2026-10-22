"""Immutable dated snapshots; pandas runs only in the requested interpreter.

Recipe (not executed): lab.py snapshot RUN --name a200 --dev-end 2020-12-31
--request 'Use <project>/.venv/bin/python and copy <project>/cache/panel_A200.pkl
with pd.read_pickle into full.pkl; preserve rets and date-keyed uni.'
"""
import ast
import contextlib
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from lab import core, rloop
if (Path(__file__).with_name('roles.py')).exists():
    from lab import roles
else:
    roles = None
SPLIT = '''import json, sys, pandas as pd
from pathlib import Path
cut = pd.Timestamp(sys.argv[1]).date()
rows, columns, dates, later = {}, {}, [], []
def split(x, key='data'):
    if isinstance(x, pd.DataFrame):
        if not isinstance(x.index, pd.DatetimeIndex) or x.index.hasnans:
            raise ValueError('DataFrame requires a non-null DatetimeIndex')
        idx = x.index
        mask = idx.date <= cut
        rows[key] = {'full': len(x), 'dev': int(mask.sum()), 'holdout': int((~mask).sum())}
        columns[key] = list(map(str, x.columns))
        dates.extend(idx.date); later.extend(idx.date[~mask])
        return x.loc[mask], x.loc[~mask]
    if not isinstance(x, dict):
        raise ValueError('expected DataFrame or date-keyed dict')
    if all(isinstance(v, (pd.DataFrame, dict)) for v in x.values()):
        pairs = {k: split(v, str(k)) for k, v in x.items()}
        return ({k: v[0] for k, v in pairs.items()}, {k: v[1] for k, v in pairs.items()})
    a, b = {}, {}
    for k, v in x.items():
        date = pd.Timestamp(k)
        if pd.isna(date) or isinstance(k, (int, float)):
            raise ValueError('invalid date key')
        date = date.date(); dates.append(date)
        (a if date <= cut else b)[k] = v
        if date > cut: later.append(date)
    rows[key] = {'full': len(x), 'dev': len(a), 'holdout': len(b)}
    return a, b
x = pd.read_pickle('full.pkl')
a, b = split(x)
if not dates: raise ValueError('empty snapshot')
pd.to_pickle(a, 'dev.pkl'); pd.to_pickle(b, 'holdout.pkl')
Path('full.pkl').unlink()
print(json.dumps(dict(rows=rows, columns=columns, date_min=str(min(dates)),
                     date_max=str(max(dates)), holdout_start=str(min(later)) if later else None)))
'''

def _path(name):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', name):
        raise ValueError('invalid dataset name')
    return core.ROOT / 'data' / name


@contextlib.contextmanager
def _workspace(target):
    with tempfile.TemporaryDirectory(prefix='snapshot-', dir=target.parent) as t:
        previous = Path(t) / 'previous'
        if target.exists(): target.rename(previous)
        target.mkdir()
        try:
            yield target, previous
        except BaseException:
            shutil.rmtree(target)
            if previous.exists(): previous.rename(target)
            raise

def snapshot(a):
    d = core.run_dir(a.run).resolve()
    target = _path(a.name)
    dt.date.fromisoformat(a.dev_end)
    request = getattr(a, 'request', None) or (d / 'brief.md').read_text()
    if '\n' not in request and len(request) < 240 and Path(request).is_file():
        request = Path(request).read_text()
    matches = core.PY_RE.findall(request + '\n' + (d / 'brief.md').read_text())
    python = next((p for p in matches if '/' in p), matches[0] if matches else sys.executable)
    if '/' in python: python = str(Path(python).absolute())
    target.parent.mkdir(parents=True, exist_ok=True)
    with rloop.locked(target.parent), _workspace(target) as (work, previous):
        c = core.cfg(d)
        provider = getattr(a, 'provider', None)
        fake = (provider or c['provider']).startswith('cmd:') and (d / 'fake.py').exists()
        if fake: shutil.copy2(d / 'fake.py', work / 'fake.py')
        instruction = core.prompt('snapshot', request=request, python=python)
        if roles:
            roles.call('snapshot', instruction, work, run=d, write=True, provider=provider)
        else:
            core.invoke(provider or c['provider'], instruction, work, c['model'], True)
        if fake: (work / 'fake.py').unlink()
        env = os.environ | {'PYTHONDONTWRITEBYTECODE': '1'} | {k: os.environ.get('RLOOP_THREADS', '1') for k in rloop.THREADS}
        for args in ([python, 'build.py'], [python, '-c', SPLIT, a.dev_end]):
            result = subprocess.run(['nice', '-n', '10', *args], cwd=work, env=env, timeout=1800, check=True,
                                    capture_output=True, text=True, encoding="utf-8", errors="replace")
            if result.stderr: print(result.stderr.strip(), file=sys.stderr)
        manifest = json.loads(result.stdout)
        files = sorted(work.rglob('*'))
        if any(p.is_symlink() for p in files): raise ValueError('snapshot contains symlinks')
        hashes = {str(p.relative_to(work)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files if p.is_file()}
        manifest.update(name=a.name, sha256=hashes, dev_end=a.dev_end,
                        source=(work / 'source.txt').read_text().strip(), built_utc=core.now())
        old = previous / 'manifest.json'
        if previous.exists() and not getattr(a, 'force', False):
            if not old.exists() or json.loads(old.read_text())['sha256'] != hashes:
                raise ValueError('snapshot hash changed; use --force')
            manifest = json.loads(old.read_text())
        (work / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
        catalog = core.ROOT / 'memory/global/datasets.json'
        catalog.parent.mkdir(parents=True, exist_ok=True)
        entries = json.loads(catalog.read_text()) if catalog.exists() else {}
        entries[a.name] = manifest
        pending = catalog.with_suffix('.tmp')
        pending.write_text(json.dumps(entries, indent=2) + '\n'); pending.replace(catalog)
    core.event(d, 'snapshot', f'{a.name}: dev <= {a.dev_end}', dataset=a.name, manifest=str(target / 'manifest.json'))
    return manifest

def data_env(name, split):
    if split not in ('dev', 'holdout'): raise ValueError('invalid split')
    path = _path(name)
    if not (path / f'{split}.pkl').is_file(): raise FileNotFoundError(path / f'{split}.pkl')
    return {'LAB_DATA_DIR': str(path.resolve())}

def load(split='split'):
    return 'pd.read_pickle(os.environ["LAB_DATA_DIR"] + f"/{' + split + '}.pkl")'

def dev_only_copy(name, dest):
    dest = Path(dest)
    if dest.exists() and any(dest.iterdir()): raise ValueError('dev destination must be empty')
    dest.mkdir(parents=True, exist_ok=True)
    for file in ('manifest.json', 'dev.pkl'): shutil.copy2(_path(name) / file, dest / file)

def check_evaluator(path, name):
    _path(name)
    findings, seen = [], set()
    def scan(p):
        p = p.resolve()
        if p in seen: return
        seen.add(p)
        tree = ast.parse(p.read_text())
        safe = set()
        for n in ast.walk(tree):
            if isinstance(n, (ast.BinOp, ast.JoinedStr)) and 'LAB_DATA_DIR' in ast.unparse(n):
                safe.update(id(v) for v in ast.walk(n) if isinstance(v, ast.Constant) and v.value in ('/', '.pkl', '/dev.pkl', '/holdout.pkl', 'dev.pkl', 'holdout.pkl'))
        for n in ast.walk(tree):
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in safe:
                if n.value.startswith(('/', '../')) or re.search(r'\.(pkl|parquet|csv)\b', n.value):
                    findings.append(f'data outside snapshot: {p}:{n.lineno}: {n.value}')
            if isinstance(n, (ast.Import, ast.ImportFrom)):
                mods = [n.module or ''] if isinstance(n, ast.ImportFrom) else [x.name for x in n.names]
                for mod in mods:
                    local = p.parent / (mod.replace('.', '/') + '.py')
                    if local.is_file(): scan(local)
                    elif re.search(r'loader|cache|warehouse|lo_common', mod):
                        findings.append(f'data outside snapshot: {p}:{n.lineno}: import {mod}')
    scan(Path(path))
    return sorted(set(findings))

def selftest():
    from unittest.mock import patch
    python = sys.executable
    if subprocess.run([python, '-c', 'import pandas'], capture_output=True).returncode:
        python = os.environ.get('LAB_PANDAS_PYTHON', python)  # an interpreter with pandas
        if subprocess.run([python, '-c', 'import pandas'], capture_output=True).returncode:
            return print('data selftest: skipped (no pandas; set LAB_PANDAS_PYTHON)')
    with tempfile.TemporaryDirectory() as t, patch.object(core, 'ROOT', Path(t)):
        d = Path(t) / 'run'; d.mkdir()
        (d / 'brief.md').write_text(f'Use {python}')
        build = "import pandas as pd\ni=pd.date_range('2015-12-31', '2026-12-31', freq='YE')\npd.to_pickle({'rets':pd.DataFrame({'A':range(len(i))},index=i),'uni':{x:['A'] for x in i}}, 'full.pkl')\n"
        (d / 'fake.py').write_text(f"import sys\nfrom pathlib import Path\nassert 'TASK: snapshot' in sys.stdin.read()\nPath('build.py').write_text({build!r})\nPath('source.txt').write_text('toy')\n")
        core.save_cfg(d, {'provider': 'cmd:python3 fake.py'})
        a = SimpleNamespace(run=str(d), name='toy', dev_end='2020-12-31', request=None, force=False, provider='cmd:python3 fake.py')
        m = snapshot(a); p = _path('toy')
        assert all(hashlib.sha256((p / f).read_bytes()).hexdigest() == h for f, h in m['sha256'].items())
        assert json.loads((Path(t) / 'memory/global/datasets.json').read_text())['toy'] == m
        subprocess.run([python, '-c', "import pandas as pd\nfor s in ['dev','holdout']:\n x=pd.read_pickle(s+'.pkl'); dates=list(x['rets'].index)+list(x['uni']); assert all((v.year<=2020)==(s=='dev') for v in dates)"], cwd=p, check=True)
        snapshot(a)
        a.dev_end = '2019-12-31'
        try: snapshot(a)
        except ValueError: pass
        else: raise AssertionError('overwrite accepted')
        assert json.loads((p / 'manifest.json').read_text()) == m
        a.force = True; assert snapshot(a)['dev_end'] == '2019-12-31'
        dev_only_copy('toy', Path(t) / 'dev'); assert not (Path(t) / 'dev/holdout.pkl').exists()
        assert data_env('toy', 'dev')['LAB_DATA_DIR'] == str(p.resolve())
        ev = Path(t) / 'evaluate.py'; ev.write_text('x=pd.read_pickle("/abs/cache.pkl")')
        assert check_evaluator(ev, 'toy')
        ev.write_text(load()); assert not check_evaluator(ev, 'toy')
    print('data selftest ok')
