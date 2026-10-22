"""Start/stop the bundled ai-kanban board (vendor/ai-kanban, no npm dependencies) on 127.0.0.1."""
import os
import secrets
import shutil
import subprocess
import sys
import time
import urllib.request

from lab import core

GUIDE = """Node.js 22.13+ is required for the bundled board (none found on PATH).
  Windows: winget install OpenJS.NodeJS.LTS   (then open a new terminal)
  macOS:   brew install node   (or https://nodejs.org)"""


def _port():
    saved = core.ROOT / "kanban" / "port"  # the port init chose for this workspace
    return int(os.environ.get("LAB_KANBAN_PORT") or (saved.read_text().strip() if saved.is_file() else "8790"))


def _up(port):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=2):
            return True
    except Exception:
        return False


def kanban_up(a=None):
    port, state = _port(), core.ROOT / "kanban"
    url = f"http://127.0.0.1:{port}"
    if _up(port):
        print(f"kanban already running: {url} (left untouched)")
        return url
    node = shutil.which("node")
    if not node:
        print(GUIDE)
        return None
    state.mkdir(exist_ok=True)
    key = core.ROOT / "memory" / "kanban.key"  # local secret: never printed, never packaged
    if not key.exists():
        key.parent.mkdir(exist_ok=True)
        key.write_text(secrets.token_urlsafe(32))
    env = dict(os.environ, HOST="127.0.0.1", PORT=str(port), DB_PATH=str(state / "research.db"), DIRECTOR_KEY=key.read_text().strip(), LAB_PAGES=str(core.ROOT))
    kw = dict(creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS) if sys.platform == "win32" else dict(start_new_session=True)
    with open(state / "server.log", "ab") as log:
        proc = subprocess.Popen([node, "--disable-warning=ExperimentalWarning", "src/index.js"], cwd=core.PKG_DEFAULTS / "vendor" / "ai-kanban",
                                env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log, **kw)
    (state / "pid").write_text(str(proc.pid))
    (state / "port").write_text(str(port))  # lab/kanban.py syncs to the board this workspace started
    for _ in range(30):
        if _up(port):
            from types import SimpleNamespace
            from lab import kanban
            kanban.kanban_sync(SimpleNamespace(url=url))  # creates the LAB project, else the UI opens on "Create your first project"
            print(f"kanban up: {url}  (stop with kanban-down)")
            return url
        time.sleep(0.5)
    print(f"kanban did not answer on {url}; see {state / 'server.log'}")
    return None


def kanban_down(a=None):
    pid = core.ROOT / "kanban" / "pid"
    if not pid.exists():
        print("kanban: no bundled instance recorded")
        return
    n = int(pid.read_text())
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/PID", str(n), "/T", "/F"], capture_output=True)
    else:
        try:
            os.kill(n, 15)
        except ProcessLookupError:
            pass
    pid.unlink()
    pid.with_name("port").unlink(missing_ok=True)
    print(f"kanban stopped (pid {n})")


def selftest():
    import contextlib
    import io
    import tempfile
    from pathlib import Path
    from unittest.mock import patch
    with tempfile.TemporaryDirectory() as t, patch.object(core, "ROOT", Path(t)), patch.dict(os.environ, {"LAB_KANBAN_PORT": "8790"}):
        out = io.StringIO()
        with patch.object(sys.modules[__name__], "_up", return_value=False), patch.object(shutil, "which", return_value=None), \
                patch.object(subprocess, "Popen", side_effect=AssertionError("must not start")), contextlib.redirect_stdout(out):
            assert kanban_up() is None
        assert "winget install OpenJS.NodeJS.LTS" in out.getvalue() and "macOS" in out.getvalue()
        out = io.StringIO()
        with patch.object(sys.modules[__name__], "_up", return_value=True), patch.object(subprocess, "Popen", side_effect=AssertionError("untouched")), \
                contextlib.redirect_stdout(out):
            assert kanban_up() == "http://127.0.0.1:8790" and "already running" in out.getvalue()
        assert not (Path(t) / "memory" / "kanban.key").exists()
    print("kanban_up selftest ok")
