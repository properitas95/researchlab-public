"""Revise a design, then freeze only fresh, unambiguous critique-approved inputs."""
import ast
import hashlib
import json
import math
import os
import re
import shlex
import sys
import tempfile
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

from lab import core, roles, critic, fetchdata


def replan(a):
    d = core.run_dir(a.run).resolve()
    c = core.cfg(d)
    exp = Path(c['exp_dir'])
    if (exp / 'loop.json').exists():
        raise ValueError('replan refused: experiment already frozen')
    fetchdata.materialize(d, exp)  # restores exp/data if the previous design round removed it
    instruction = core.prompt('replan', brief=(d / 'brief.md').read_text(),
                              critique=(d / 'critique.json').read_text(), guard=critic.design_notes(d),
                              last_error=os.environ.get('LAB_LAST_ERROR', '(none)'))
    (d / 'replan_reply.md').write_text(roles.call("replan", instruction, cwd=exp, run=d, write=True))
    core.event(d, 'replan', 'design revised; rerunning critic',
               reply_sha256=hashlib.sha256((d / 'replan_reply.md').read_bytes()).hexdigest())
    return critic.critic(a)


def freeze_spec(d) -> dict | str:
    try:
        d = Path(d)
        exp = Path(core.cfg(d)['exp_dir']).resolve()
        protocol = (exp / 'PROTOCOL.md').read_text()
        replies = [(d / n).read_text() for n in ('replan_reply.md', 'plan_reply.md') if (d / n).exists()]
        def line(key):
            for reply in replies:
                found = re.findall(r'^' + key + r':\s*(.+)$', reply, re.M)
                if found:
                    if len(set(found)) != 1:
                        raise ValueError(f'ambiguous {key} lines')
                    return shlex.split(found[0].strip().strip('`'))
            return []
        files, cmd = line('FILES'), core.strip_env(line('EVAL'))
        evaluators = files[:1] or [p.name for p in exp.glob('*.py') if '--split' in p.read_text()]
        if len(evaluators) != 1 or (files and len(files) != 2):
            return 'ambiguous or missing evaluator / FILES pair'
        evaluator = evaluators[0]
        mutable = files[1:] or re.findall(r'Mutable file\s*[:=]\s*`?([^\s`;]+)', protocol, re.I)
        if len(mutable) != 1 or not (exp / mutable[0]).is_file():
            return 'ambiguous or missing Mutable file'
        if (Path(mutable[0]).is_absolute() or '..' in Path(mutable[0]).parts
                or not (exp / mutable[0]).resolve().is_relative_to(exp)):
            return 'mutable file must be inside experiment'
        if not (exp / evaluator).is_file():
            return 'evaluator missing'
        py = core.PY_RE.findall((d / 'brief.md').read_text())
        if not cmd:
            if len(set(py)) > 1:
                return 'ambiguous Python interpreter in brief'
            cmd = [py[0] if py else 'python3' if os.name == 'nt' else sys.executable, evaluator, '--split', 'dev']
        elif len(set(py)) == 1:
            cmd = [py[0], *cmd[1:]]  # same interpreter the critic ran (critic._command substitutes the brief's)
        if (len(cmd) < 4 or not re.fullmatch(r'python(?:3(?:\.\d+)?)?', Path(cmd[0]).name)
                or (exp / cmd[1]).resolve() != (exp / evaluator).resolve()
                or cmd.count('--split') != 1 or cmd[cmd.index('--split') + 1] != 'dev'
                or any(x.startswith('--split=') for x in cmd)
                or any(re.search(r'[;&|<>\n\r`$]', x) or 'holdout' in x.lower() for x in cmd)):
            return 'EVAL must be a direct Python evaluator command selecting --split dev'
        rel = lambda p: Path(os.path.relpath(p, exp)).as_posix()
        frozen, skipped = [rel(exp / evaluator), 'PROTOCOL.md'], []
        frozen += [rel(x) for x in sorted((exp / 'data').rglob('*')) if x.is_file()]  # lab-materialized datasets are frozen inputs
        critique = json.loads((d / 'critique.json').read_text())
        for f in critique['findings']:
            if not f['id'].startswith('unfrozen-'):
                continue
            paths = list(f.get('paths', []))
            evidence = f.get('evidence', '')
            if '--frozen:' in evidence:
                paths += ast.literal_eval(evidence.split('--frozen:', 1)[1].strip())
            paths += re.findall(r"[`\"']((?:/|\.\./)[^`\"'\n]+)[`\"']", evidence)
            for name in dict.fromkeys(paths):
                p = (exp / name).resolve()
                if not p.exists():
                    continue
                entries = sorted(x for x in p.rglob('*') if x.is_file()) if p.is_dir() else [p]
                if p.is_dir() and sum(x.stat().st_size for x in entries) > 50 * 1024 * 1024:
                    skipped.append(rel(p))
                    continue
                frozen.extend(rel(x) for x in entries)
        if (exp / mutable[0]).resolve() in {(exp / x).resolve() for x in frozen}:
            return 'mutable file overlaps frozen inputs'
        values = re.findall(r'\bmin_delta\b\s*[:=]?\s*([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)', protocol)
        if len(set(values)) > 1 or ('min_delta' in protocol and not values):
            return 'ambiguous or invalid min_delta'
        delta = float(values[0]) if values else 0
        if not math.isfinite(delta) or delta < 0:
            return 'min_delta must be finite and nonnegative'
        holdout = cmd.copy()
        holdout[cmd.index('--split') + 1] = 'holdout'
        return dict(eval=shlex.join(cmd), holdout=shlex.join(holdout), mutable=mutable,
                    frozen=list(dict.fromkeys(frozen)), min_delta=delta, skipped=sorted(set(skipped)))
    except (OSError, ValueError, TypeError, KeyError, IndexError, SyntaxError) as e:
        return f'cannot derive freeze spec: {e}'


