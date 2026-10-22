#!/usr/bin/env python3
"""rloop: small research loop. Frozen evaluator, one TSV row per trial, keep/discard, holdout once.

  rloop.py init    DIR --q Q1 --eval CMD --frozen F.. --mutable F.. [--holdout CMD]
                   [--direction max|min] [--min-delta 0] [--timeout 600] [--stop-after 5]
  rloop.py run     DIR --q Q1 -m "what changed" [--procedure v0]
  rloop.py holdout DIR --q Q1        one opening: holdout score of the best kept code and the baseline
  rloop.py revert  DIR --q Q1        restore mutable files of the best kept trial

Each trial snapshots its mutable files to DIR/runs/<n>/, so discarded ideas stay readable.
  rloop.py status  DIR
  rloop.py decide  DIR --q Q1 --verdict V -m "why"
  rloop.py selftest

The evaluator prints one JSON line last on stdout: {"score": float, "pass": bool, ...}.
"""
import argparse
import contextlib
import datetime as dt
import hashlib
import json
import re
import os
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import types
from pathlib import Path

try:
    import fcntl, resource  # absent on Windows
except ImportError:
    fcntl = resource = None

COLS = ["n", "utc", "q", "protocol", "procedure", "code", "score", "status", "seconds", "desc"]
VERDICTS = ("invalid", "no_improvement", "inconclusive", "supported_within_scope")


def sha(paths, root):
    h = hashlib.sha256()
    for p in sorted(paths):
        h.update(p.encode() + b"\0" + (root / p).read_bytes())
    return h.hexdigest()[:12]


def load(d):
    return json.loads((d / "loop.json").read_text(encoding="utf-8", errors="replace"))


def rows(d):
    f = d / "results.tsv"
    if not f.exists():
        return []
    lines = f.read_text(encoding="utf-8", errors="replace").splitlines()[1:]
    return [dict(zip(COLS, ln.split("\t"))) for ln in lines if ln]


def append(d, row):
    f = d / "results.tsv"
    if not f.exists():
        f.write_text("\t".join(COLS) + "\n", encoding="utf-8", errors="replace", newline="\n")
    with f.open("a", encoding="utf-8", errors="replace", newline="\n") as fh:
        fh.write("\t".join(str(row[c]).replace("\t", " ").replace("\n", " ") for c in COLS) + "\n")


def _drop_pyc(d):
    for p in Path(d).glob("__pycache__/*.pyc"):
        p.unlink(missing_ok=True)


THREADS = ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS")


def mem_info():
    """(total bytes, free %) from sysctl (macOS), /proc/meminfo (Linux) or GlobalMemoryStatusEx (Windows); None if unknown."""
    try:
        if sys.platform == "darwin":
            sysctl = lambda k: int(subprocess.run(["sysctl", "-n", k], capture_output=True, text=True, encoding="utf-8", errors="replace").stdout)
            return sysctl("hw.memsize"), sysctl("kern.memorystatus_level")
        if sys.platform == "win32":
            import ctypes

            class MS(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong), ("ullTotalPhys", ctypes.c_ulonglong),
                            ("ullAvailPhys", ctypes.c_ulonglong)] + [(f"x{i}", ctypes.c_ulonglong) for i in range(5)]
            ms = MS(dwLength=ctypes.sizeof(MS))
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(ms))
            return ms.ullTotalPhys, 100 - ms.dwMemoryLoad
        kb = {k: int(v.split()[0]) for k, v in (ln.split(":") for ln in Path("/proc/meminfo").read_text(encoding="utf-8", errors="replace").splitlines())}
        return kb["MemTotal"] * 1024, 100 * kb["MemAvailable"] // kb["MemTotal"]
    except (ValueError, OSError, KeyError, AttributeError, ZeroDivisionError):
        return None


def mem_free_pct():
    m = mem_info()
    return 100 if m is None else m[1]


def kill_tree(p):
    if sys.platform == "win32":  # shell=True: kill the shell and its children
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)], capture_output=True)
    else:
        os.killpg(p.pid, signal.SIGKILL)


