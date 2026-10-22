"""`researchlab attach`: plug a paper2agent bundle (skills + an MCP server) into the workspace.
agents/<name>/ holds the unpacked bundle; skills are copied to skills/<skill>; the MCP server is recorded under "mcp" in harness.json."""
import contextlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

from lab import core

NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*$")  # also a bare TOML key for codex -c mcp_servers.<name>


def _unzip(zpath, dest):
    """Extract with UTF-8 names (cp437 fallback for zips without the UTF-8 flag); refuses paths that leave dest."""
    dest = Path(dest).resolve()
    with zipfile.ZipFile(zpath) as z:
        for i in z.infolist():
            name = i.filename
            if not i.flag_bits & 0x800:
                try:
                    name = name.encode("cp437").decode("utf-8")
                except (UnicodeEncodeError, UnicodeDecodeError):
                    pass
            t = (dest / name).resolve()
            if dest != t and dest not in t.parents:
                raise ValueError(f"unsafe path in zip: {i.filename}")
            if name.endswith("/"):
                t.mkdir(parents=True, exist_ok=True)
                continue
            t.parent.mkdir(parents=True, exist_ok=True)
            with z.open(i) as src, t.open("wb") as out:
                shutil.copyfileobj(src, out)
            if (i.external_attr >> 16) & 0o111:
                t.chmod(t.stat().st_mode | 0o111)


def _unpack_inner(d):
    for z in sorted(Path(d).rglob("*.zip")):
        _unzip(z, z.parent)


def venv_python(venv):
    return Path(venv) / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _rel(p):
    return Path(p).resolve().relative_to(core.ROOT.resolve()).as_posix()


def _servers(d):
    """(package dir, server script) for each src/requirements.txt next to a python file that starts an MCP server."""
    for req in sorted(Path(d).rglob("src/requirements.txt")):
        for py in sorted(req.parent.glob("*.py")):
            if "mcp.run(" in py.read_text(encoding="utf-8", errors="replace"):
                yield req.parent.parent, py
                break