def autofreeze(a):
    d = core.run_dir(a.run).resolve()
    c = core.cfg(d)
    tagged = bool(getattr(a, 'tagged', False))  # campaign, after its replan rounds: freeze with the unresolved findings tagged
    reason = critic.freeze_gate(d, tagged)
    if reason:
        raise ValueError(f'autofreeze refused: {reason}')
    j = json.loads((d / 'critique.json').read_text())
    majors = j.get('majors')
    if type(majors) is not int or majors < 0 or majors != sum(f['severity'] == 'major' for f in j['findings']):
        raise ValueError('autofreeze refused: major count is invalid')
    _, fixable, limits = critic.triage(j)  # data-limitation majors are declared in PROTOCOL.md, fixable ones still block
    tags = []
    if tagged:
        if critic.validity(j):
            raise ValueError('autofreeze refused: validity blocker: ' + ', '.join(f['id'] for f in critic.validity(j)))
        tags = [f for f in j['findings'] if f['severity'] == 'blocker'] + fixable
    elif len(fixable) > c.get('auto_freeze_max_majors', 0):
        raise ValueError(f'autofreeze refused: {len(fixable)} fixable majors exceed allowance')
    spec = freeze_spec(d)
    if isinstance(spec, str):
        raise ValueError(f'autofreeze refused: {spec}; human decision required')
    # The current critic reads plan_reply only; never freeze a different, untested command.
    if shlex.split(spec['eval']) != critic._command(d, (d / 'brief.md').read_text()):
        raise ValueError('autofreeze refused: derived EVAL differs from critic dev command')
    if (d / 'replan_reply.md').exists():
        from lab.director import _last_event
        revision = _last_event(d, 'replan') or {}
        if revision.get('reply_sha256') != hashlib.sha256((d / 'replan_reply.md').read_bytes()).hexdigest():
            raise ValueError('autofreeze refused: replan reply changed; rerun replan')
    digest = hashlib.sha256((d / 'critique.json').read_bytes()).hexdigest()
    protocol = Path(c['exp_dir']) / 'PROTOCOL.md'
    original = protocol.read_text()
    if limits or tags:  # PROTOCOL.md is frozen: the declarations must be in place before rloop hashes it
        text = re.split(r'\n## (?:Declared limitations \(auto-accepted\)|Unresolved at freeze \(tagged\))', original)[0].rstrip('\n')
        if limits:
            text += '\n\n## Declared limitations (auto-accepted)\n\n' + '\n'.join(
                f"- `{f['id']}`: {' '.join(f['claim'].split())[:200]} (accepted by campaign autofreeze, not verified)" for f in limits) + '\n'
        if tags:
            text += '\n\n## Unresolved at freeze (tagged)\n\n' + '\n'.join(
                f"- `{f['id']}` ({f['severity']}): {' '.join(str(f.get('claim') or f.get('evidence', '')).split())[:200]}" for f in tags) + '\n'
        protocol.write_text(text)
    try:
        result = core.rloop('init', c['exp_dir'], '--q', c['q'], '--eval', spec['eval'], '--holdout', spec['holdout'],
                            '--frozen', *spec['frozen'], '--mutable', *spec['mutable'], '--min-delta', spec['min_delta'],
                            *(['--guard-root', core.ROOT] if c.get('guard') else []))
    except BaseException:
        protocol.write_text(original)
        raise
    if limits:
        core.event(d, 'autofreeze', 'auto-accepted data limitations', limitations=[f['id'] for f in limits],
                   accepted_limitations=[dict(id=f['id'], claim=f['claim']) for f in limits])
    if tags:
        ids = [f['id'] for f in tags]
        core.save_cfg(d, core.cfg(d) | dict(tagged=True, tags=[dict(id=f['id'], severity=f['severity'], claim=' '.join(str(f.get('claim') or f.get('evidence', '')).split())[:200]) for f in tags]))
        core.event(d, 'autofreeze', 'froze with unresolved findings tagged', tags=ids, tagged=True)
    core.event(d, 'freeze', result.strip(), auto=True, by='autofreeze', spec=spec, critique_sha256=digest)
    return spec


