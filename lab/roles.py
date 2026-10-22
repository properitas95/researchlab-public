"""Stage roles, prompt memory and model-call receipts."""
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from lab import core, isolate

ROOT = core.ROOT


def _config():
    path = Path(os.environ.get('LAB_HARNESS', ROOT / 'harness.json'))
    if not path.exists():  # toy roots in selftests fall back to the packaged config
        path = core.PKG_DEFAULTS / 'harness.json'
    return json.loads(path.read_text())


def alternate(provider):
    """The other CLI for a stage that failed twice (LAB_FALLBACK_PROVIDER overrides; None = no alternate)."""
    return os.environ.get('LAB_FALLBACK_PROVIDER') or {'codex': 'claude', 'claude': 'codex'}.get(provider)


def role_of(stage):
    return _config()['stages'][stage]


def remember(role, text):
    if role not in _config()['roles']:
        raise ValueError(f'unknown role {role}')
    path = ROOT / 'memory' / 'roles' / f'{role}.md'
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a') as f:
        f.write(f'- {core.now()[:10]} {" ".join(text.splitlines())}\n')


def _network(instruction, cwd, model, timeout, env=None):
    # core.invoke's cmd transport permits per-call flags without changing shared process state.
    with tempfile.TemporaryDirectory(prefix='lab-network-') as tmp:
        out = Path(tmp) / 'reply.md'
        cmd = [shutil.which('codex') or 'codex', 'exec', '--ephemeral', '--skip-git-repo-check',
               '--sandbox', 'workspace-write', '--cd', str(Path(cwd).resolve()),
               '-c', 'sandbox_workspace_write.network_access=true', '--output-last-message', str(out)]
        cmd += core.codex_args(model) + ['-']
        relay = ("import subprocess,sys; subprocess.run(sys.argv[1:-1], stdout=subprocess.DEVNULL, check=True); "
                 "sys.stdout.write(open(sys.argv[-1], encoding='utf-8').read())")  # no shell redirection: works on Windows too
        join = subprocess.list2cmdline if os.name == 'nt' else shlex.join
        transport = 'cmd:' + join([sys.executable, '-c', relay, *cmd, str(out)])
        return core.invoke(transport, instruction, cwd, model, True, timeout=timeout, env=env)


PATH_RE = re.compile(r"(?<![\w.:/\\-])(?:/(?:Users|home|private)/|[A-Za-z]:\\(?=[\w .-]+\\))[^\s'\"`()<>\[\],;{}\x00]+")  # X:\dir\..., not LaTeX x:\mathbf{1}


def _outside_paths(reply, *ok):
    """Absolute paths named in a model reply that sit outside every folder in ok (observed, never enforced)."""
    ok = [os.path.normcase(os.path.realpath(p)) for p in ok]
    found = {m.group().rstrip('.:') for m in PATH_RE.finditer(reply)}
    inside = lambda q: any(q == d or q.startswith(d.rstrip(os.sep) + os.sep) for d in ok)
    return sorted(p for p in found if not (os.path.isabs(p) and inside(os.path.normcase(os.path.realpath(p)))))  # C:\x on POSIX is not a local path