def attach(bundle, name=None, install=False, python=None):
    root = core.ROOT
    src = Path(bundle).expanduser().resolve()
    if not src.exists():
        sys.exit(f"no such bundle: {src}")
    with tempfile.TemporaryDirectory(prefix="lab-attach-") as t:
        tmp = Path(t) / "b"
        if src.is_dir():
            shutil.copytree(src, tmp)
        else:
            _unzip(src, tmp)
        _unpack_inner(tmp)
        skills = {f.parent.name: f.parent for f in sorted(tmp.rglob("SKILL.md"))}  # first sorted wins when a bundle repeats a skill
        name = name or next(iter(skills), None) or re.sub(r"\W+", "-", src.stem).strip("-")
        if not NAME_RE.match(name):
            sys.exit(f"bad name {name!r}: letters, digits, - and _ only")
        dest = root / "agents" / name
        if dest.exists():
            sys.exit(f"{dest} exists: pick another --name or delete it")
        # the bundle folder may be the one wrapper directory inside the zip
        top = [p for p in tmp.iterdir()]
        shutil.copytree(top[0] if len(top) == 1 and top[0].is_dir() else tmp, dest)
    for sname in skills:
        s = next(f.parent for f in sorted(dest.rglob("SKILL.md")) if f.parent.name == sname)
        shutil.copytree(s, root / "skills" / sname, dirs_exist_ok=True)
        print(f"skill   {sname} -> {root / 'skills' / sname}")
    h_path = root / "harness.json"
    h = json.loads(h_path.read_text(encoding="utf-8"))
    todo = []
    mcp, found = h.setdefault("mcp", {}), list(_servers(dest))
    for i, (pkg, script) in enumerate(found):
        key = name if i == 0 else f"{name}-{pkg.name}"
        venv = dest / ".venv"
        mcp[key] = {"command": str(Path(python).expanduser().resolve()) if python else _rel(venv_python(venv)), "args": [_rel(script)], "cwd": _rel(pkg)}  # --python reuses an installed env
        req = pkg / "src" / "requirements.txt"
        cmds = [[shutil.which("python3.11") or sys.executable, "-m", "venv", str(venv)], [str(venv_python(venv)), "-m", "pip", "install", "-r", str(req)]]
        line = " && ".join(" ".join(map(str, c)) for c in cmds)
        print(f"mcp     {key}: {mcp[key]['command']} {mcp[key]['args'][0]}  (cwd {mcp[key]['cwd']})")
        if python:
            pass
        elif install:
            print(f"install (needs network): {line}")
            todo += cmds
        else:
            print(f"install later: researchlab attach ... --install, or run: {line}")
        for s in sorted(pkg.glob("repo/*/src")):
            print(f"python import path for experiments: {_rel(s)}  (inside the workspace, the evaluator guard allows it)")
    h_path.write_text(json.dumps(h, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    (dest / "attach.json").write_text(json.dumps({"skills": sorted(skills), "mcp": [k for k in mcp if k == name or k.startswith(name + "-")]}, indent=1) + "\n", encoding="utf-8")
    for c in todo:  # after the files are recorded, so a failed install can be retried by hand
        subprocess.run(c, check=True)
    print(f"attached {name}: {len(skills)} skill(s), {len(found)} mcp server(s) -> {dest}")


def listing():
    mcp = core.harness().get("mcp", {})
    for f in sorted((core.ROOT / "agents").glob("*/attach.json")):
        m = json.loads(f.read_text(encoding="utf-8"))
        print(f"{f.parent.name:<20} skills: {', '.join(m['skills']) or '-'}  mcp: {', '.join(k + (' (venv missing)' if not (core.ROOT / mcp[k]['command']).exists() else '') for k in m['mcp'] if k in mcp) or '-'}")


def cmd_attach(a):
    if a.list or not a.bundle:
        return listing()
    attach(a.bundle, a.name, a.install, getattr(a, "python", None))


def selftest():
    from unittest import mock
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        b = t / "bundle"  # tiny synthetic bundle: one skill, one fake MCP package inside an inner zip
        (b / "skill/mini-paper").mkdir(parents=True)
        (b / "skill/mini-paper/SKILL.md").write_text("---\nname: mini-paper\ndescription: synthetic skill for the attach selftest only\n---\nbody\n")
        pkg = t / "pk/Mini-mcp"
        (pkg / "src").mkdir(parents=True)
        (pkg / "src/requirements.txt").write_text("fastmcp\n")
        (pkg / "src/Mini_mcp.py").write_text("mcp = None\nmcp.run()\n")
        (pkg / "repo/Mini/src/mini").mkdir(parents=True)
        (b / "mcp").mkdir()
        with zipfile.ZipFile(b / "mcp/Mini-mcp.zip", "w") as z:
            for f in sorted(pkg.rglob("*")):
                z.write(f, "Mini-mcp/" + f.relative_to(pkg).as_posix() + ("/" if f.is_dir() else ""))
        w = t / "w"
        w.mkdir()
        shutil.copy(core.PKG_DEFAULTS / "harness.json", w / "harness.json")
        zb = t / "b.zip"
        with zipfile.ZipFile(zb, "w") as z:
            for f in sorted(b.rglob("*")):
                if f.is_file():
                    z.write(f, "Wrapper/" + f.relative_to(b).as_posix())
        with mock.patch.object(core, "ROOT", w), contextlib.redirect_stdout(io.StringIO()):
            assert core.fingerprint("cmd:x")["mcp"] == []
            attach(zb)
            h = json.loads((w / "harness.json").read_text())
            m = h["mcp"]["mini-paper"]
            assert m["cwd"].endswith("Mini-mcp") and m["args"][0].endswith("src/Mini_mcp.py") and ".venv" in m["command"], m
            assert "mini-paper" in core.skill_dirs(w) and (w / "agents/mini-paper/attach.json").is_file()
            assert core.fingerprint("cmd:x")["mcp"] == []  # registered, not installed: never started
            py = w / m["command"]
            py.parent.mkdir(parents=True, exist_ok=True); py.write_text("")  # stands in for the installed venv
            assert core.fingerprint("cmd:x")["mcp"] == ["mini-paper"]
            cl = core.claude_mcp_args(Path(t))  # claude: one --mcp-config file holding only this server
            assert cl[0].startswith("--mcp-config=") and list(json.loads(Path(cl[0].split("=", 1)[1]).read_text())["mcpServers"]) == ["mini-paper"]
            cx = core.codex_mcp_args()
            assert any(x.startswith("mcp_servers.mini-paper.command=") for x in cx) and any(x.startswith("mcp_servers.mini-paper.args=") for x in cx)
            assert any(x.startswith("mcp_servers.mini-paper.cwd=") for x in cx)
            with mock.patch.object(core, "harness", lambda: {"clean": True}):
                assert core.claude_mcp_args(Path(t)) == [] and core.codex_mcp_args() == [] and core.fingerprint("cmd:x")["mcp"] == []
            try:
                attach(zb)
                raise AssertionError("second attach overwrote")
            except SystemExit:
                pass
    print("attach selftest ok (unpack, skill copy, harness mcp entry, claude/codex flags, fingerprint, empty = no flags)")