def selftest():
    with tempfile.TemporaryDirectory() as t:
        d, exp = Path(t), Path(t) / 'exp'
        exp.mkdir()
        core.save_cfg(d, dict(exp_dir=str(exp), provider='cmd:python3 fake.py', critic=['cmd:python3 fake.py', None]))
        (d / 'brief.md').write_text('Use python3.')
        (d / 'pick.json').write_text('{}')
        (d / 'dep.txt').write_text('dependency')
        (exp / 'PROTOCOL.md').write_text('Mutable file: candidate.py\nmin_delta=0.03')
        (exp / 'candidate.py').write_text('x=0')
        evaluator = "import sys,candidate\nassert sys.argv[-1]=='dev'\nx=0 if sys.argv[-1]=='dev' else 1\nprint('{\"score\":%s,\"pass\":true}' % candidate.x)\n# --split\n"
        (exp / 'evaluate.py').write_text(evaluator)
        (exp / 'probe_candidate.py').write_text('x=2')
        (d / 'plan_reply.md').write_text('FILES: evaluate.py candidate.py\nEVAL: python3 evaluate.py --split dev\nPROBE: probe_candidate.py')
        fake = '''import sys, json, pathlib, os
s=sys.stdin.read().split(chr(10)*2 + "---" + chr(10)*2, 1)[-1]
if s.startswith('TASK: replan'):
    pathlib.Path('candidate.py').write_text('x=1')
    print('FILES: evaluate.py candidate.py\\nEVAL: python3 evaluate.py --split dev')
else:
    bad = ('1: x=' + '0') in s or os.environ.get('LAB_TEST_DIRTY')
    f=dict(id='unfrozen-dependency',severity='major' if bad else 'minor',claim='dependency',
           evidence="add to --frozen: " + repr([".." + chr(47) + "dep.txt"]),fix='freeze dependency')
    print(json.dumps(dict(verdict='fix_first' if bad else 'freeze_ok',findings=[f])))
'''
        for p in (d / 'fake.py', exp / 'fake.py'):
            p.write_text(fake)
        a = Namespace(run=str(d))
        assert critic.critic(a)['majors'] == 1
        try:
            autofreeze(a)
            assert False, 'major accepted'
        except ValueError:
            pass
        with patch.object(roles, 'call', wraps=roles.call) as rc:
            replan(a)
        assert (exp / 'candidate.py').read_text() == 'x=1'
        wanted = ('Severity rubric', 'What the lab already enforces', 'failed critic before', 'Declared limitations', 'min_delta')
        assert all(k in rc.call_args_list[0].args[1] for k in wanted)  # replan gets the design guard up front
        assert all(k in core.prompt('plan', brief='b', pick='p', data='d', guard=critic.design_notes(d)) for k in wanted)
        for folder in ('small', 'large'):
            (d / folder).mkdir()
            (d / folder / 'data').write_text('data')
        with (d / 'large/data').open('r+b') as f:
            f.truncate(51 * 1024 * 1024)
        j = json.loads((d / 'critique.json').read_text())
        j['findings'][0]['paths'] = ['../small', '../large']
        (d / 'critique.json').write_text(json.dumps(j))
        (exp / 'data/ds').mkdir(parents=True)  # lab-materialized dataset: frozen without any critic finding
        (exp / 'data/ds/a.csv').write_text('1,2')
        spec = autofreeze(a)
        assert 'data/ds/a.csv' in spec['frozen'] and not Path(spec['frozen'][-1]).is_absolute()
        assert '../small/data' in spec['frozen'] and spec['skipped'] == ['../large']
        assert '../dep.txt' in spec['frozen'] and spec['min_delta'] == .03
        assert json.loads((exp / 'loop.json').read_text())['Q1']['frozen'] == spec['frozen']
        event = json.loads((d / 'events.jsonl').read_text().splitlines()[-1])
        assert event['auto'] and event['by'] == 'autofreeze' and len(event['critique_sha256']) == 64
        (d / 'replan_reply.md').write_text('FILES: evaluate.py candidate.py\nEVAL: PATH=/usr/bin:$PATH VAR=x python3 evaluate.py --split dev\n')
        assert freeze_spec(d)['eval'] == 'python3 evaluate.py --split dev'  # leading NAME=value assignments are stripped
        (d / 'replan_reply.md').unlink()
        (d / 'plan_reply.md').unlink()
        (exp / 'second.py').write_text('# --split')
        assert isinstance(freeze_spec(d), str)
        assert not (exp / '.rloop/holdout_Q1.json').exists()
        (exp / 'loop.json').unlink()
        (exp / 'second.py').unlink()
        from lab import campaign, director
        a.auto_freeze, a.replan_rounds = True, 2
        protocol0 = (exp / 'PROTOCOL.md').read_text()
        with patch.dict(os.environ, {'LAB_TEST_DIRTY': '1'}), patch.object(
                director, 'next_cmd', side_effect=lambda *_: 'USER: stop' if (exp / 'loop.json').exists() else 'USER: read protocol then ./lab.py freeze toy'):
            critic.critic(a)
            assert campaign.campaign(a) is None  # never parks on a stalled critique: replans to the cap, then freezes tagged
        ev = [json.loads(x) for x in (d / 'events.jsonl').read_text().splitlines()]
        assert sum(e['stage'] == 'campaign' and e['msg'] == 'run replan' for e in ev) == 2 and not [e for e in ev if e['stage'] == 'park']
        tag = [e for e in ev if e['stage'] == 'autofreeze' and e.get('tagged')][-1]
        assert 'unfrozen-dependency' in tag['tags'] and json.loads((d / 'run.json').read_text())['tagged'] is True
        assert '## Unresolved at freeze (tagged)\n\n- `' in (exp / 'PROTOCOL.md').read_text()
        assert [t['id'] for t in core.cfg(d)['tags']] == tag['tags']
        (exp / 'loop.json').unlink()
        (exp / 'PROTOCOL.md').write_text(protocol0)
        core.save_cfg(d, {k: v for k, v in core.cfg(d).items() if k not in ('tagged', 'tags')})
        # limitation majors do not block; fixable majors and blockers do; deterministic/never-listed ids stay fixable
        lim = dict(id='holdout-prior-execution-unknown', severity='major', claim='Prior holdout execution is unknown.', evidence='e', fix='f')
        fix = dict(id='min-delta-unset', severity='major', claim='MIN_DELTA is None.', evidence='e', fix='f')
        put = lambda *fs: (d / 'critique.json').write_text(json.dumps(dict(
            json.loads((d / 'critique.json').read_text()), findings=list(fs), verdict='fix_first',
            blockers=sum(f['severity'] == 'blocker' for f in fs), majors=sum(f['severity'] == 'major' for f in fs))))
        assert critic.limitation(lim) and not critic.limitation(fix)
        assert not critic.limitation(dict(id='score-ignores-mutable', severity='major', claim='Survivorship is unknown in the snapshot.'))
        assert not critic.limitation(dict(id='baseline-pass', severity='major', evidence='survivorship unknown'))  # deterministic: no claim
        assert not critic.limitation(dict(id='survivorship-zero-fill', severity='major', claim='Survivorship is unknown and returns are zero-filled.'))
        assert critic.limitation(dict(id='x', severity='known', claim='anything'))
        def route(*fs):
            put(*fs)
            with patch.object(sys.modules[__name__], 'replan') as rp, patch.object(sys.modules[__name__], 'autofreeze') as af, \
                    patch.object(director, 'next_cmd', side_effect=lambda *_: 'USER: stop' if rp.called or af.called
                                 else 'USER: read protocol then ./lab.py freeze toy'):
                campaign.campaign(a)
            return rp.called, af.called
        assert route(lim, lim | dict(id='lockbox-prior-exploration')) == (False, True)
        assert route(lim, fix) == (True, False)
        assert route(lim, dict(fix, severity='blocker')) == (True, False)
        assert route(dict(fix, id='score-ignores-mutable', claim='Survivorship is unknown.')) == (True, False)
        a.replan_rounds = 0  # rounds used up: tag and proceed unless a validity blocker remains
        def end_of_rounds(*fs):
            put(*fs)
            with patch.object(sys.modules[__name__], 'autofreeze') as af, patch.object(director, 'next_cmd', side_effect=lambda *_: 'USER: stop' if af.called
                                 else 'USER: read protocol then ./lab.py freeze toy'):
                return campaign.campaign(a), af.called and af.call_args.args[0].tagged
        assert end_of_rounds(lim, fix) == (None, True)
        assert end_of_rounds(dict(fix, severity='blocker', id='critic-failed')) == (None, True)
        for vid in ('probe-invalid', 'score-ignores-mutable', 'baseline-pass', 'baseline-score', 'dev-evaluation', 'holdout-executed-during-planning'):
            assert end_of_rounds(dict(fix, id=vid, severity='blocker')) == ('parked', False), vid
        assert end_of_rounds(dict(fix, id='probe-invalid')) == ('parked', False)
        put(lim, dict(lim, id='lockbox-prior-exploration'))
        spec = autofreeze(a)
        text = (exp / 'PROTOCOL.md').read_text()
        assert '## Declared limitations (auto-accepted)' in text and '`holdout-prior-execution-unknown`' in text and 'not verified' in text
        assert text.count('Declared limitations') == 1 and 'PROTOCOL.md' in spec['frozen']
        ev = [json.loads(x) for x in (d / 'events.jsonl').read_text().splitlines()]
        from lab import rloop  # the frozen hash covers the appended section: dropping it must read as tampering
        cfg = json.loads((exp / 'loop.json').read_text())['Q1']
        rloop.check_frozen(exp, cfg)
        (exp / 'PROTOCOL.md').write_text(text.split('\n## Declared')[0])
        try:
            rloop.check_frozen(exp, cfg)
            assert False, 'section not frozen'
        except SystemExit:
            pass
        (exp / 'PROTOCOL.md').write_text(text)
        assert [e for e in ev if e['stage'] == 'autofreeze'][-1]['limitations'] == ['holdout-prior-execution-unknown', 'lockbox-prior-exploration']
        put(lim, fix)
        (exp / 'loop.json').unlink()
        try:
            autofreeze(a)
            assert False, 'fixable major accepted'
        except ValueError:
            pass

    print('autofreeze selftest ok')
