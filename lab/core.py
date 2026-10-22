"""Shared pieces: run folder, event log, model calls. One research question = one runs/<id>/ folder."""
import contextlib
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import threading
import sys
import tempfile
from pathlib import Path

from lab import rloop as rloop_mod

PKG_DEFAULTS = Path(__file__).resolve().parent / "defaults"  # packaged prompts, skills, harness.json, examples, vendor
def strip_env(cmd):
    """Drop leading NAME=value tokens (`PATH=... python3 evaluate.py ...`): the lab runs the evaluator in its own environment."""
    i = 0
    while i < len(cmd) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", cmd[i], re.S):
        i += 1
    return cmd[i:]


PY_RE = re.compile(r"(?:[\w./-]+/)?python(?:3(?:\.\d+)?)?\b")  # a python interpreter named in a brief or command


def win_py(py):
    """python/python3 is the Microsoft Store stub on Windows: use the running interpreter there."""
    return sys.executable if os.name == "nt" and py in ("python", "python3") else py


def find_root():
    """Workspace: LAB_HOME, else the nearest folder at or above cwd with harness.json, else the repo this file sits in."""
    if os.environ.get("LAB_HOME"):
        return Path(os.environ["LAB_HOME"]).resolve(), True
    cwd = Path.cwd().resolve()
    for p in (cwd, *cwd.parents):
        if (p / "harness.json").is_file():
            return p, True
    repo = PKG_DEFAULTS.parents[1]
    return (repo, True) if (repo / "harness.json").is_file() else (cwd, False)


ROOT, FOUND = find_root()
PROMPTS = ROOT / "prompts"
RLOOP = Path(__file__).resolve().parent / "rloop.py"
# Claude calls default to haiku (cheap, still a different model family from the codex planner/proposer)
DEFAULTS = {"provider": "codex", "model": None, "reviewer": "claude", "reviewer_model": "haiku", "iters": 5, "wall_min": 20,
            "q": "Q1", "critic": ["claude", "haiku"], "screen_panel": [["codex", None], ["claude", "haiku"], ["codex", None]],
            "panel": [["codex", None, "methodologist"], ["claude", "haiku", "skeptic"], ["codex", None, "reproducibility"]]}


def now():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def run_dir(rid):
    d = Path(rid) if Path(rid).is_dir() else ROOT / "runs" / rid
    if not (d / "run.json").exists():
        sys.exit(f"no run.json in {d}: start with `lab.py new`")
    return d


def cfg(d):
    return DEFAULTS | json.loads((d / "run.json").read_text(encoding="utf-8", errors="replace"))


