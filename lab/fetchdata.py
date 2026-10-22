"""Stage fetch-data: the data engineer downloads public data into data/<name>/; the harness verifies it, not the model."""
import hashlib
import json
import os
import re
import shutil
import tempfile
from pathlib import Path

from lab import core, roles

NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]*')
KEYS = ('name', 'source_url', 'license', 'retrieved_utc', 'files')


def _sha(p):
    h = hashlib.sha256()
    with p.open('rb') as f:
        for b in iter(lambda: f.read(1 << 20), b''):
            h.update(b)
    return h.hexdigest()


def _verify(stage, name, cap, prune=True):
    """Return (manifest, None) when every listed file exists inside stage/name with the right sha256 and size, else (None, why)."""
    base = (stage / name).resolve()
    if not NAME.fullmatch(name) or not base.is_dir() or base.parent != stage.resolve():
        return None, 'dataset folder missing or name invalid'
    try:
        m = json.loads((base / 'MANIFEST.json').read_text(encoding='utf-8'))
    except (OSError, ValueError) as e:
        return None, f'MANIFEST.json unreadable: {e}'
    if not isinstance(m, dict) or any(k not in m for k in KEYS) or m['name'] != name or not isinstance(m['files'], list) or not m['files']:
        return None, f'MANIFEST.json needs {", ".join(KEYS)} (name matching the folder, non-empty files)'
    listed, total = set(), 0
    for f in m['files']:
        rel = f.get('path') if isinstance(f, dict) else None
        if not isinstance(rel, str) or os.path.isabs(rel):
            return None, f'bad file entry {f!r}'
        p = (base / rel).resolve()
        if base not in p.parents:
            return None, f'file outside data/{name}: {rel}'
        if not p.is_file() or p.is_symlink() or (base / rel).is_symlink():
            return None, f'listed file missing: {rel}'
        if _sha(p) != f.get('sha256'):
            return None, f'sha256 mismatch: {rel}'
        if f.get('bytes') is not None and f['bytes'] != p.stat().st_size:
            return None, f'size mismatch: {rel}'
        listed.add(p); total += p.stat().st_size
    if total > cap:
        return None, f'refused: {total / 1e6:.0f} MB exceeds LAB_DATA_MAX_MB'
    extra = [p for p in base.rglob('*') if p.is_file() and p not in listed and p.name != 'MANIFEST.json']
    for p in extra if prune else ():  # delivered data is exactly the verified list; data already in the workspace is never touched
        p.unlink()
    return (m, [str(p.relative_to(base)) for p in extra], total), None


