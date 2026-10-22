"""Seal the evidence available after search without opening a holdout."""
import hashlib
import json
import platform
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

from lab import core, rloop as rl


def _file(exp, name):
    p = Path(name)
    return p if p.is_absolute() else exp / p


def _digest(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _versions(command, exp):
    """Ask the evaluator's interpreter, without importing anything in this process."""
    try:
        token = shlex.split(command)[0]
        if "/" in token and not Path(token).is_absolute():
            token = str((exp / token).absolute())
        code = ("import importlib, json, platform, sys; "
                "names=('numpy','pandas','scipy','torch'); "
                "v={}; "
                "\nfor n in names:\n try:\n  m=importlib.import_module(n); v[n]=getattr(m,'__version__',None)\n "
                "except Exception: v[n]=None\n"
                "print(json.dumps({'platform':platform.platform(),'sys_version':sys.version,'packages':v}))")
        out = subprocess.check_output([token, "-c", code], cwd=exp, text=True,
                                      stderr=subprocess.PIPE, timeout=30)
        return json.loads(out)
    except Exception as e:
        return {"platform": platform.platform(), "sys_version": sys.version,
                "packages": {}, "error": f"{type(e).__name__}: {e}"}


def _snapshot_files(exp, trial, mutable):
    root = exp / "runs" / str(trial)
    if not root.is_dir():
        raise RuntimeError(f"winner snapshot missing: {root}")
    for name in mutable:
        if not (root / name).is_file():
            raise RuntimeError(f"winner snapshot missing: {root / name}")
    out = []
    for p in sorted(x for x in root.rglob("*") if x.is_file()):
        out.append({"trial": str(trial), "path": p.relative_to(exp).as_posix(), "sha256": _digest(p)})
    return out


def final(a):
    d = core.run_dir(a.run)
    cfg = core.cfg(d)
    exp, q = Path(cfg["exp_dir"]), cfg["q"]
    loop = json.loads((exp / "loop.json").read_text())[q]
    if not (exp / "results.tsv").is_file():
        raise RuntimeError(f"search ledger missing: {exp / 'results.tsv'}")
    frozen = [{"path": str(p), "sha256": _digest(_file(exp, p)) if _file(exp, p).is_file() else None}
              for p in loop["frozen"]]
    aggregate = rl.sha(loop["frozen"], exp) if all(f["sha256"] for f in frozen) else None
    rows = [r for r in rl.rows(exp) if r["q"] == q]
    best = rl.best(exp, q, loop["direction"])
    lock_path = exp / ".rloop" / f"holdout_{q}.json"
    lock = json.loads(lock_path.read_text()) if lock_path.exists() else None
    holdout = [r for r in rows if r["status"] == "holdout"]
    snapshots = _snapshot_files(exp, best['n'], loop['mutable']) if best else []
    runtime = _versions(loop["eval"], exp)
    frozen_sha256 = {x["path"]: x["sha256"] for x in frozen}
    snapshot_sha256 = {x["path"]: x["sha256"] for x in snapshots}
    manifest = {
        "run": d.name, "q": q, "sealed_utc": core.now(),
        "protocol_hash": loop.get("protocol"), "frozen_hash": loop.get("frozen_hash"),
        "frozen_current_hash": aggregate, "tampered": aggregate != loop.get("frozen_hash"),
        "frozen_files": frozen, "winner_snapshot_files": snapshots,
        "frozen_sha256": frozen_sha256, "winner_snapshot_sha256": snapshot_sha256,
        "eval": loop["eval"], "holdout": loop.get("holdout", ""),
        "runtime": runtime, "dev_best": best, "holdout_rows": holdout, "holdout_lock": lock,
    }
    target = d / "final_evaluation.json"
    force = bool(getattr(a, "force", False))
    if target.exists():
        old = json.loads(target.read_text())
        keys = ("frozen_files", "winner_snapshot_files", "protocol_hash", "frozen_hash",
                "frozen_current_hash", "eval", "holdout")
        if any(old.get(k) != manifest.get(k) for k in keys) and not force:
            raise RuntimeError(f"refusing to overwrite changed manifest: {target} (use --force)")
    target.write_text(json.dumps(manifest, indent=1, ensure_ascii=False) + "\n")
    core.event(d, "final", f"sealed {target}; tampered={manifest['tampered']}")
    return target


def selftest():
    py = sys.executable
    with tempfile.TemporaryDirectory() as td:
        root, exp = Path(td), Path(td) / "exp"
        exp.mkdir(); (exp / "fake.py").write_text("import sys; print('fixture')\n")
        (exp / "eval.py").write_text("print('{}')\n")
        (exp / "hold.py").write_text("from pathlib import Path; Path('holdout-ran').write_text('bad')\n")
        (exp / "x.txt").write_text("x\n")
        (exp / "runs" / "1").mkdir(parents=True); (exp / "runs" / "1" / "x.txt").write_text("x\n")
        (exp / "runs" / "2").mkdir(); (exp / "runs" / "2" / "x.txt").write_text("better\n")
        (exp / "runs" / "2" / "extra.txt").write_text("also frozen\n")
        loop = {"Q1": {"eval": f"{py} eval.py", "holdout": f"{py} hold.py",
                        "frozen": ["eval.py"], "mutable": ["x.txt"], "direction": "max",
                        "min_delta": 0, "timeout": 1, "stop_after": 1,
                        "frozen_hash": rl.sha(["eval.py"], exp), "protocol": "proto"}}
        (exp / "loop.json").write_text(json.dumps(loop))
        for n, score in ((1, "1"), (2, "2")):
            rl.append(exp, {"n": n, "utc": "now", "q": "Q1", "protocol": "proto",
                            "procedure": "v0", "code": "code", "score": score,
                            "status": "keep", "seconds": "0", "desc": "trial"})
        d = root / "runs" / "toy"; d.mkdir(parents=True)
        (d / "run.json").write_text(json.dumps({"id": "toy", "exp_dir": str(exp),
                                                 "provider": "cmd:python3 fake.py", "q": "Q1"}))
        class A: run = str(d); force = False
        p = final(A()); m = json.loads(p.read_text())
        assert m["frozen_files"][0]["sha256"] == _digest(exp / "eval.py") and not m["tampered"]
        assert m["dev_best"]["n"] == "2" and len(m["winner_snapshot_files"]) == 2
        assert m["winner_snapshot_sha256"]["runs/2/extra.txt"] == _digest(exp / "runs/2/extra.txt")
        assert not (exp / "holdout-ran").exists()
        (exp / "runs" / "2" / "extra.txt").write_text("tampered\n")
        try:
            final(A())
        except RuntimeError:
            pass
        else:
            raise AssertionError("changed snapshot was overwritten")
        A.force = True; m = json.loads(final(A()).read_text())
        assert m["winner_snapshot_sha256"]["runs/2/extra.txt"] == _digest(exp / "runs/2/extra.txt")
        (exp / "eval.py").write_text("# tampered\n")
        A.force = False
        try:
            final(A())
        except RuntimeError:
            pass
        else:
            raise AssertionError("changed frozen manifest was overwritten")
        A.force = True; m = json.loads(final(A()).read_text())
        assert m["tampered"] and m["holdout_rows"] == []
    print("final selftest ok")


if __name__ == "__main__":
    selftest()
