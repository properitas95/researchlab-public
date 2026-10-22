"""Pre-freeze design review: deterministic dev checks plus one isolated critic."""
import ast
import hashlib
import json
import math
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

from lab import core, defects, roles

def _inputs(d):
    exp = Path(core.cfg(d)['exp_dir']).resolve()
    files = {d / 'brief.md', d / 'pick.json', exp / 'PROTOCOL.md'}
    reply = d / 'plan_reply.md'
    text = (exp / 'PROTOCOL.md').read_text()
    if reply.exists():
        files.add(reply)
        text += '\n' + reply.read_text()
    names = re.findall(r'[\w./-]+\.py\b', text)
    names += re.findall(r'[\w./-]+\.(?:json|txt|yaml|yml|toml|csv)\b',
                        '\n'.join(line for line in text.splitlines() if 'mutable' in line.lower()))
    for line in text.splitlines():
        if line.startswith('FILES:'):
            names += shlex.split(line[6:].strip().strip('`'))
    files.update(exp / n for n in names if (exp / n).is_file())  # prose mentions of outside paths are covered by the unfrozen-dependency check
    files.update(exp.glob('*.py'))  # include local loaders and newly added dependencies in freshness checks
    return exp, sorted(p.resolve() for p in files)

def _hashes(files):
    return {str(p): hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None for p in files}

_NEVER_ID = re.compile(r'score-ignores-mutable|baseline-(?:pass|score)|dev-evaluation|look-?ahead|leak|holdout-(?:executed|run)|executed-during-planning|'
                       r'score-hypothesis|hypothesis-mismatch|pass-encodes|unfrozen-|probe-invalid|no-mutable-probe', re.I)
_NEVER_TEXT = re.compile(r'look-?ahead|leakage|executed during planning|(?:holdout|lockbox)[^.]{0,40}(?:run|executed|opened) (?:during|while) planning|'
                         r'pass(?:es)? encodes|score does not depend on the mutable|zero-fill', re.I)
_LIMITS = (  # data limitations a redesign cannot remove; conservative keyword sets over id + claim
    r'prior[- ](?:holdout[- ])?(?:exploration|execution)|explored before|already (?:been )?(?:explored|opened)|(?:holdout|lockbox)[^.]{0,80}(?:not virgin|non-?virgin|reused|may (?:already )?have been (?:opened|explored))',
    r'(?:surviv|delist)[^.]{0,120}(?:unknown|unverified|not (?:supplied|provided|available|known)|may be|cannot be (?:verified|determined))',
    r'(?:sample|period|universe|window)[^.]{0,60}(?:fixed|set|dictated|specified) by (?:the )?(?:brief|task|user)|(?:brief|task) (?:fixes|fixed|specifies|dictates)[^.]{0,40}(?:sample|period|universe)',
    r'provenance|licen[sc]e|redistribut')


def limitation(f):
    """True for a data limitation (accepted, not fixed by redesign); deterministic findings (no claim) never are."""
    if not f.get('claim'):
        return False
    text = f"{f.get('id', '')} {f['claim']}".lower()
    if _NEVER_ID.search(str(f.get('id', ''))) or _NEVER_TEXT.search(text):
        return False
    return bool(f.get('accepted_as_limitation') or f.get('status') == 'accepted_as_limitation' or f.get('severity') == 'known'
                or any(re.search(k, text) for k in _LIMITS))


def triage(j):
    """(blockers, fixable majors, limitation majors) of a critique."""
    majors = [f for f in j['findings'] if f['severity'] == 'major']
    blockers = max(j['blockers'], sum(f['severity'] == 'blocker' for f in j['findings']))
    return blockers, [f for f in majors if not limitation(f)], [f for f in majors if limitation(f)]

_VALIDITY = re.compile(r'dev-evaluation|score-ignores-mutable|probe-invalid|baseline-(?:pass|score)|holdout-(?:executed|run)|executed-during-planning', re.I)


