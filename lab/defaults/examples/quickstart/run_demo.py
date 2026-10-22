"""The README quickstart as one portable script (Windows has no sed): prints the dev scores, then the holdout."""
import contextlib, shutil, subprocess, sys, tempfile
from pathlib import Path

here, rloop = Path(__file__).resolve().parent, Path(__file__).resolve().parents[3] / "rloop.py"  # lab/rloop.py, repo and site-packages alike
with (contextlib.nullcontext(sys.argv[1]) if len(sys.argv) > 1 else tempfile.TemporaryDirectory()) as t:  # argv[1]: keep the run in that folder
    for f in ("evaluate.py", "candidate.py"):
        shutil.copy(here / f, t)
    def rl(*args):
        r = subprocess.run([sys.executable, str(rloop), *args], cwd=t, capture_output=True, text=True, encoding="utf-8", errors="replace")
        print(r.stdout + r.stderr, end="", flush=True)
        r.returncode and sys.exit(f"rloop {args[0]} failed (exit {r.returncode}): {rloop}")
    rl("init", ".", "--q", "Q1", "--eval", "{python} evaluate.py --split dev", "--holdout", "{python} evaluate.py --split holdout",
       "--frozen", "evaluate.py", "--mutable", "candidate.py", "--direction", "min", "--min-delta", "0.001")
    k = "0.0"
    for new in ("0.0", "0.3", "0.5"):
        cand = Path(t) / "candidate.py"
        cand.write_text(cand.read_text().replace(f"K = {k}", f"K = {new}"))
        k = new
        rl("run", ".", "--q", "Q1", "-m", f"K={new}")
    rl("revert", ".", "--q", "Q1")
    rl("holdout", ".", "--q", "Q1")
    rl("status", ".")