def call(stage, instruction, cwd, run=None, write=None, provider=None, model=None, timeout=1800, files=None):
    config = _config()
    name = config['stages'][stage]
    role = config['roles'][name]
    run = Path(run) if run is not None else None
    overrides = json.loads((run / 'run.json').read_text()) if run and (run / 'run.json').exists() else {}
    pk, mk = ('reviewer', 'reviewer_model') if name == 'reviewer' else ('provider', 'model')
    if name not in ('researcher', 'reviewer'):
        overrides = {}
    provider = provider if provider is not None else overrides.get(pk, role['provider'])
    model = model if model is not None else overrides.get(mk, role['model'])
    model = model or None  # An explicit empty model keeps provider-specific panels on CLI defaults.
    if os.environ.get('LAB_STAGE_FALLBACK') and alternate(provider):  # campaign: third attempt of a stage that failed twice
        provider = alternate(provider)
        model = os.environ.get('LAB_FALLBACK_MODEL') or (overrides.get('reviewer_model') or config['roles']['reviewer']['model'] or None
                                                         if provider == 'claude' else None)
    write = bool(role['write'] if write is None else write and role['write']) and name != 'reviewer'
    path = Path(role['prompt'])
    if path.parts[0] == 'prompts':
        path = Path(*path.parts[1:])
    override = Path(os.environ.get('LAB_PROMPTS', ROOT / 'prompts')) / path
    fallback = ROOT / 'prompts' / path
    if not fallback.is_file():
        fallback = core.PKG_DEFAULTS / 'prompts' / path
    prefix = (override if override.is_file() else fallback).read_text()
    for scope, path in [('role', ROOT / 'memory' / 'roles' / f'{name}.md'),
                        ('global', ROOT / 'memory' / 'global' / 'INDEX.md')]:
        if scope in role['memory'] and path.exists():
            text = path.read_text()
            if scope == 'role':
                n = max(0, int(config['memory_lines']))
                text = '\n'.join(text.splitlines()[-n:]) if n else ''
            prefix += '\n\n' + text
    instruction = prefix + '\n\n---\n\n' + instruction
    started, reply, ok = time.monotonic(), '', False
    utc = core.now()
    try:
        # files=None keeps cwd; env is scrubbed either way. Bundled skills sit in the call folder only while the call runs
        with isolate.run(provider, cwd, files, write) as (ws, env), core.skills(ws, ROOT):
            if provider == 'codex' and role['network'] and write:
                reply = _network(instruction, ws, model, timeout, env)
            else:
                reply = core.invoke(provider, instruction, ws, model, write, timeout=timeout, env=env)
            if run is not None:
                for p in _outside_paths(reply, ROOT, cwd, ws, tempfile.gettempdir()):  # temp folders are the harness's own (Windows: C:\...\Temp)
                    core.event(run, 'isolate', f'model reply names a path outside the workspace: {p}')
        ok = True
        return reply
    finally:
        if run is not None:
            row = dict(utc=utc, stage=stage, role=name, provider=provider, model=model, write=write,
                       prompt_sha=hashlib.sha256(instruction.encode()).hexdigest()[:12],
                       chars_in=len(instruction), chars_out=len(reply), seconds=time.monotonic()-started, ok=ok, skills_sha=core.skills_sha(ROOT),
                       **core.fingerprint(provider, model))
            with (run / 'calls.jsonl').open('a') as f:
                f.write(json.dumps(row, ensure_ascii=False) + '\n')