def validity(j):
    """Findings no tagged freeze may carry: the dev evaluation, the score/mutable link, the baseline or the holdout seal is broken."""
    bad = [f for f in j['findings'] if f['severity'] in ('blocker', 'major') and (
        _VALIDITY.search(str(f.get('id', ''))) or re.search(r'executed during planning|(?:holdout|lockbox)[^.]{0,40}(?:run|executed|opened) (?:during|while) planning', str(f.get('claim', '')), re.I))]
    if j['blockers'] > sum(f['severity'] == 'blocker' for f in j['findings']):
        bad.append(dict(id='blocker-count', severity='blocker', claim='blocker count exceeds listed findings'))
    return bad


def design_notes(d):
    """Up-front block for plan/replan: the critic's own rubric and enforced-controls text (single source: prompts/critic.md),
    classes that failed critic in earlier runs (methods ledger), and data limits declared in MANIFEST notes."""
    from lab import methods
    d = Path(d)
    text = core._prompt_path('critic').read_text(encoding='utf-8', errors='replace')
    parts = [m[0].strip() for m in (re.search(r'Severity rubric:.*?(?=\nUse freeze_ok)', text, re.S),
                                    re.search(r'What the lab already enforces.*?(?=\n\nFor each issue)', text, re.S)) if m]
    runs = {}
    for r in methods._rows():
        if r.get('kind') == 'finding' and r.get('severity') in ('blocker', 'major', 'known'):
            runs.setdefault(r['class'], set()).add(r['run'])
    top = sorted(runs, key=lambda k: (-len(runs[k]), k))[:6] or ['score-ignores-mutable', 'score-hypothesis-mismatch', 'min-delta-unset',
                                                                'baseline-pass', 'unfrozen-dependency']
    parts.append('Designs that failed critic before (check yours against each): ' + ', '.join(top) + '.')
    notes, plan = [], d / 'data_plan.json'
    for r in (json.loads(plan.read_text(encoding='utf-8')).get('datasets', []) if plan.exists() else []):
        try:
            n = json.loads((core.ROOT / r['manifest']).read_text(encoding='utf-8')).get('notes')
        except (KeyError, OSError, ValueError):
            continue
        notes += [f"{r['name']}: {x}" for x in ([n] if isinstance(n, str) else n or [])]
    parts.append('Data limitations already declared (write them, with those in the brief, under "## Declared limitations" in PROTOCOL.md now): '
                 + ('; '.join(notes) or '(none in manifests)') + '. Set min_delta from a measured dev noise floor (repeat or bootstrap) before handing over.')
    return '\n'.join(parts)


def freeze_gate(d, tagged=False):  # tagged: blockers are checked by validity() instead
    d = Path(d).resolve()
    if not (d / 'critique.json').exists():
        return 'critique missing: run critic before freeze'
    try:
        j = json.loads((d / 'critique.json').read_text())
        if j['inputs_sha256'] != _hashes(_inputs(d)[1]):
            return 'critique stale: inputs changed; rerun critic'
        if type(j['blockers']) is not int or j['blockers'] < 0 or not isinstance(j['findings'], list):
            raise ValueError('invalid findings/count')
        if not tagged and (j['blockers'] or any(f['severity'] == 'blocker' for f in j['findings'])):
            return 'critique has blockers: fix them and rerun critic'
        if j['verdict'] not in ('freeze_ok', 'fix_first'):
            raise ValueError('invalid verdict')
    except (OSError, ValueError, KeyError, TypeError):
        return 'critique stale or invalid: rerun critic'
    return None

def _command(d, brief):
    py = core.PY_RE.search(brief)
    python = py[0] if py else 'python3' if os.name == 'nt' else sys.executable
    reply = d / 'plan_reply.md'
    lines = re.findall(r'^EVAL:\s*(.+)$', reply.read_text() if reply.exists() else '', re.M)
    cmd = core.strip_env(shlex.split(lines[-1].strip().strip('`'))) if lines else [python, 'evaluate.py', '--split', 'dev']
    if py and cmd:
        cmd[0] = python
    if (len(cmd) < 4 or not re.fullmatch(r'python(?:3(?:\.\d+)?)?', Path(cmd[0]).name)
            or not cmd[1].endswith('.py') or any(x in (';', '&&', '|') or 'holdout' in x.lower() for x in cmd)):
        raise ValueError('EVAL must be `python3 <script>.py --split dev` with no interpreter flags (-I, -B, -X ...): '
                         'the lab isolates the evaluator itself and -I would switch that guard off' if cmd[1:2] and cmd[1].startswith('-')
                         else 'EVAL must be a direct Python script command for --split dev only')
    splits = [cmd[i+1] if x == '--split' and i+1 < len(cmd) else x.partition('=')[2]
              for i, x in enumerate(cmd) if x == '--split' or x.startswith('--split=')]
    if splits != ['dev']:
        raise ValueError('EVAL must select exactly --split dev')
    return cmd

