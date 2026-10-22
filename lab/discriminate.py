"""Seal competing mechanisms, then judge dev-only diagnostics with fixed inequalities."""
import hashlib
import json
import math
import operator
import re
import sys
import tempfile
from pathlib import Path

from lab import core, roles
from lab.ablate import fixture, interpreter
from lab.posthoc import rejection, run_py
from lab.util import read as _read

OPS = {'>': operator.gt, '<': operator.lt, '>=': operator.ge, '<=': operator.le}


def numeric(x):
    return type(x) is int or type(x) is float and math.isfinite(x)


def require(ok):
    if not ok:
        raise ValueError("invalid hypotheses schema")


def validate(j):
    try:
        hs, ds = j['hypotheses'], j['diagnostics']
        require(isinstance(j['phenomenon'], str) and j['phenomenon'].strip())
        require(isinstance(hs, list) and 2 <= len(hs) <= 3 and isinstance(ds, list) and ds)
        names = [d['name'] for d in ds]
        require(len(set(names)) == len(names) and all(re.fullmatch('[a-z][a-z0-9_]*', n) for n in names))
        require(all(isinstance(d['what_to_compute'], str) and d['what_to_compute'].strip() for d in ds))
        require(len({h['id'] for h in hs}) == len(hs))
        ps = []
        for h in hs:
            require(all(isinstance(h[k], str) and h[k].strip() for k in ('id', 'mechanism')))
            require(isinstance(h['predictions'], list) and len(h['predictions']) >= 2)
            for p in h['predictions']:
                require(p['diagnostic'] in names and p['op'] in OPS and numeric(p['value']))
                require(all(isinstance(p[k], str) and p[k].strip() for k in ('metric', 'why')))
                ps.append((h['id'], p))
        for hid, p in ps:
            for kid, q in ps:
                if (hid != kid and (p['diagnostic'], p['metric']) == (q['diagnostic'], q['metric'])
                        and p['op'].startswith('>') and q['op'].startswith('<')
                        and (p['value'] > q['value'] or p['value'] == q['value']
                             and (p['op'] == '>' or q['op'] == '<'))):
                    return j
        raise ValueError('need incompatible predictions from different hypotheses on the same diagnostic metric')
    except (KeyError, TypeError) as e:
        raise ValueError('invalid hypotheses schema') from e


def seal(d):
    digest = hashlib.sha256((d / 'hypotheses.json').read_bytes()).hexdigest()
    events = [json.loads(s) for s in (d / 'events.jsonl').read_text().splitlines()]
    latest = next((e for e in reversed(events) if e['stage'] == 'hypotheses'), {})
    if latest.get('sha256') != digest:
        raise ValueError('hypotheses seal missing or changed')
    return digest


def hypotheses(a):
    d = core.run_dir(a.run).resolve()
    c, target = core.cfg(d), d / 'hypotheses.json'
    if target.exists() and not getattr(a, 'force', False):
        raise ValueError('hypotheses already sealed; use --force to replace')
    exp = Path(c.get('exp_dir', d / 'exp')).resolve()
    evidence = core.ledger(d, exp) if exp.exists() else d / 'LEDGER.md'
    inputs = '\n\n'.join(f'## {p.name}\n{_read(p)}' for p in
                         (d / 'brief.md', d / 'RESULT.md', evidence, d / 'field.md',
                          exp / 'PROTOCOL.md' if (exp / 'PROTOCOL.md').exists() else d / 'PROTOCOL.md'))
    instruction = core.prompt('hypotheses', inputs=inputs)
    for attempt in range(2):
        try:
            j = validate(core.last_json(roles.call("hypotheses", instruction, cwd=d, run=d, write=False)))
            break
        except ValueError as e:
            if attempt:
                raise
            instruction += f'\nPrevious response rejected: {e}. Return a corrected complete JSON object.\n'
    with target.open('w' if getattr(a, 'force', False) else 'x') as f:
        f.write(json.dumps(j, indent=1, ensure_ascii=False) + '\n')
    core.event(d, 'hypotheses', 'sealed predictions', sha256=hashlib.sha256(target.read_bytes()).hexdigest())


def measure(folder, py, script):
    # shortcut: posthoc static checks are not a sandbox for hostile Python.
    reason = 'script missing or escapes diagnostics' if not script.is_file() or script.is_symlink() else rejection(script.read_text(), folder)
    if reason:
        return {'valid': False, 'error': reason}
    rc, out, err = run_py(folder, py, script, 600)
    if rc is None:
        return {'valid': False, 'error': 'timeout 600s'}
    row = {'valid': False, 'stdout': out, 'stderr': err, 'returncode': rc}
    try:
        result = json.loads(out.strip().splitlines()[-1])
        if rc or not isinstance(result, dict):
            raise ValueError('expected successful JSON object')
        row.update(valid=True, metrics=result)
    except (ValueError, IndexError) as e:
        row['error'] = str(e)
    return row


