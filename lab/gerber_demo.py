"""`researchlab demo gerber`: real-data demo setup. Copies the shipped KOSPI200 dev snapshot into the workspace and creates run demo-gerber."""
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

from lab import core


def _numpy_python():
    from shutil import which
    for py in dict.fromkeys(filter(None, [sys.executable, which("python3")])):
        if subprocess.run([py, "-c", "import numpy"], capture_output=True).returncode == 0:
            return py


def setup(run=False, example=None):
    """Create the data copy and the run; returns the run dir. Exits with a hint on any refusal."""
    if not core.FOUND:
        sys.exit("demo gerber needs a workspace: run `researchlab init DIR`, cd into it, then `researchlab demo gerber`")
    ex = Path(example) if example else core.PKG_DEFAULTS / "examples" / "gerber"
    d = core.ROOT / "runs" / "demo-gerber"
    if d.exists():
        sys.exit(f"{d} exists; delete it (and data/kr_a200_dev) to start over")
    py = _numpy_python()
    if not py:
        sys.exit("no python with numpy found: python -m pip install numpy")
    man = json.loads((ex / "data" / "MANIFEST.json").read_text(encoding="utf-8"))
    files = {x["path"]: x["sha256"] for x in man["files"]}  # same MANIFEST schema fetch-data verifies
    missing = [f for f in files if not (ex / "data" / f).is_file()]
    if missing:  # distributions without the bundled snapshot (e.g. a public source tree)
        sys.exit(f"demo data not included here: put {', '.join(missing)} (sha256 in MANIFEST.json) into {ex / 'data'}")
    for f, sha in files.items():
        if hashlib.sha256((ex / "data" / f).read_bytes()).hexdigest() != sha:
            sys.exit(f"{f}: sha256 differs from MANIFEST.json; reinstall researchlab")
    dst = core.ROOT / "data" / "kr_a200_dev"
    dst.mkdir(parents=True, exist_ok=True)
    for f in (*files, "MANIFEST.json"):
        shutil.copy2(ex / "data" / f, dst / f)
    d.mkdir(parents=True)
    (d / "brief.md").write_text((ex / "brief.md").read_text(encoding="utf-8").replace("{PYTHON}", py), encoding="utf-8")
    exp = d / "exp"
    exp.mkdir()
    core.save_cfg(d, {"id": d.name, "exp_dir": str(exp), "provider": "codex", "q": "Q1", "guard": True})
    core.event(d, "new", f"demo gerber, data {dst}, python {py}")
    print(f"data: {dst}\nrun: {d}")
    return d