def _reply_line(d, key):
    for n in ('replan_reply.md', 'plan_reply.md'):  # same lookup order as autofreeze
        found = re.findall(r'^' + key + r':\s*(.+)$', (d / n).read_text() if (d / n).exists() else '', re.M)
        if found:
            return shlex.split(found[-1].strip().strip('`'))
    return []

def probe_check(d, exp, cmd, base, add):
    """Dev score must change when the mutable file is swapped for the planner's probe variant."""
    files, probe = _reply_line(d, 'FILES'), _reply_line(d, 'PROBE')
    if len(files) != 2 or len(probe) != 1 or not (exp / probe[0]).is_file() or not (exp / files[1]).is_file():
        return add('no-mutable-probe', 'major', f'PROBE line missing or probe file absent (FILES {files}, PROBE {probe}); cannot show the score depends on the mutable file')
    if not all((exp / x).resolve().is_relative_to(exp) for x in (files[1], probe[0])):
        return add('no-mutable-probe', 'major', f'mutable/probe path leaves the experiment: {files[1]} {probe[0]}')
    t = Path(tempfile.mkdtemp(prefix='.critic-probe-', dir=exp.parent))  # sibling of exp so ../ dependencies resolve
    try:
        shutil.copytree(exp, t / exp.name, ignore=shutil.ignore_patterns('__pycache__'))  # stale .pyc of the mutable file must not mask the probe
        shutil.copyfile(exp / probe[0], t / exp.name / files[1])
        r = subprocess.run([sys.executable, *cmd[1:]] if os.name == 'nt' else cmd, cwd=t / exp.name, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)
        out = json.loads(r.stdout.strip().splitlines()[-1])
        if r.returncode or not isinstance(out, dict) or type(out.get('score')) not in (int, float) or not math.isfinite(out['score']):
            raise ValueError(f'exit {r.returncode}: {r.stdout[-1000:]} {r.stderr[-1000:]}')
    except (OSError, ValueError, IndexError, subprocess.TimeoutExpired) as e:
        return add('probe-invalid', 'major', f'probe {probe[0]} run failed: {str(e)[-1500:]}')
    finally:
        shutil.rmtree(t, ignore_errors=True)
    if math.isclose(out['score'], base, rel_tol=1e-9, abs_tol=1e-12):
        add('score-ignores-mutable', 'blocker', f'dev score with {files[1]} = {base}, with probe {probe[0]} = {out["score"]}: the score does not depend on the mutable file, so search over it cannot change it (a clipped score such as max(0, ·) also causes this)')