def discriminate(a):
    d = core.run_dir(a.run).resolve()
    c, digest = core.cfg(d), seal(d)
    j = validate(json.loads((d / 'hypotheses.json').read_text()))
    exp, folder = Path(c.get('exp_dir', d / 'exp')).resolve(), d / 'diagnostics'
    if folder.is_symlink():
        raise ValueError('diagnostics must not be a symlink')
    folder.mkdir(exist_ok=True)
    lc = json.loads((exp / 'loop.json').read_text()).get(c['q'], {}) if (exp / 'loop.json').exists() else {}
    fallback = re.search(r'(?:[^\s`\"\']*/)?python(?:3(?:\.\d+)?)?\b', _read(d / 'brief.md'))
    py = interpreter(exp, lc.get('eval') or (fallback.group() if fallback else sys.executable))
    for diag in j['diagnostics']:
        (folder / (diag['name'] + '.py')).unlink(missing_ok=True)
    roles.call("discriminate", core.prompt('diagnostics', hypotheses=json.dumps(j), experiment=exp,
                evaluator=lc.get('eval', '(read brief)'), brief=_read(d / 'brief.md'),
                protocol=_read(exp / 'PROTOCOL.md')), cwd=folder, run=d, write=True)
    if seal(d) != digest:
        raise ValueError('seal changed during generation')
    core.event(d, 'diagnostics', 'starting sealed dev diagnostics', sha256=digest)
    records, judged = {}, []
    for diag in j['diagnostics']:
        name = diag['name']
        try:
            records[name] = measure(folder, py, folder / (name + '.py'))
        except (OSError, UnicodeError) as e:
            records[name] = {'valid': False, 'error': str(e)}
    if seal(d) != digest:
        raise ValueError('seal changed during measurement')
    lines = ['# Discrimination (dev only)', '', '| Hypothesis | Status | Prediction | Observed | Test |', '|---|---|---|---|---|']
    for h in j['hypotheses']:
        ps = []
        for p in h['predictions']:
            r = records[p['diagnostic']]
            value = r.get('metrics', {}).get(p['metric']) if r['valid'] else None
            test = ('pass' if OPS[p['op']](value, p['value']) else 'fail') if numeric(value) else 'untested'
            ps.append(p | {'observed': value if numeric(value) else None, 'status': test})
        status = 'refuted' if any(p['status'] == 'fail' for p in ps) else 'survives' if any(p['status'] == 'pass' for p in ps) else 'untested'
        judged.append(h | {'predictions': ps, 'status': status})
        for p in ps:
            cells = [h['id'], status, f"{p['diagnostic']}.{p['metric']} {p['op']} {p['value']}", p['observed'], p['status']]
            lines.append('| ' + ' | '.join(str(x).replace('|', '\\|').replace('\n', ' ') for x in cells) + ' |')
    survivors = [h['id'] for h in judged if h['status'] == 'survives']
    next_test = ('Add baseline_reproduction: reproduce the phenomenon with the original dev baseline and treatment before revising mechanisms.' if not survivors
                 else 'Add mechanism_intervention: independently toggle each surviving mechanism, fixing the others; preseal contrasting predictions for ' + ', '.join(survivors) + '.')
    summary = f'Exactly one hypothesis survives: {survivors[0]}.' if len(survivors) == 1 else f'Survivors: {survivors or "none"}. {next_test}'
    result = dict(sha256=digest, interpreter=py, diagnostics=records, hypotheses=judged, survivors=survivors, summary=summary)
    (d / 'discrimination.json').write_text(json.dumps(result, indent=1, ensure_ascii=False) + '\n')
    (d / 'DISCRIMINATION.md').write_text('\n'.join(lines) + '\n\n' + summary + '\nSurvival is provisional; untested predictions remain unresolved.\n')
    core.event(d, 'discriminate', summary, sha256=digest, survivors=survivors)


def selftest():
    with tempfile.TemporaryDirectory() as t:
        d, exp, a = fixture(t)
        (d / 'brief.md').write_text('toy dev study')
        j = {'phenomenon': 'toy', 'diagnostics': [{'name': n, 'what_to_compute': 'toy metric'} for n in ('contrast', 'check')],
             'hypotheses': [{'id': h, 'mechanism': h, 'predictions': [dict(diagnostic=n, metric='x', op=op, value=0, why='toy')
                             for n in ('contrast', 'check')]} for h, op in [('H1', '>'), ('H2', '<')]]}
        (d / 'fake.py').write_text(f'import json\nprint(json.dumps({j!r}))\n')
        hypotheses(a)
        try:
            hypotheses(a)
            assert False, 'overwrite accepted'
        except ValueError:
            pass
        folder = d / 'diagnostics'; folder.mkdir()
        code = 'import json; print(json.dumps({"x": 1}))\n'
        (folder / 'fake.py').write_text(f"from pathlib import Path\nfor n in ('contrast', 'check'): Path(n+'.py').write_text({code!r})\n")
        discriminate(a)
        result = json.loads((d / 'discrimination.json').read_text())
        assert [h['status'] for h in result['hypotheses']] == ['survives', 'refuted']
        events = [json.loads(s) for s in (d / 'events.jsonl').read_text().splitlines()]
        assert [e['stage'] for e in events] == ['hypotheses', 'diagnostics', 'discriminate']
        assert events[0]['sha256'] == result['sha256'] == seal(d)
        for p in j['hypotheses'][1]['predictions']: p['op'] = '>'
        try:
            validate(j)
            assert False, 'non-discriminating hypotheses accepted'
        except ValueError:
            pass
        (folder / 'bad.py').write_text('raise AssertionError("executed")\n# HoLdOuT\n')
        assert not measure(folder, sys.executable, folder / 'bad.py')['valid']
        assert rejection('open("../outside", "w")', folder)
    print('discriminate selftest ok')