def python_cmd(cmd):
    """Let evaluator commands say {python} instead of python3 (absent on Windows)."""
    exe = subprocess.list2cmdline([sys.executable]) if sys.platform == "win32" else shlex.quote(sys.executable)
    cmd = cmd.replace("{python}", exe)
    if os.name == "nt":  # on Windows `python3` is the Microsoft Store stub: use the running interpreter
        cmd = re.sub(r"^\s*python3?(?=\s|$)", lambda m: exe, cmd)
    return cmd


def execute(d, cmd, timeout, log, root=None):
    """Run cmd in its own low-priority process group with 1 BLAS thread and a memory guard;
    return (score, seconds, status). Peak RSS and CPU time (null without `resource`) go to resources.jsonl."""
    t0, min_free = dt.datetime.now(), int(os.environ.get("RLOOP_MIN_FREE", "10"))
    env = os.environ | {k: os.environ.get("RLOOP_THREADS", "1") for k in THREADS} | {"PYTHONDONTWRITEBYTECODE": "1", "PYTHONUTF8": "1"}
    mode = os.environ.get("RLOOP_GUARD")  # python evaluator children may only read d (and root), the interpreter and site-packages; "1"/"0" force on/off
    guard = mode == "1" or (mode != "0" and bool(root))
    if guard:
        try:
            from lab import isolate
        except ImportError:  # run as a script
            import isolate
        cmd, genv = isolate.python_cmd(cmd, d, [root] if root else ())
        env = env | genv
    _drop_pyc(d)  # a same-size edit within one second would otherwise reuse stale bytecode and repeat the old score
    killed = []
    spawn = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if sys.platform == "win32"
             else {"start_new_session": True, "preexec_fn": lambda: os.nice(10)})
    with open(log, "w", encoding="utf-8", errors="replace") as fh:
        p = subprocess.Popen(python_cmd(cmd), shell=True, cwd=d, stdout=subprocess.PIPE, stderr=fh, text=True, encoding="utf-8", errors="replace",
                             env=env, **spawn)

        def guard():  # jetsam kills the whole session first otherwise (2026-10-14 incident)
            while p.poll() is None:
                if mem_free_pct() < min_free:
                    killed.append(f"memory guard: free {mem_free_pct()}% < {min_free}%")
                    kill_tree(p)
                    return
                time.sleep(1)
        threading.Thread(target=guard, daemon=True).start()
        try:
            out, _ = p.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            kill_tree(p)
            out, _ = p.communicate()
            killed.append(f"timeout {timeout}s")
        fh.write(out or "")
        if killed:
            fh.write(f"\n# killed: {killed[0]}\n")
    denied = Path(d) / ".denied.jsonl"
    if guard and denied.exists():  # no run folder here: keep the refusals next to the trial
        with (Path(d) / "denied.jsonl").open("a", encoding="utf-8", errors="replace") as df:
            df.write(denied.read_text(encoding="utf-8", errors="replace"))
        denied.unlink()
    secs = (dt.datetime.now() - t0).total_seconds()
    ru = resource and resource.getrusage(resource.RUSAGE_CHILDREN)  # one child per rloop process, so max = this trial
    with (Path(d) / "resources.jsonl").open("a", encoding="utf-8", errors="replace") as rf:  # ru_maxrss: bytes on macOS, KiB on Linux
        rf.write(json.dumps({"log": Path(log).name, "seconds": round(secs, 1),
                             "peak_rss_mb": ru and round(ru.ru_maxrss / (2**20 if sys.platform == "darwin" else 1024), 1),
                             "cpu_s": ru and round(ru.ru_utime + ru.ru_stime, 1), "mem_guard": mem_info() is not None,
                             "killed": killed[0] if killed else None}) + "\n")
    if killed:
        return None, secs, "crash"
    last = [ln for ln in out.splitlines() if ln.startswith("{")]
    if p.returncode or not last:
        return None, secs, "crash"
    try:
        res = json.loads(last[-1])
        score = float(res["score"])
    except (ValueError, KeyError, TypeError):
        return None, secs, "crash"
    return score, secs, ("invalid" if res.get("pass") is False else "ok")