def critic(a):
    d = core.run_dir(a.run).resolve()
    c = core.cfg(d)
    exp, files = _inputs(d)
    hashes, findings = _hashes(files), []
    def add(id, severity, evidence):
        findings.append(dict(id=id, severity=severity, evidence=evidence))
    for p in files:
        if not p.is_file():
            add('missing-input', 'blocker', str(p))
    brief, protocol = (d / 'brief.md').read_text(), (exp / 'PROTOCOL.md').read_text()
    real = re.search(r'\breal\b|actual\s+data|실제|실측', brief, re.I)
    base = None
    try:
        cmd = _command(d, brief)
        evaluator = (exp / cmd[1]).resolve()
        sources = [p for p in files if p.suffix == '.py']
        if evaluator not in sources:
            raise ValueError('EVAL script is absent from review inputs')
        tree = ast.parse(evaluator.read_text())
        branches = [n.test for n in ast.walk(tree) if isinstance(n, (ast.If, ast.IfExp))]
        if not any(re.search(r'\bsplit\b|\bargv\b', ast.unparse(n)) for n in branches):
            add('split-branch', 'major', f'{evaluator}: no split-dependent branch; dev==holdout risk')
        for p in sources:
            source = p.read_text()
            for n, line in enumerate(source.splitlines(), 1):
                if real and (re.search(r'\bsynthetic\b', line, re.I) or re.search(
                        r'\b(data|returns|rets|panel|x)\s*=.*(?:random\.|rng\.(?:normal|uniform|standard_normal))', line)):
                    add('synthetic-data', 'blocker', f'{p}:{n}: {line.strip()}')
            symbols = {}
            def path_value(n):
                if isinstance(n, ast.Constant) and isinstance(n.value, str):
                    return n.value
                if isinstance(n, ast.Name):
                    return symbols.get(n.id, '')
                if isinstance(n, ast.Call) and ast.unparse(n.func) in ('Path', 'str') and n.args:
                    return path_value(n.args[0])
                if isinstance(n, ast.BinOp) and isinstance(n.op, (ast.Div, ast.Add)):
                    left, right = path_value(n.left), path_value(n.right)
                    return (str(Path(left) / right) if isinstance(n.op, ast.Div) else left + right) if left and right else ''
                return ''
            nodes = list(ast.walk(ast.parse(source)))
            for n in sorted((n for n in nodes if isinstance(n, ast.Assign)), key=lambda n: n.lineno):
                symbols.update({t.id: path_value(n.value) for t in n.targets if isinstance(t, ast.Name)})
            paths = sorted(set(re.findall(r'''["']((?:/|\.\./)[^"'\n]+)["']''', source)) | {v for n in nodes if (v := path_value(n)).startswith(('/', '../'))})
            dirs = [Path(x) for x in paths if (exp / x).is_dir()]
            paths += [str(root / (n.module.replace('.', '/') + '.py')) for root in dirs for n in nodes
                      if isinstance(n, ast.ImportFrom) and n.module and (exp / root / (n.module.replace('.', '/') + '.py')).is_file()]
            paths = [x for x in paths if not (exp / x).resolve().is_relative_to(exp / 'data')]  # exp/data/** is frozen automatically
            if paths:
                add('unfrozen-dependency', 'major', f'{p}: unfrozen dependency; add paths and their loaders/caches to --frozen: {paths}')
        if any(isinstance(n, ast.Call) and any(isinstance(v, ast.Constant) and v.value == 'holdout' for v in n.args) for n in ast.walk(tree)):
            raise ValueError('explicit holdout call in evaluator; dev execution refused')
        r = subprocess.run([sys.executable, *cmd[1:]] if os.name == 'nt' else cmd, cwd=exp, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)
        result = json.loads(r.stdout.strip().splitlines()[-1])
        if (r.returncode or not isinstance(result, dict) or type(result.get('score')) not in (int, float)
                or not math.isfinite(result['score']) or type(result.get('pass')) is not bool):
            raise ValueError(f'invalid last JSON or exit {r.returncode}: {r.stdout[-1000:]} {r.stderr[-1000:]}')
        if not result['pass']:
            add('baseline-pass', 'blocker', f'{shlex.join(cmd)}: baseline pass=false; validity must not encode effect: {result}')
        anchors = re.findall(r'(?:baseline|status[- ]quo)(?:[^\n]*?\b(?:score|value)\s*(?:=|:|is)?|\s*(?:=|:|is))\s*([-+]?\d+(?:\.\d+)?(?:e[-+]?\d+)?)', protocol, re.I)
        tol = 0.5 * 10 ** -len(anchors[0].partition('.')[2].split('e')[0]) if anchors else 0  # a protocol quotes the score rounded
        if anchors and not math.isclose(result['score'], float(anchors[0]), rel_tol=1e-8, abs_tol=max(tol, 1e-10)):
            add('baseline-score', 'blocker', f'PROTOCOL.md: status-quo score {anchors[0]}, dev baseline {result["score"]}')
        base = result['score']
    except (OSError, ValueError, SyntaxError, IndexError, subprocess.TimeoutExpired) as e:
        add('dev-evaluation', 'blocker', str(e))
    if base is not None:
        probe_check(d, exp, cmd, base, add)
    with tempfile.TemporaryDirectory(prefix='critic-') as t:
        parts = []
        for i, p in enumerate(files):
            if p.is_file():
                target = Path(t) / f'{i}_{p.name}'
                shutil.copy2(p, target)
                parts.append(f'## {p} (copy: {target.name})\n' + '\n'.join(
                    f'{n}: {line}' for n, line in enumerate(p.read_text(errors='replace').splitlines(), 1)))
        provider, model = c.get('critic', ['claude', 'sonnet'])
        if provider.startswith('cmd:'):
            for token in shlex.split(provider[4:]):
                if Path(token).name == token and (d / token).is_file():
                    shutil.copy2(d / token, Path(t) / token)
        try:
            review = core.last_json(roles.call("critic", core.prompt('critic', inputs='\n\n'.join(parts),
                                    checks=json.dumps(findings), known=defects.prompt_block()), cwd=t, run=d, write=False, provider=provider, model=model or ""))
            if (review.get('verdict') not in ('freeze_ok', 'fix_first') or not isinstance(review.get('findings'), list)
                    or any(not all(isinstance(f.get(k), str) and f[k] for k in ('id', 'severity', 'claim', 'evidence', 'fix'))
                           or f['severity'] not in ('blocker', 'major', 'minor') for f in review['findings'])):
                raise ValueError('invalid critic JSON')
            findings.extend(review['findings'])
            if review['verdict'] == 'fix_first' and not review['findings']:
                add('critic-verdict', 'blocker', 'critic requested fix_first without findings')
        except (RuntimeError, ValueError, TypeError, AttributeError, OSError, subprocess.TimeoutExpired) as e:
            add('critic-failed', 'blocker', str(e))
    if hashes != _hashes(_inputs(d)[1]):
        add('inputs-changed', 'blocker', 'inputs changed during review; rerun critic')
    known = defects.apply(findings)  # recurring data-layer findings: ledger status decides (lab/defects.py)
    blockers, majors = (sum(f['severity'] == s for f in findings) for s in ('blocker', 'major'))
    verdict = 'fix_first' if blockers or majors else 'freeze_ok'  # the model's verdict counts through its findings
    out = dict(blockers=blockers, majors=majors, verdict=verdict, findings=findings, inputs_sha256=hashes, known_defects=known)
    (d / 'critique.json').write_text(json.dumps(out, indent=2, ensure_ascii=False) + '\n')
    rows = ['# Design critique', '', f'Verdict: {verdict}', '', '| id | severity | claim | evidence | fix |', '|---|---|---|---|---|']
    rows += ['| ' + ' | '.join(str(f.get(k, '')).replace('|', '\\|').replace('\n', '<br>')
                             for k in ('id', 'severity', 'claim', 'evidence', 'fix')) + ' |' for f in findings]
    if known:
        rows += ['', '알려진 자료 제약(장부 확인 전, 결과는 잠정): ' + ', '.join(known)]
    (d / 'CRITIQUE.md').write_text('\n'.join(rows) + '\n')
    core.event(d, 'critic', f'{verdict}: {blockers} blockers, {majors} majors')
    return out