def save_cfg(d, c):
    (d / "run.json").write_text(json.dumps(c, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", errors="replace")


def event(d, stage, msg, **kw):
    with (d / "events.jsonl").open("a", encoding="utf-8", errors="replace") as fh:
        fh.write(json.dumps({"utc": now(), "stage": stage, "msg": msg, **kw}, ensure_ascii=False) + "\n")
    print(f"[{stage}] {msg}")


def _prompt_path(name):
    """LAB_PROMPTS, then the workspace prompts/, then the packaged defaults."""
    for base in (Path(os.environ.get("LAB_PROMPTS", PROMPTS)), PROMPTS, PKG_DEFAULTS / "prompts"):
        if (base / f"{name}.md").is_file():
            return base / f"{name}.md"
    return PKG_DEFAULTS / "prompts" / f"{name}.md"


def prompt(name, **fields):
    text = _prompt_path(name).read_text(encoding="utf-8", errors="replace")
    for k, v in fields.items():
        text = text.replace("{{" + k + "}}", str(v))
    return text


def prompt_hash(name):
    return hashlib.sha256(_prompt_path(name).read_bytes()).hexdigest()[:6]


def harness():
    """harness.json the way roles reads it: LAB_HARNESS, else the workspace copy, else the packaged default."""
    path = Path(os.environ.get("LAB_HARNESS", ROOT / "harness.json"))
    return json.loads((path if path.exists() else PKG_DEFAULTS / "harness.json").read_text(encoding="utf-8", errors="replace"))


# Clean mode ("clean": true in harness.json, default): results must not depend on the user's personal CLI setup.
# Flags proven in docs/evidence/clean_mode_probe.md. Login stays: no CODEX_HOME change, no --bare (needs an API key).
# User skills: claude drops the user's skills folder under --setting-sources project; codex lists the user's personal skill folders
# unless each SKILL.md is switched off by path (host_skills). Project skills in the call folder stay visible to both.
# Known limit: codex still reads the user's global instructions file in its home (no flag without moving CODEX_HOME); claude keeps project settings and
# CLAUDE.md of the run folder. The fingerprint records both.
CLEAN_CODEX = ["--ignore-user-config", "--ignore-rules", "-c", "project_doc_max_bytes=0",
               "--disable", "plugins", "--disable", "hooks", "--disable", "memories", "--disable", "apps"]
CLEAN_CLAUDE = ["--setting-sources", "project", "--strict-mcp-config"]  # no --disable-slash-commands: it also hides project skills
SKILL_HOMES = {"claude": ".claude/skills", "codex": ".agents/skills"}  # project skill folders each CLI reads in its cwd
# per provider: user files that may shape a call -> True when clean mode suppresses them
USER_FILES = {"codex": {".codex/config.toml": True, ".codex/AGENTS.md": False},
              "claude": {".claude/settings.json": True, ".claude/CLAUDE.md": True}}


def pinned(provider, model=None):
    """(model, effort) a call really uses: explicit model, else the codex pin from harness.json in clean mode."""
    h = harness()
    if provider != "codex" or not h.get("clean", True):
        return model, None
    return model or h.get("codex_model"), h.get("codex_effort")


def host_skills():
    """SKILL.md files codex would list from the user's home (its personal skill folders; dot folders such as
    .system are the CLI's own and stay). Symlinked skills appear under both spellings so the path selector matches."""
    out = set()
    for base in (Path.home() / ".agents" / "skills", Path.home() / ".codex" / "skills"):
        for top, dirs, files in os.walk(base, followlinks=True):
            dirs[:] = [x for x in dirs if not x.startswith(".")]
            if "SKILL.md" in files:
                f = Path(top) / "SKILL.md"
                out |= {f.as_posix(), f.resolve().as_posix()}
    return sorted(p for p in out if "'" not in p)  # TOML literal strings cannot hold a quote


def mcp_servers():
    """Attached MCP servers from harness.json "mcp": name -> {command, args, cwd}; workspace-relative paths made absolute."""
    out = {}
    for name, m in harness().get("mcp", {}).items():
        res = lambda v: str(ROOT / v) if v and not Path(v).is_absolute() and (ROOT / v).exists() else v
        cmd = res(m["command"])
        if not (Path(cmd).is_file() or shutil.which(cmd)):  # registered but not installed: starting it would only time out every call
            continue
        out[name] = {"command": cmd, "args": [res(x) for x in m.get("args", [])], **({"cwd": res(m["cwd"])} if m.get("cwd") else {})}
    return out


def claude_mcp_args(tmp):
    """--mcp-config=<json file in tmp> holding only the attached servers (with --strict-mcp-config in clean mode nothing else loads)."""
    srv = mcp_servers()
    if not srv:
        return []
    f = Path(tmp) / "mcp.json"
    f.write_text(json.dumps({"mcpServers": {n: {"type": "stdio", **m} for n, m in srv.items()}}), encoding="utf-8")
    return [f"--mcp-config={f}"]  # "=" form: the flag is variadic and would swallow the prompt


def codex_mcp_args():
    out = []
    for n, m in mcp_servers().items():
        out += ["-c", f"mcp_servers.{n}.command={json.dumps(m['command'])}", "-c", f"mcp_servers.{n}.args={json.dumps(m['args'])}", "-c", f"mcp_servers.{n}.startup_timeout_sec=60"]  # torch imports take 10s+ cold
        out += ["-c", f"mcp_servers.{n}.cwd={json.dumps(m['cwd'])}"] if "cwd" in m else []
    return out


def codex_args(model=None):
    """codex exec flags for clean mode (none when \"clean\": false) plus the model and reasoning effort pin."""
    m, effort = pinned("codex", model)
    clean = harness().get("clean", True)
    off = host_skills() if clean else []
    skills = ["-c", "skills.config=[" + ",".join(f"{{path='{p}',enabled=false}}" for p in off) + "]"] if off else []
    return (CLEAN_CODEX + skills if clean else []) + (["--model", m] if m else []) + (["-c", f"model_reasoning_effort={effort}"] if effort else []) + codex_mcp_args()


def skill_dirs(root=None):
    """name -> folder of each bundled skill; <workspace>/skills/<name> wins over the packaged default, like prompts."""
    out = {}
    for base in (PKG_DEFAULTS / "skills", Path(root or ROOT) / "skills"):
        out |= {f.parent.name: f.parent for f in sorted(base.glob("*/SKILL.md"))} if base.is_dir() else {}
    return out


def skills_sha(root=None):
    h = hashlib.sha256()
    for name, src in sorted(skill_dirs(root).items()):
        for f in sorted(p for p in src.rglob("*") if p.is_file()):
            h.update(f"{name}/{f.relative_to(src).as_posix()}".encode() + b"\0" + f.read_bytes())
    return h.hexdigest()[:12]


_STAGED, _STAGE_LOCK = {}, threading.Lock()  # cwd -> [calls inside, folders made]: parallel calls in one folder share one copy


@contextlib.contextmanager
def skills(cwd, root=None):
    """Bundled skills visible to one model call: copies into cwd/.claude/skills and cwd/.agents/skills, removed when the last
    call in that folder leaves. Only what the lab created is removed; a folder that already exists (the user's own) is left alone."""
    key = str(Path(cwd).resolve())
    with _STAGE_LOCK:
        if key not in _STAGED:
            made = []
            for home in SKILL_HOMES.values():
                for name, src in skill_dirs(root).items():
                    dst = Path(cwd) / home / name
                    top = next((p for p in (Path(cwd) / home.split("/")[0], Path(cwd) / home, dst) if not p.exists()), None)
                    if top is not None:
                        shutil.copytree(src, dst, dirs_exist_ok=True)
                        made.append(top)
            _STAGED[key] = [0, made]
        _STAGED[key][0] += 1
    try:
        yield
    finally:
        with _STAGE_LOCK:
            _STAGED[key][0] -= 1
            if not _STAGED[key][0]:
                for p in _STAGED.pop(key)[1]:
                    shutil.rmtree(p, ignore_errors=True)


_VERSIONS = {}


def _cli_version(provider):
    if provider not in _VERSIONS:
        try:
            r = subprocess.run([shutil.which(provider) or provider, "--version"], capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL)
            _VERSIONS[provider] = r.stdout.strip() or None if not r.returncode else None
        except (OSError, subprocess.SubprocessError):
            _VERSIONS[provider] = None
    return _VERSIONS[provider]


def fingerprint(provider, model=None):
    """Per-call setup record for calls.jsonl: CLI version, effective model/effort, clean flag, and sha256[:12] of the
    user config files that could apply (never their contents). suppressed=true: clean mode keeps them out of the call."""
    clean = bool(harness().get("clean", True))
    m, effort = pinned(provider, model)
    fp = {"clean": clean, "model_eff": m, "effort": effort, "cli": _cli_version(provider) if provider in USER_FILES else None, "user_cfg": {},
          "host_skills_off": len(host_skills()) if clean and provider == "codex" else 0, "mcp": sorted(mcp_servers())}  # a count, never names
    for rel, off in USER_FILES.get(provider, {}).items():
        f = Path.home() / rel
        if f.is_file():
            fp["user_cfg"][rel] = {"sha": hashlib.sha256(f.read_bytes()).hexdigest()[:12], "suppressed": clean and off}
    return fp


def invoke(provider, instruction, cwd, model=None, write=False, timeout=1800, env=None):
    """Call a model CLI and return its final message.
    provider: codex | claude | cmd:<shell command reading the instruction on stdin> (tests, other CLIs).
    write=False keeps the agent read-only; write=True lets it edit files under cwd."""
    cwd = Path(cwd)
    if provider.startswith("cmd:"):
        from lab.rloop import python_cmd  # {python}, and python3 → this interpreter on Windows
        r = subprocess.run(python_cmd(provider[4:]), shell=True, cwd=cwd, input=instruction, capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=timeout, env=env)
        if r.returncode:
            raise RuntimeError(r.stderr.strip() or "command failed")
        return r.stdout
    out = cwd / ".lab_last_message.md"
    if provider == "codex":
        cmd = [shutil.which("codex") or "codex", "exec", "--ephemeral", "--skip-git-repo-check",
               "--sandbox", "workspace-write" if write else "read-only", "--cd", str(cwd),
               "--output-last-message", str(out)] + codex_args(model) + ["-"]
        r = subprocess.run(cmd, input=instruction, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, env=env)
        if r.returncode:
            raise RuntimeError(r.stderr.strip()[-2000:] or "codex failed")
        text = out.read_text(encoding="utf-8", errors="replace")
        out.unlink()
        return text
    if provider == "claude":
        tools = "Read,Glob,Grep,Skill,Bash,Edit,Write" if write else "Read,Glob,Grep,Skill"
        cmd = [shutil.which("claude") or "claude", "--print", "--no-session-persistence",
               "--permission-mode", "acceptEdits" if write else "dontAsk", f"--tools={tools}"]
        with tempfile.TemporaryDirectory(prefix="lab-mcp-") as tmp:
            cmd += (CLEAN_CLAUDE if harness().get("clean", True) else []) + claude_mcp_args(tmp) + (["--model", model] if model else []) + [instruction]
            env = dict(os.environ if env is None else env, MCP_TIMEOUT="90000") if mcp_servers() else env  # attached servers can take >30 s to start (torch import)
            r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, env=env)
        if r.returncode:
            raise RuntimeError(r.stderr.strip()[-2000:] or "claude failed")
        return r.stdout
    raise ValueError(f"unknown provider {provider}")


def last_json(text):
    """The last top-level {...} or [...] value in a model reply."""
    dec, i, found = json.JSONDecoder(), 0, None
    while i < len(text):
        if text[i] in "[{":
            try:
                found, i = dec.raw_decode(text, i)
                continue
            except ValueError:
                pass
        i += 1
    if found is None:
        raise ValueError("no JSON in reply")
    return found


def ledger(d, exp):
    """Write d/LEDGER.md: results.tsv + each trial's evaluator JSON lines + lab events, so writers and
    reviewers see the gate evidence (split, period ratios, bootstrap) and the freeze history, not just scores."""
    exp = Path(exp)
    parts = ["## results.tsv", (exp / "results.tsv").read_text(encoding="utf-8", errors="replace") if (exp / "results.tsv").exists() else "(none)",
             "## evaluator output per trial (runs/<n>.log, JSON lines)"]
    for log in sorted((exp / "runs").glob("*.log"), key=lambda p: int(p.stem)):
        parts += [f"#{log.stem} {ln}" for ln in log.read_text(encoding="utf-8", errors="replace").splitlines() if ln.startswith("{")]
    parts += ["## lab events (events.jsonl)", (d / "events.jsonl").read_text(encoding="utf-8", errors="replace") if (d / "events.jsonl").exists() else ""]
    (d / "LEDGER.md").write_text("\n".join(parts) + "\n", encoding="utf-8", errors="replace")
    return d / "LEDGER.md"


def slots(per_job_gb=1.5, reserve_gb=4.0):
    """How many concurrent jobs the free memory allows right now (at least 1)."""
    m = rloop_mod.mem_info()
    if m is None:
        return 1
    total, level = m
    return max(1, int((total * level / 100 / 2**30 - reserve_gb) // per_job_gb))


def rloop(*args, check=True):
    r = subprocess.run([sys.executable, str(RLOOP), *map(str, args)], capture_output=True, text=True, encoding="utf-8", errors="replace")
    if check and r.returncode:
        raise RuntimeError(r.stderr.strip() or r.stdout.strip())
    return r.stdout + r.stderr


def selftest():
    import tempfile
    from unittest import mock
    with mock.patch.object(rloop_mod, "mem_info", lambda: None):
        assert slots() == 1
    with mock.patch.object(rloop_mod, "mem_info", lambda: (16 * 2**30, 50)):
        assert slots() == 2  # (8 - 4) // 1.5
    with tempfile.TemporaryDirectory() as t:  # parallel calls in one folder: nobody loses the skills mid-call, last one cleans up
        import time
        seen = []
        def one(i):
            with skills(t):
                time.sleep(0.02 * i); seen.append((Path(t) / ".agents/skills/evaluator-design/SKILL.md").exists())
        ths = [threading.Thread(target=one, args=(i,)) for i in range(5)]
        [x.start() for x in ths]; [x.join() for x in ths]
        assert all(seen) and len(seen) == 5 and not (Path(t) / ".agents").exists() and not _STAGED
    with tempfile.TemporaryDirectory() as t:  # non-ASCII survives under a non-UTF-8 default encoding
        d = Path(t)
        event(d, "s", "한글")
        save_cfg(d, {"q": "한글"})
        assert cfg(d)["q"] == "한글" and "한글" in (d / "events.jsonl").read_bytes().decode("utf-8")
    for name, src in skill_dirs(PKG_DEFAULTS).items():  # packaged skills: open SKILL.md frontmatter with name + description
        head, sep, body = src.joinpath("SKILL.md").read_text(encoding="utf-8").removeprefix("---\n").partition("\n---\n")
        meta = dict(ln.split(": ", 1) for ln in head.splitlines() if ": " in ln)
        assert sep and meta.get("name") == name and len(meta.get("description", "")) > 40 and len(body.splitlines()) < 500, name
    print("core selftest ok (skills frontmatter)")