def best(d, q, direction):
    kept = [r for r in rows(d) if r["q"] == q and r["status"] == "keep"]
    if not kept:
        return None
    return (max if direction == "max" else min)(kept, key=lambda r: float(r["score"]))


def copy_trial(d, cfg, n):
    for p in cfg["mutable"]:
        (d / "runs" / n / p).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(d / p, d / "runs" / n / p)


def restore(d, cfg, n):
    for p in cfg["mutable"]:
        shutil.copy2(d / "runs" / n / p, d / p)


def now():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@contextlib.contextmanager
def locked(d):
    """Serialize run/holdout per loop dir: trial numbers and snapshots must not collide."""
    (d / ".rloop").mkdir(exist_ok=True)
    with open(d / ".rloop" / "lock", "w") as fh:
        if fcntl:
            fcntl.flock(fh, fcntl.LOCK_EX)
        else:
            import msvcrt
            while True:  # LK_LOCK gives up after ~10 s
                try:
                    msvcrt.locking(fh.fileno(), msvcrt.LK_LOCK, 1)
                    break
                except OSError:
                    pass
        yield


def check_frozen(d, cfg):
    if sha(cfg["frozen"], d) != cfg["frozen_hash"]:
        sys.exit(f"frozen files changed ({', '.join(cfg['frozen'])}): this is a new protocol, init a new --q")


