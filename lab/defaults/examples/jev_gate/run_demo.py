"""Scripted 3-trial run of the README in a temp folder (portable: no sed, no cd)."""
import contextlib, json, shutil, subprocess, sys, tempfile
from pathlib import Path

here, rloop = Path(__file__).resolve().parent, Path(__file__).resolve().parents[3] / "rloop.py"  # lab/rloop.py, repo and site-packages alike
with (contextlib.nullcontext(sys.argv[1]) if len(sys.argv) > 1 else tempfile.TemporaryDirectory()) as t:  # argv[1]: keep the run in that folder
    for f in ("gen.py", "evaluate.py", "policy.json"):
        shutil.copy(here / f, t)
    def sh(*cmd):
        r = subprocess.run([sys.executable, *cmd], cwd=t, capture_output=True, text=True, encoding="utf-8", errors="replace")
        print(r.stdout + r.stderr, end="", flush=True)
        r.returncode and sys.exit(f"{cmd[0]} failed (exit {r.returncode})")
    sh("gen.py")
    sh(str(rloop), "init", ".", "--q", "Q1", "--eval", "{python} evaluate.py --split dev", "--holdout", "{python} evaluate.py --split holdout",
       "--frozen", "evaluate.py", "data.json", "--mutable", "policy.json", "--direction", "min", "--min-delta", "0.01")
    for thr, why in (([1.0] * 5, "baseline: humans review everything"), ([0.8] * 5, "one threshold for all tasks"),
                     ([1.0, 0.9, 0.6, 0.6, 0.6], "per-task thresholds")):
        (Path(t) / "policy.json").write_text(json.dumps({"thr": thr}))
        sh(str(rloop), "run", ".", "--q", "Q1", "-m", why)
    sh(str(rloop), "holdout", ".", "--q", "Q1")
    sh(str(rloop), "status", ".")