def fetch_data(a):
    d = core.run_dir(a.run)
    p = core.cfg(d)['provider']
    provider = getattr(a, 'provider', None) or (p if p.startswith('cmd:') else None)  # cmd: = offline test provider
    cap = int(float(os.environ.get('LAB_DATA_MAX_MB', '500')) * 1e6)
    root = core.ROOT / 'data'
    root.mkdir(exist_ok=True)
    papers = d / 'field_papers.md'
    present = {}  # data already in the workspace (copied by a demo or by hand), verified against its own MANIFEST
    for m in sorted(root.glob('*/MANIFEST.json')):
        name = m.parent.name
        got, bad = _verify(root, name, cap, prune=False) if not name.startswith('.') else (None, 'hidden')
        if got:
            present[name] = dict(name=name, status='present', reason='already in the workspace; sha256 verified by the lab', manifest=f'data/{name}/MANIFEST.json',
                                 bytes=got[2], license=got[0]['license'], files=[f'data/{name}/{f["path"]}' for f in got[0]['files']])
    with tempfile.TemporaryDirectory(prefix='.fetch-', dir=root) as t:
        stage = Path(t).resolve()
        instr = core.prompt('fetch_data', brief=(d / 'brief.md').read_text(), pick=(d / 'pick.json').read_text(),
                            papers=papers.read_text() if papers.exists() else '(no papers read)', max_mb=cap // 10**6,
                            present='\n'.join(f'- {r["name"]}: {", ".join(r["files"])}' for r in present.values()) or '(none)')
        roles.call('fetch-data', instr, cwd=stage, run=d, write=True, provider=provider)
        try:
            ask = json.loads((stage / '_plan.json').read_text(encoding='utf-8'))
            needed = bool(ask['needed'])
        except (OSError, ValueError, KeyError, TypeError):
            raise RuntimeError('model did not write a valid _plan.json')
        plan = dict(needed=needed, reason=str(ask.get('reason', '')), retrieved_utc=core.now(), max_mb=cap // 10**6, datasets=[])
        if needed:
            for x in ask.get('datasets') or []:
                name, why = str(x.get('name', '')), str(x.get('reason', ''))
                if name in present:
                    continue  # the lab's verified row wins over what the model says
                row = dict(name=name, status=x.get('status') if x.get('status') in ('fetched', 'refused', 'failed') else 'failed', reason=why)
                if row['status'] == 'fetched':
                    got, bad = _verify(stage, name, cap)
                    dest = root / name
                    if not bad and dest.exists():
                        bad = f'data/{name} already exists'
                    if bad:
                        row.update(status='refused' if bad.startswith('refused') else 'failed', reason=bad)
                        core.event(d, 'fetch-data', f'{name}: {bad}', dataset=name)
                    else:
                        m, dropped, total = got
                        shutil.move(str(stage / name), str(dest))
                        row.update(manifest=f'data/{name}/MANIFEST.json', bytes=total, license=m['license'], files=[f'data/{name}/{f["path"]}' for f in m['files']])
                        if dropped:
                            core.event(d, 'fetch-data', f'{name}: dropped unlisted files {dropped}', dataset=name)
                plan['datasets'].append(row)
    plan['datasets'] = list(present.values()) + plan['datasets']
    (d / 'data_plan.json').write_text(json.dumps(plan, indent=1, ensure_ascii=False) + '\n', encoding='utf-8')
    ok = [r['name'] for r in plan['datasets'] if r['status'] in ('fetched', 'present')]
    core.event(d, 'fetch-data', f'needed={needed}; fetched {len(ok)}/{len(plan["datasets"])}: {", ".join(ok) or "-"}')


def materialize(d, exp):
    """Put every fetched/present dataset of data_plan.json into <exp>/data/<name>/ as real files (hardlinks on one filesystem, else copies;
    never symlinks, so the frozen hash and the guard see them) and verify each against its MANIFEST. Idempotent; returns the dataset names."""
    f = d / 'data_plan.json'
    if not f.exists():
        return []
    dest, done, modes = Path(exp) / 'data', [], {}
    for r in json.loads(f.read_text(encoding='utf-8'))['datasets']:
        if r['status'] not in ('fetched', 'present'):
            continue
        name, src = r['name'], core.ROOT / 'data' / r['name']
        got, bad = _verify(src.parent, name, 1 << 62, prune=False)
        if bad:
            raise RuntimeError(f'data/{name}: {bad}')
        if (dest / name).is_dir() and not _verify(dest, name, 1 << 62, prune=False)[1]:
            done.append(name); continue  # already in place and intact
        shutil.rmtree(dest / name, ignore_errors=True)
        for rel in ['MANIFEST.json'] + [x['path'] for x in got[0]['files']]:
            (dest / name / rel).parent.mkdir(parents=True, exist_ok=True)
            try:
                os.link(src / rel, dest / name / rel); modes[name] = modes.get(name, 'link')
            except OSError:
                shutil.copy2(src / rel, dest / name / rel); modes[name] = 'copy'
        _, bad = _verify(dest, name, 1 << 62, prune=False)
        if bad:
            raise RuntimeError(f'exp/data/{name}: {bad}')
        done.append(name)
    if modes:
        core.event(d, 'materialize', f'data into {dest}: ' + ', '.join(f'{k} ({v})' for k, v in modes.items()), datasets=modes)
    return done


def plan_input(d):
    """Text for the plan prompt: data_plan.json summary with paths relative to the experiment folder (./data/<name>/<file>)."""
    f = d / 'data_plan.json'
    if not f.exists():
        return '(none: no fetch-data stage ran; use only data the brief names)'
    p = json.loads(f.read_text(encoding='utf-8'))
    if not p['datasets']:
        return f'(none needed: {p["reason"]})'
    return '\n'.join(f'- {r["name"]} [{r["status"]}] {r["reason"]}' + (f' manifest: ./{r["manifest"]}; files: {", ".join("./" + f for f in r["files"])}' if r['status'] in ('fetched', 'present') else '')
                     for r in p['datasets']) + '\nThese paths are relative to the experiment folder (your current directory); the lab has placed the files there.'


def selftest():
    from types import SimpleNamespace
    from unittest.mock import patch
    from lab import director
    with tempfile.TemporaryDirectory() as t:
        t = Path(t).resolve()
        (t / 'runs/r').mkdir(parents=True)
        d = t / 'runs/r'
        (d / 'brief.md').write_text('FF49 daily'); (d / 'pick.json').write_text('{"id":"C1","title":"x"}')
        fake = t / 'fake.py'
        # case selects the behaviour; every case writes one dataset 'ds' with a 5-byte file
        fake.write_text('''import hashlib, json, os, sys
from pathlib import Path
s = sys.stdin.read(); assert 'TASK: fetch-data' in s
case = Path(__file__).with_name('case.txt').read_text()
def ds(sha=None, path='a.csv', name='ds'):
    Path(name).mkdir(exist_ok=True); Path(name, 'a.csv').write_bytes(b'x,y\\n1,2')
    sha = sha or hashlib.sha256(b'x,y\\n1,2').hexdigest()
    Path(name, 'MANIFEST.json').write_text(json.dumps(dict(name=name, source_url='http://u', license='CC0', retrieved_utc='t', files=[dict(path=path, sha256=sha, bytes=7, rows=1)])))
plan = dict(needed=True, reason='r', datasets=[dict(name='ds', status='fetched', reason='ok')])
if case == 'none': plan = dict(needed=False, reason='synthetic only', datasets=[]); ds()
elif case == 'ok': ds(); Path('ds/scratch.py').write_text('x')
elif case == 'badsha': ds(sha='0' * 64)
elif case == 'outside': ds(path='../stray.txt'); Path('stray.txt').write_text('z')
Path('_plan.json').write_text(json.dumps(plan))
''')
        (d / 'run.json').write_text('{}')
        def run(case):
            (t / 'case.txt').write_text(case)
            for x in (d / 'data_plan.json', t / 'data'):
                shutil.rmtree(x, True) if x.is_dir() else x.unlink(missing_ok=True)
            with patch.object(core, 'ROOT', t), patch.object(roles, 'ROOT', t), patch.dict(os.environ):
                fetch_data(SimpleNamespace(run=str(d), provider='cmd:python3 ' + str(fake)))
            return json.loads((d / 'data_plan.json').read_text())
        p = run('ok'); row = p['datasets'][0]
        assert p['needed'] and row['status'] == 'fetched' and row['license'] == 'CC0' and row['files'] == ['data/ds/a.csv'], p
        assert sorted(x.name for x in (t / 'data/ds').iterdir()) == ['MANIFEST.json', 'a.csv'] and [x.name for x in (t / 'data').iterdir()] == ['ds']  # scratch dropped, staging removed
        assert 'dropped unlisted' in (d / 'events.jsonl').read_text() and 'ds [fetched]' in plan_input(d)
        p = run('badsha'); assert p['datasets'][0]['status'] == 'failed' and 'sha256 mismatch' in p['datasets'][0]['reason'] and not (t / 'data/ds').exists()
        assert 'sha256 mismatch' in (d / 'events.jsonl').read_text()
        p = run('outside'); assert p['datasets'][0]['status'] == 'failed' and 'outside' in p['datasets'][0]['reason'] and not (t / 'stray.txt').exists() and not (t / 'data/ds').exists()
        with patch.dict(os.environ, {'LAB_DATA_MAX_MB': '0.000001'}):
            p = run('ok'); assert p['datasets'][0]['status'] == 'refused' and not (t / 'data/ds').exists()
        p = run('none'); assert p == p | dict(needed=False, datasets=[]) and not (t / 'data/ds').exists() and 'none needed' in plan_input(d)
        def keep(case):  # data a demo already copied in: registered as present, never pruned or re-downloaded
            (t / 'case.txt').write_text(case); (d / 'data_plan.json').unlink(missing_ok=True)
            k = t / 'data/kr'; k.mkdir(parents=True, exist_ok=True); (k / 'r.tsv').write_bytes(b'abc'); (k / 'notes.txt').write_text('mine')
            (k / 'MANIFEST.json').write_text(json.dumps(dict(name='kr', source_url='local', license='internal', retrieved_utc='t', files=[dict(path='r.tsv', sha256=hashlib.sha256(b'abc').hexdigest(), bytes=3)])))
            with patch.object(core, 'ROOT', t), patch.object(roles, 'ROOT', t):
                fetch_data(SimpleNamespace(run=str(d), provider='cmd:python3 ' + str(fake)))
                return json.loads((d / 'data_plan.json').read_text()), plan_input(d)
        p, txt = keep('none'); assert p['datasets'][0] | dict(status='present', files=['data/kr/r.tsv']) == p['datasets'][0] and (t / 'data/kr/notes.txt').exists()
        assert './data/kr/r.tsv' in txt and str(t) not in txt and 'kr [present]' in txt, txt  # paths are experiment-relative
        ex = t / 'exp'; ex.mkdir()  # materialize: real files inside the experiment folder, hash-verified, idempotent, restorable
        with patch.object(core, 'ROOT', t):
            assert materialize(d, ex) == ['kr'] and sorted(x.name for x in (ex / 'data/kr').iterdir()) == ['MANIFEST.json', 'r.tsv']
            f = ex / 'data/kr/r.tsv'
            assert f.read_bytes() == b'abc' and not f.is_symlink() and not (ex / 'data/kr/notes.txt').exists() and f.samefile(t / 'data/kr/r.tsv')
            assert '"materialize"' in (d / 'events.jsonl').read_text()
            shutil.rmtree(ex / 'data'); assert materialize(d, ex) == ['kr'] and f.read_bytes() == b'abc'
            (t / 'data/kr/r.tsv').write_bytes(b'abd'); shutil.rmtree(ex / 'data')
            try:
                materialize(d, ex); raise SystemExit('tampered source accepted')
            except RuntimeError as e:
                assert 'sha256 mismatch' in str(e)
            (t / 'data/kr/r.tsv').write_bytes(b'abc')
        shutil.rmtree(ex)
        shutil.rmtree(t / 'data/kr')
        # director: pick -> fetch-data -> plan
        exp = t / 'exp'; exp.mkdir()
        core.save_cfg(d, core.cfg(d) | dict(exp_dir=str(exp), q='Q1'))
        for f in ('candidates.json', 'novelty.json'): (d / f).write_text('[]')
        (d / 'data_plan.json').unlink()
        with patch.object(core, 'ROOT', t):
            assert director.next_cmd(d) == './lab.py fetch-data r', director.next_cmd(d)
            run('none'); assert director.next_cmd(d) == './lab.py plan r'
    print('fetchdata selftest ok (needed=false, verified fetch, wrong sha, path outside, size cap, director order)')