def cmd_init(a):
    d = Path(a.dir)
    f = d / "loop.json"
    allcfg = json.loads(f.read_text(encoding="utf-8", errors="replace")) if f.exists() else {}
    if a.q in allcfg:
        sys.exit(f"{a.q} already exists; a changed protocol gets a new question id")
    cfg = {k: getattr(a, k) for k in ("eval", "holdout", "frozen", "mutable", "direction", "min_delta",
                                      "timeout", "stop_after")}
    cfg["frozen_hash"] = sha(a.frozen, d)
    spec = json.dumps({k: v for k, v in cfg.items() if k != "mutable"}, sort_keys=True)  # guard_root is added after: it does not change the protocol
    if a.guard_root:
        cfg["guard_root"] = str(Path(a.guard_root).resolve())
    cfg["protocol"] = hashlib.sha256(spec.encode()).hexdigest()[:12]
    allcfg[a.q] = cfg
    f.write_text(json.dumps(allcfg, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", errors="replace")
    print(f"{a.q} protocol {cfg['protocol']}")


def cmd_run(a):
    d = Path(a.dir)
    with locked(d):
        _run(d, a)


def _run(d, a):
    cfg = load(d)[a.q]
    check_frozen(d, cfg)
    n = len(rows(d)) + 1
    copy_trial(d, cfg, str(n))
    code = sha(cfg["mutable"], d)
    score, secs, st = execute(d, cfg["eval"], cfg["timeout"], d / "runs" / f"{n}.log", cfg.get("guard_root"))
    b = best(d, a.q, cfg["direction"])
    if st == "ok":
        sign = 1 if cfg["direction"] == "max" else -1
        st = "keep" if b is None or sign * (score - float(b["score"])) > cfg["min_delta"] else "discard"
    append(d, dict(n=n, utc=now(), q=a.q, protocol=cfg["protocol"], procedure=a.procedure,
                   code=code,
                   score="" if score is None else f"{score:.6g}", status=st, seconds=f"{secs:.1f}", desc=a.m))
    ref = "" if b is None else f" (best {b['score']} #{b['n']})"
    print(f"#{n} {a.q} {st} score={score}{ref}  log runs/{n}.log")
    tail = [r["status"] for r in rows(d) if r["q"] == a.q][::-1]
    streak = next((i for i, s in enumerate(tail) if s == "keep"), len(tail))
    if tail[:3] == ["crash"] * 3:
        print("STOP: 3 crashes in a row. Fix the setup or revert before another trial.")
    elif streak >= cfg["stop_after"]:
        print(f"STOP: {streak} trials without a keep. Close with `decide` or change the question.")


def cmd_holdout(a):
    d = Path(a.dir)
    with locked(d):
        _holdout(d, a)


def _holdout(d, a):
    cfg = load(d)[a.q]
    check_frozen(d, cfg)
    lock = d / ".rloop" / f"holdout_{a.q}.json"
    if not cfg["holdout"]:
        sys.exit(f"{a.q} has no holdout command")
    if lock.exists():
        sys.exit(f"holdout for {a.q} already opened: {lock.read_text(encoding='utf-8').strip()}")
    b = best(d, a.q, cfg["direction"])
    if b is None or sha(cfg["mutable"], d) != b["code"]:
        sys.exit("holdout evaluates the best kept code only: run `revert` first")
    base = next(r for r in rows(d) if r["q"] == a.q and r["status"] == "keep")
    missing = [f"runs/{r['n']}/{p}" for r in (b, base) for p in cfg["mutable"] if not (d / "runs" / r["n"] / p).exists()]
    if missing:
        sys.exit(f"snapshot missing, holdout not opened: {', '.join(missing)}")
    lock.parent.mkdir(exist_ok=True)
    lock.write_text(json.dumps({"protocol": cfg["protocol"], "best": b["n"], "base": base["n"], "utc": now()}) + "\n", encoding="utf-8", errors="replace")
    for r in ([b] if base["n"] == b["n"] else [b, base]):
        restore(d, cfg, r["n"])
        n = len(rows(d)) + 1
        score, secs, st = execute(d, cfg["holdout"], cfg["timeout"], d / "runs" / f"{n}.log", cfg.get("guard_root"))
        append(d, dict(n=n, utc=now(), q=a.q, protocol=cfg["protocol"], procedure=r["procedure"],
                       code=r["code"], score="" if score is None else f"{score:.6g}",
                       status="holdout" if st == "ok" else st, seconds=f"{secs:.1f}",
                       desc=f"holdout of #{r['n']} (dev {r['score']}): {r['desc']}"))
        print(f"#{n} {a.q} holdout of #{r['n']} score={score} (dev {r['score']})")
    restore(d, cfg, b["n"])


def cmd_revert(a):
    d = Path(a.dir)
    cfg = load(d)[a.q]
    b = best(d, a.q, cfg["direction"])
    if b is None:
        sys.exit("no kept trial yet")
    restore(d, cfg, b["n"])
    print(f"restored {', '.join(cfg['mutable'])} from #{b['n']} (best of {a.q})")


def cmd_status(a):
    d = Path(a.dir)
    rs = rows(d)
    for q, cfg in load(d).items():
        b = best(d, q, cfg["direction"])
        mine = [r for r in rs if r["q"] == q]
        lock = d / ".rloop" / f"holdout_{q}.json"
        print(f"{q} protocol {cfg['protocol']}: {len(mine)} trials, best "
              f"{'-' if b is None else b['score'] + ' #' + b['n'] + ' ' + b['desc']}"
              f"{'  [holdout opened]' if lock.exists() else ''}")
        for r in mine[-5:]:
            print(f"  #{r['n']} {r['status']:8} {r['score']:>10} {r['desc'][:90]}")
        trials = [r for r in mine if r["status"] != "holdout"][1:]  # first trial is the baseline
        for proc in dict.fromkeys(r["procedure"] for r in trials):
            ps = [r["status"] for r in trials if r["procedure"] == proc]
            print(f"  procedure {proc}: {len(ps)} trials, {ps.count('keep')} keep, {ps.count('crash')} crash")
    dec = d / "DECISIONS.md"
    if dec.exists():
        print("decisions:", *dec.read_text(encoding="utf-8", errors="replace").splitlines()[-3:], sep="\n  ")


def cmd_decide(a):
    d = Path(a.dir)
    load(d)[a.q]
    if a.verdict not in VERDICTS:
        sys.exit(f"verdict must be one of {VERDICTS}")
    with (d / "DECISIONS.md").open("a") as fh:
        fh.write(f"- {dt.date.today()} {a.q} **{a.verdict}**: {a.m}\n")
    print(f"{a.q} {a.verdict}")


def selftest_windows():
    """Windows branches, simulated on any host."""
    from unittest import mock
    with tempfile.TemporaryDirectory() as t:
        d = Path(t)
        calls = []

        def locking(fd, mode, n):
            calls.append(mode)
            if len(calls) == 1:
                raise OSError("busy")
        ms = types.SimpleNamespace(LK_LOCK=1, locking=locking)
        with mock.patch.object(sys, "platform", "win32"), mock.patch.dict(sys.modules, {"msvcrt": ms}), mock.patch(__name__ + ".fcntl", None):
            with locked(d):  # first locking attempt fails (busy), second succeeds
                pass
            assert calls == [1, 1], calls
            assert python_cmd("{python} a.py") == subprocess.list2cmdline([sys.executable]) + " a.py"
            killed = []
            with mock.patch.object(subprocess, "run", lambda c, **k: killed.append(c)):
                kill_tree(types.SimpleNamespace(pid=7))
            assert killed == [["taskkill", "/F", "/T", "/PID", "7"]], killed
            seen = {}
            real = subprocess.Popen

            def fake(cmd, **k):
                seen.update(k)
                return real(cmd, **{x: v for x, v in k.items() if x not in ("creationflags", "start_new_session", "preexec_fn")})
            with mock.patch.object(subprocess, "Popen", fake), mock.patch.object(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200, create=True), \
                    mock.patch(__name__ + ".resource", None), mock.patch(__name__ + ".mem_info", lambda: None):
                (d / "ev.py").write_text("print('{\"score\": 2}')\n", encoding="utf-8", errors="replace")
                r = execute(t, "{python} ev.py", 60, d / "w.log")
            assert seen["creationflags"] == 0x200 and "preexec_fn" not in seen and "start_new_session" not in seen, seen
            assert r[0] == 2.0 and r[2] == "ok", r
            res = json.loads((d / "resources.jsonl").read_text(encoding="utf-8", errors="replace"))
            assert res["peak_rss_mb"] is None and res["cpu_s"] is None and res["mem_guard"] is False, res
    with mock.patch.object(sys, "platform", "linux"), mock.patch.object(Path, "read_text", lambda *a, **k: "MemTotal: 1000 kB\nMemAvailable: 250 kB\n"):
        assert mem_info() == (1024000, 25), mem_info()
    with mock.patch.object(sys, "platform", "linux"), mock.patch.object(Path, "read_text", lambda *a, **k: ""):
        assert mem_info() is None and mem_free_pct() == 100


def selftest():
    py = sys.executable
    me = str(Path(__file__).resolve())
    selftest_windows()
    with tempfile.TemporaryDirectory() as t:  # {python} placeholder; plain commands keep working
        (Path(t) / "ev.py").write_text("import json; print(json.dumps({'score': 7}))\n", encoding="utf-8", errors="replace")
        assert execute(t, "{python} ev.py", 60, Path(t) / "p.log")[0] == 7.0
    with tempfile.TemporaryDirectory() as t, tempfile.TemporaryDirectory() as out:  # RLOOP_GUARD=1: reads outside the folder fail
        from unittest import mock
        (Path(out) / "x").write_text("x")
        (Path(t) / "ev2.py").write_text(f"import json; open({str(Path(out) / 'x')!r}).read(); print(json.dumps({{'score': 1}}))\n", encoding="utf-8", errors="replace")
        assert execute(t, "{python} ev2.py", 60, Path(t) / "g0.log")[0] == 1.0  # default off
        with mock.patch.dict(os.environ, RLOOP_GUARD="1"):
            assert execute(t, "{python} ev2.py", 60, Path(t) / "g1.log")[0] is None and (Path(t) / "denied.jsonl").exists()
        with tempfile.TemporaryDirectory() as w:  # guard root = workspace: its data/ is readable, files outside are not; env unset follows the run flag
            (Path(w) / "data").mkdir(); (Path(w) / "data" / "y").write_text("y")
            (Path(t) / "ev3.py").write_text(f"import json; open({str(Path(w) / 'data' / 'y')!r}).read(); print(json.dumps({{'score': 3}}))\n", encoding="utf-8", errors="replace")
            assert execute(t, "{python} ev3.py", 60, Path(t) / "g2.log", w)[0] == 3.0
            assert execute(t, "{python} ev2.py", 60, Path(t) / "g3.log", w)[0] is None
            assert execute(t, "{python} ev2.py", 60, Path(t) / "g4.log", None)[0] == 1.0  # old run: no root, off
            with mock.patch.dict(os.environ, RLOOP_GUARD="0"):
                assert execute(t, "{python} ev2.py", 60, Path(t) / "g5.log", w)[0] == 1.0
    with tempfile.TemporaryDirectory() as t:  # same-size edit within one second must not reuse stale bytecode
        (Path(t) / "cand.py").write_text("K = 0.3\n", encoding="utf-8", errors="replace")
        (Path(t) / "ev.py").write_text("import json; from cand import K; print(json.dumps({'score': K}))\n", encoding="utf-8", errors="replace")
        first = execute(t, f"{py} ev.py", 60, Path(t) / "a.log")[0]
        (Path(t) / "cand.py").write_text("K = 0.5\n", encoding="utf-8", errors="replace")
        assert (first, execute(t, f"{py} ev.py", 60, Path(t) / "b.log")[0]) == (0.3, 0.5), "stale bytecode"
    with tempfile.TemporaryDirectory() as t:
        d = Path(t)
        (d / "eval.py").write_text("import json;x=float(open('x.txt').read());print(json.dumps({'score':-(x-3)**2,'pass':x>=0}))\n", encoding="utf-8", errors="replace")
        (d / "hold.py").write_text("import json;x=float(open('x.txt').read());print(json.dumps({'score':-(x-2.5)**2}))\n", encoding="utf-8", errors="replace")

        def rl(*args, ok=True):
            r = subprocess.run([py, me, *args], capture_output=True, text=True, encoding="utf-8", errors="replace")
            assert (r.returncode == 0) == ok, (args, r.stdout, r.stderr)
            return r.stdout + r.stderr

        def trial(x, msg):
            (d / "x.txt").write_text(str(x), encoding="utf-8", errors="replace")
            return rl("run", t, "--q", "Q1", "-m", msg)

        rl("init", t, "--q", "Q1", "--eval", f"{py} eval.py", "--holdout", f"{py} hold.py",
           "--frozen", "eval.py", "hold.py", "--mutable", "x.txt", "--stop-after", "2")
        rl("init", t, "--q", "Q1", "--eval", "x", "--frozen", "eval.py", "--mutable", "x.txt", ok=False)
        assert "keep" in trial(0, "baseline")
        assert "invalid" in trial(-1, "evaluator says pass=false")
        assert "keep" in trial(2, "better")
        assert "discard" in trial(10, "worse")
        (d / "x.txt").write_text("not a number", encoding="utf-8", errors="replace")
        out = rl("run", t, "--q", "Q1", "-m", "broken")
        assert "crash" in out and "STOP: 2 trials" in out, out
        rl("holdout", t, "--q", "Q1", ok=False)            # code is not the best
        rl("revert", t, "--q", "Q1")
        (d / "runs" / "1" / "x.txt").rename(d / "x1.bak")
        assert "snapshot missing" in rl("holdout", t, "--q", "Q1", ok=False)
        assert not (d / ".rloop" / "holdout_Q1.json").exists()
        (d / "x1.bak").rename(d / "runs" / "1" / "x.txt")
        assert (d / "x.txt").read_text(encoding="utf-8", errors="replace") == "2"
        out = rl("holdout", t, "--q", "Q1")
        assert "holdout of #3 score=-0.25" in out and "holdout of #1 score=-6.25" in out, out
        assert (d / "x.txt").read_text(encoding="utf-8", errors="replace") == "2" and (d / "runs" / "4" / "x.txt").read_text(encoding="utf-8", errors="replace") == "10"
        rl("holdout", t, "--q", "Q1", ok=False)            # second opening refused
        (d / "eval.py").write_text((d / "eval.py").read_text(encoding="utf-8", errors="replace") + "# tweak\n", encoding="utf-8", errors="replace")
        rl("run", t, "--q", "Q1", "-m", "after tamper", ok=False)
        rl("decide", t, "--q", "Q1", "--verdict", "great", "-m", "x", ok=False)
        res = [json.loads(x) for x in (d / "resources.jsonl").read_text(encoding="utf-8", errors="replace").splitlines()]
        assert res and all(r["peak_rss_mb"] is None or r["peak_rss_mb"] >= 0 for r in res), res
        (d / "thr.py").write_text("import os,json;print(json.dumps({'score':float(os.environ['OMP_NUM_THREADS'])}))\n", encoding="utf-8", errors="replace")
        rl("init", t, "--q", "T", "--eval", f"{py} thr.py", "--frozen", "thr.py", "--mutable", "x.txt")
        assert "score=1.0" in rl("run", t, "--q", "T", "-m", "threads pinned to 1")
        (d / "eval.py").write_text("import json,time;time.sleep(1);print(json.dumps({'score':0}))\n", encoding="utf-8", errors="replace")
        rl("init", t, "--q", "P", "--eval", f"{py} eval.py", "--frozen", "eval.py", "--mutable", "x.txt")
        ps = [subprocess.Popen([py, me, "run", t, "--q", "P", "-m", "parallel"], stdout=subprocess.DEVNULL) for _ in range(2)]
        assert all(p.wait() == 0 for p in ps)
        ns = [r["n"] for r in rows(d)]
        assert len(ns) == len(set(ns)), ns
        rl("decide", t, "--q", "Q1", "--verdict", "supported_within_scope", "-m", "x=2 beats baseline")
        st = rl("status", t)
        assert "best -1 #3" in st and "[holdout opened]" in st and "v0: 4 trials, 1 keep, 1 crash" in st, st
        assert [r["status"] for r in rows(d) if r["q"] == "Q1"] == ["keep", "invalid", "keep", "discard", "crash", "holdout", "holdout"]
    print("selftest ok")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("init")
    s.add_argument("dir"); s.add_argument("--q", required=True); s.add_argument("--eval", required=True)
    s.add_argument("--holdout", default=""); s.add_argument("--frozen", nargs="+", required=True)
    s.add_argument("--mutable", nargs="+", required=True)
    s.add_argument("--direction", choices=("max", "min"), default="max")
    s.add_argument("--min-delta", type=float, default=0.0); s.add_argument("--timeout", type=int, default=600)
    s.add_argument("--stop-after", type=int, default=5); s.add_argument("--guard-root", default="")
    s = sub.add_parser("run")
    s.add_argument("dir"); s.add_argument("--q", required=True); s.add_argument("-m", required=True)
    s.add_argument("--procedure", default=os.environ.get("RLOOP_PROCEDURE", "v0"))
    for name in ("holdout", "revert"):
        s = sub.add_parser(name); s.add_argument("dir"); s.add_argument("--q", required=True)
    s = sub.add_parser("status"); s.add_argument("dir")
    s = sub.add_parser("decide")
    s.add_argument("dir"); s.add_argument("--q", required=True); s.add_argument("--verdict", required=True)
    s.add_argument("-m", required=True)
    sub.add_parser("selftest")
    a = ap.parse_args()
    if a.cmd == "selftest":
        return selftest()
    globals()[f"cmd_{a.cmd}"](a)


if __name__ == "__main__":
    main()