def selftest():
    from unittest.mock import patch
    with tempfile.TemporaryDirectory() as t, patch.object(core, 'ROOT', Path(t)):  # real defect ledger must not downgrade findings
        d, exp = Path(t), Path(t) / 'exp'
        exp.mkdir()
        (d / 'brief.md').write_text('Use real data.')
        (d / 'pick.json').write_text('{}')
        (d / 'data.csv').write_text('0')
        (d / 'fake.py').write_text('import sys; assert "pass" in sys.stdin.read(); print(\'{"findings":[],"verdict":"freeze_ok"}\')')
        core.save_cfg(d, dict(exp_dir=str(exp), critic=['cmd:python3 fake.py', None]))
        (exp / 'PROTOCOL.md').write_text('Baseline score = 0. Mutable: candidate.py')
        (exp / 'candidate.py').write_text('x = 0\n')
        bad = "import json\nx=float(open('../data.csv').read())\nprint(json.dumps({'score':x,'pass':x>0}))\n"
        (exp / 'evaluate.py').write_text(bad)
        a = SimpleNamespace(run=str(d))
        assert freeze_gate(d)
        j = critic(a)
        assert j['blockers'] and j['majors'] and freeze_gate(d)
        assert {'baseline-pass', 'split-branch', 'unfrozen-dependency'} <= {f['id'] for f in j['findings']}
        (exp / 'probe_candidate.py').write_text('x = 1\n')
        (d / 'plan_reply.md').write_text('FILES: evaluate.py candidate.py\nEVAL: python3 evaluate.py --split dev\nPROBE: probe_candidate.py\n')
        good = "import json,sys,candidate\nx=candidate.x if sys.argv[-1]=='dev' else 1\nprint(json.dumps({'score':x,'pass':True}))\n"
        (exp / 'evaluate.py').write_text(good)
        assert 'stale' in freeze_gate(d)
        assert critic(a)['verdict'] == 'freeze_ok' and freeze_gate(d) is None
        (exp / 'evaluate.py').write_text(good.replace('x=candidate.x', 'x=2') + '# synthetic input\n')
        assert {'baseline-score', 'synthetic-data'} <= {f['id'] for f in critic(a)['findings']}
        (d / 'plan_reply.md').write_text('EVAL: python3 evaluate.py --split holdout')
        assert 'dev-evaluation' in {f['id'] for f in critic(a)['findings']}
        ids = lambda: {f['id'] for f in critic(a)['findings']}
        reply = 'FILES: evaluate.py candidate.py\nEVAL: python3 evaluate.py --split dev\n'
        (d / 'plan_reply.md').write_text(reply + 'PROBE: probe_candidate.py\n')
        (exp / 'evaluate.py').write_text("import json,sys\nprint(json.dumps({'score':0,'pass':True}))\n")  # ignores candidate.py
        assert 'score-ignores-mutable' in ids()
        (exp / 'evaluate.py').write_text("import json,sys,candidate\nx=candidate.x if sys.argv[-1]=='dev' else 9\nprint(json.dumps({'score':x,'pass':True}))\n")
        assert not ids() & {'score-ignores-mutable', 'no-mutable-probe', 'probe-invalid'}
        (d / 'plan_reply.md').write_text(reply)
        assert 'no-mutable-probe' in ids()
        (exp / 'probe_candidate.py').write_text('raise SystemExit(3)\n')
        (d / 'plan_reply.md').write_text(reply + 'PROBE: probe_candidate.py\n')
        assert 'probe-invalid' in ids()
        (d / 'plan_reply.md').write_text('EVAL: PATH=/usr/bin:$PATH VAR=x python3 evaluate.py --split dev\n')  # env prefix is stripped, interpreter still substituted
        assert _command(d, 'Use python3.') == ['python3', 'evaluate.py', '--split', 'dev']
        (d / 'plan_reply.md').write_text('EVAL: X=1 python3 evaluate.py --split holdout\n')
        try:
            _command(d, ''); raise SystemExit('holdout accepted')
        except ValueError:
            pass
        # exp/data/** is frozen automatically: ./data and absolute paths inside it are not unfrozen dependencies, other outside paths still are
        (exp / 'data').mkdir(); (exp / 'data/x.csv').write_text('1')
        (d / 'plan_reply.md').write_text(reply + 'PROBE: probe_candidate.py\n')
        src = "import json,sys,candidate\nP1=%r\nP2='./data/x.csv'\nP3=%r\nx=candidate.x if sys.argv[-1]=='dev' else 9\nprint(json.dumps({'score':x,'pass':True}))\n"
        (exp / 'evaluate.py').write_text(src % (str(exp / 'data/x.csv'), 'unused'))
        assert 'unfrozen-dependency' not in ids()
        (exp / 'evaluate.py').write_text(src % (str(exp / 'data/x.csv'), '../data.csv'))
        assert 'unfrozen-dependency' in ids()
        assert 'clipped score such as max(0' in Path(__file__).read_text()
        for name in ('plan', 'replan'):
            assert 'unclipped and signed (no `max(0, ·)`)' in core._prompt_path(name).read_text(encoding='utf-8')
    print('critic selftest ok')