def selftest():
    from unittest.mock import patch
    config = _config()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        prompts = root / 'prompts'
        (prompts / 'roles').mkdir(parents=True)
        for role in config['roles']:
            (prompts / 'roles' / f'{role}.md').write_text('ROLE ' + role)
            config['roles'][role]['provider'] = 'cmd:python3 fake.py'
        config['memory_lines'] = 2
        (root / 'harness.json').write_text(json.dumps(config))
        (root / 'fake.py').write_text('import sys\ns=sys.stdin.read()\nprint(s)\n')
        (root / 'run.json').write_text('{}')
        (prompts / 'propose.md').write_text('TASK: {{value}}')
        env = dict(LAB_HARNESS=str(root / 'harness.json'), LAB_PROMPTS=str(prompts))
        with patch(__name__ + '.ROOT', root), patch.dict(os.environ, env):
            remember('reviewer', 'old line')
            remember('reviewer', 'kept one')
            remember('reviewer', 'kept two')
            glob = root / 'memory/global/INDEX.md'
            glob.parent.mkdir(parents=True)
            glob.write_text('global memory')
            with patch.object(core, 'invoke', wraps=core.invoke) as invoke:
                reply = call('review', core.prompt('propose', value='review'), root, root, write=True)
                assert invoke.call_args.args[4] is False
            assert reply.startswith('ROLE reviewer') and 'TASK: review' in reply
            assert 'old line' not in reply and 'kept one' in reply and 'kept two' in reply
            assert 'global memory' not in reply
            assert core.prompt('critic') == (core.PROMPTS / 'critic.md').read_text()
            assert core.prompt_hash('propose') == hashlib.sha256((prompts / 'propose.md').read_bytes()).hexdigest()[:6]
            row = json.loads((root / 'calls.jsonl').read_text())
            assert row['ok'] and not row['write'] and row['role'] == role_of('review')
            assert row['prompt_sha'] == hashlib.sha256(reply[:-1].encode()).hexdigest()[:12]
            assert row['chars_in'] == len(reply)-1 and row['chars_out'] == len(reply)
            core.save_cfg(root, dict(provider='cmd:python3 fake.py', model='run-model',
                                     reviewer='cmd:python3 fake.py', reviewer_model='review-model'))
            call('plan', 'TASK: plan', root, root)
            assert json.loads((root / 'calls.jsonl').read_text().splitlines()[-1])['model'] == 'run-model'
            call('snapshot', 'TASK: snapshot', root, root)
            assert json.loads((root / 'calls.jsonl').read_text().splitlines()[-1])['model'] is None
            call('review', 'TASK: review', root, root, model='explicit')
            assert json.loads((root / 'calls.jsonl').read_text().splitlines()[-1])['model'] == 'explicit'
            reply = call('plan', 'TASK: plan', root, root, provider='cmd:python3 fake.py', model='')
            assert 'global memory' in reply
            (root / 'fake.py').write_text('raise RuntimeError("fake failure")\n')
            try:
                call('review', 'TASK: review', root, root)
                raise AssertionError('failure was swallowed')
            except RuntimeError:
                assert not json.loads((root / 'calls.jsonl').read_text().splitlines()[-1])['ok']
            # A fake Codex executable records argv; no real CLI or network is used.
            fake = root / 'codex'
            fake.write_text('#!/usr/bin/env python3\nimport json,pathlib,sys\na=sys.argv[1:]\n'
                            's=sys.stdin.read()\n'
                            'pathlib.Path(a[a.index("--output-last-message")+1]).write_text(json.dumps(a))\n')
            fake.chmod(0o755)
            if os.name == 'nt':  # Windows does not run extensionless scripts; a .cmd shim finds the fake first
                (root / 'codex.cmd').write_text(f'@"{sys.executable}" "%~dp0codex" %*\n', encoding='utf-8')
            home0 = root / 'home0'  # fake home: the fake echoes argv, which must not carry the real user's skill paths
            (home0 / '.agents/skills/s').mkdir(parents=True)
            (home0 / '.agents/skills/s/SKILL.md').write_text('---\nname: s\ndescription: s\n---\n')
            with patch.dict(os.environ, PATH=str(root) + os.pathsep + os.environ.get('PATH', ''), HOME=str(home0), USERPROFILE=str(home0)):
                for stage, write, enabled in [('snapshot', True, True), ('snapshot', False, False),
                                               ('plan', True, False), ('review', True, False)]:
                    args = json.loads(call(stage, 'TASK: flags', root, root, write=write,
                                           provider='codex', model=''))
                    assert ('sandbox_workspace_write.network_access=true' in args) == enabled
                    off = next(x for x in args if x.startswith('skills.config='))  # survives the network relay's shell quoting
                    assert f"{{path='{(home0 / '.agents/skills/s/SKILL.md').as_posix()}',enabled=false}}" in off, off
                    assert args[args.index('--sandbox')+1] == ('read-only' if stage == 'review' or not write else 'workspace-write')
            # clean mode: flags present by default, absent with "clean": false; fingerprint carries hashes, never contents
            (root / 'claude').write_text('#!/usr/bin/env python3\nimport json,sys\nprint(json.dumps(sys.argv[1:]))\n')
            (root / 'claude').chmod(0o755)
            if os.name == 'nt':
                (root / 'claude.cmd').write_text(f'@"{sys.executable}" "%~dp0claude" %*\n', encoding='utf-8')
            home = root / 'home'
            (home / '.codex').mkdir(parents=True)
            (home / '.codex' / 'config.toml').write_text('SECRET-CONFIG-BODY')
            (home / '.codex' / 'AGENTS.md').write_text('SECRET-AGENTS-BODY')
            for sk in ('.agents/skills/mine', '.codex/skills/.system/builtin'):
                (home / sk).mkdir(parents=True)
                (home / sk / 'SKILL.md').write_text('---\nname: x\ndescription: x\n---\n')
            fake_env = dict(HOME=str(home), USERPROFILE=str(home), PATH=str(root) + os.pathsep + os.environ.get('PATH', ''))
            def last(): return json.loads((root / 'calls.jsonl').read_text().splitlines()[-1])
            with patch.dict(os.environ, fake_env):
                args = json.loads(call('plan', 'TASK: clean', root, root, provider='codex', model=''))
                assert set(core.CLEAN_CODEX) <= set(args) and args[args.index('--model')+1] == config['codex_model']
                off = next(a for a in args if a.startswith('skills.config='))  # user skills switched off by path, CLI built-ins kept
                assert (home / '.agents/skills/mine/SKILL.md').as_posix() in off and '.system' not in off and 'enabled=false' in off
                assert f"model_reasoning_effort={config['codex_effort']}" in args
                fp, cfgs = last(), last()['user_cfg']
                assert fp['clean'] and fp['model_eff'] == config['codex_model'] and fp['effort'] == config['codex_effort'] and 'cli' in fp
                assert cfgs['.codex/config.toml'] == dict(sha=hashlib.sha256(b'SECRET-CONFIG-BODY').hexdigest()[:12], suppressed=True)
                assert cfgs['.codex/AGENTS.md']['suppressed'] is False
                assert 'SECRET' not in (root / 'calls.jsonl').read_text() and fp['host_skills_off'] >= 1 and 'mine' not in json.dumps(fp)
                assert fp['skills_sha'] == core.skills_sha(root)
                args = json.loads(call('review', 'TASK: clean', root, root, provider='claude', model='haiku'))
                assert set(core.CLEAN_CLAUDE) <= set(args) and '--bare' not in args and '--disable-slash-commands' not in args
                assert 'Skill' in args[args.index('--permission-mode') + 2].split(',')
                config['clean'] = False
                (root / 'harness.json').write_text(json.dumps(config))
                args = json.loads(call('plan', 'TASK: dirty', root, root, provider='codex', model=''))
                assert not {'--ignore-user-config', '--ignore-rules', '--disable', '--model'} & set(args) and not any('effort' in a or 'skills.config' in a for a in args)
                assert not last()['clean'] and not any(v['suppressed'] for v in last()['user_cfg'].values())
                args = json.loads(call('review', 'TASK: dirty', root, root, provider='claude', model='haiku'))
                assert not {'--setting-sources', '--strict-mcp-config'} & set(args)
                config['clean'] = True
                (root / 'harness.json').write_text(json.dumps(config))
            # bundled skills: in the call folder (claude and codex project dirs) only during the call; a user's own skill stays
            (root / 'ls.py').write_text('import json,os,pathlib,sys\nsys.stdin.read()\nh=[".claude/skills",".agents/skills"]\n'
                                        'print(json.dumps(dict(cwd=os.getcwd(), names=[sorted(os.listdir(x)) for x in h],'
                                        ' rd=pathlib.Path(".claude/skills/research-data/SKILL.md").read_text())))\n')
            names = sorted(core.skill_dirs(root))
            assert {'research-data', 'evaluator-design', 'experiment-stats', 'critique-checklist', 'research-writing'} <= set(names)
            (root / '.claude/skills/own').mkdir(parents=True)
            seen = json.loads(call('plan', 'TASK: skills', root, root, provider='cmd:python3 ls.py'))
            assert seen['names'] == [sorted(names + ['own']), names], seen['names']
            assert sorted(os.listdir(root / '.claude/skills')) == ['own'] and not (root / '.agents').exists()
            shutil.rmtree(root / '.claude')
            seen = json.loads(call('plan', 'TASK: skills', root, root, provider='cmd:python3 ls.py', files=[root / 'ls.py']))
            assert Path(seen['cwd']).resolve() != root.resolve() and seen['names'] == [names, names] and not Path(seen['cwd']).exists()
            assert not (root / '.claude').exists() and 'name: research-data' in seen['rd']
            (root / 'skills/research-data').mkdir(parents=True)  # workspace override wins over the packaged default
            (root / 'skills/research-data/SKILL.md').write_text('---\nname: research-data\ndescription: WORKSPACE-OVERRIDE\n---\n')
            assert 'WORKSPACE-OVERRIDE' in json.loads(call('plan', 'TASK: skills', root, root, provider='cmd:python3 ls.py'))['rd']
            assert last()['skills_sha'] != fp['skills_sha']
            shutil.rmtree(root / 'skills')
            # reply path scan: observed and logged, outside only
            inside = root / 'runs' / 'a.txt'
            with patch.object(core, 'invoke', lambda *a, **k: f'read {inside} and /private/zz/secret.csv. also C:\\data\\x.csv'):
                call('plan', 'TASK: scan', root, root)
            ev = [json.loads(x)['msg'] for x in (root / 'events.jsonl').read_text().splitlines() if '"isolate"' in x]
            assert len(ev) == 2 and all(m.startswith('model reply names a path outside the workspace: ') for m in ev), ev
            assert ev[0].endswith('/private/zz/secret.csv') and ev[1].endswith('C:\\data\\x.csv') and str(inside) not in ' '.join(ev), ev
    print('roles selftest ok (precedence, memory, read-only, receipts, prompt override, network flags, clean mode + fingerprint, bundled skills in the call folder, reply path scan)')
