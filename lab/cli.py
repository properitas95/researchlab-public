"""ResearchLab: one research question from brief to reviewed result, in one runs/<id>/ folder.

  lab.py new RUN "brief"            start a run (or --brief-file F); --exp-dir DIR to experiment elsewhere
  lab.py next RUN                   print the one next command (rule-based, no model call)
  lab.py survey RUN                 field map, SOTA cards and field beliefs from alpha search -> field.md
  lab.py read-field RUN              skim the survey papers, deep-read the best -> papers/<id>/, field_papers.md
  lab.py preregister RUN            brief with "## Pre-registered design" -> its one candidate C1, novelty and pick (replaces ideate..pick)
  lab.py ideate RUN                 brief (+ field.md) -> candidates.json, value = impact x uncertainty x soundness
  lab.py screen RUN                 sealed 3-scorer candidate panel; medians, contested/predictable flags
  lab.py novelty RUN [--top 3]      alpha literature search per candidate, refutation query included
  lab.py pick RUN CAND              the user picks a candidate (never automatic)
  lab.py fetch-data RUN             data engineer downloads the public data the pick needs -> data/<name>/ + data_plan.json
  lab.py plan RUN                   model drafts PROTOCOL.md, evaluate.py, mutable file in exp_dir
  lab.py critic RUN                 adversarial design review of PROTOCOL.md + evaluator before freeze
  lab.py replan RUN                 planner fixes what the critique found, critic re-runs
  lab.py autofreeze RUN             freeze from the files when the critique is clean (no human); recorded as auto
  lab.py freeze RUN --eval .. --holdout .. --frozen .. --mutable .. [--min-delta X] [--anchor V --anchor-tol T]
                                    user-approved rloop init; refused without a fresh blocker-free critique
                                    (--override "reason" is recorded); the baseline must reproduce the anchor
  lab.py experiment RUN [--iters N] [--wall-min M]   proposer edits -> rloop run -> revert on non-keep
  lab.py explore RUN [--iters N] [--wall-min M] [--drafts 3] [--debug-depth 3] [--seed 0]
                                    tree search instead of the linear loop: drafts, debug, improve top-3
  lab.py analyze RUN                holdout once + rule-based verdict
  lab.py ablate RUN                 remove one component of the winner at a time (dev), outside the keep ledger
  lab.py final RUN [--force]        sealed final-evaluation manifest: hashes, environment, holdout rows
  lab.py posthoc RUN                up to 3 dev-only robustness analyses written and run by the model
  lab.py hypotheses RUN [--force]   sealed competing hypotheses with machine-checkable predictions
  lab.py discriminate RUN           dev-only diagnostics; code decides which hypotheses survive
  lab.py write RUN                  RESULT.md from the ledger, every number tied to a trial
  lab.py review RUN                 a second model checks RESULT.md against the ledger
  lab.py paper RUN                  paper/paper.tex: figures and number macros from the ledger, compiled
  lab.py panel RUN                  blind reviewer panel + meta-review
  lab.py revise RUN [--rounds 2]    revise paper.tex against surviving majors, re-review
  lab.py inspect RUN [--output F]   one offline HTML page for the whole run
  lab.py campaign RUN [--auto-pick] [--auto-freeze] [--replan-rounds 6] [--pivots N] [--max-stages 60] [--wall-min 240] [--max-chars 20000000]
                                    autonomous director: runs every non-human step, self-heals 3x, pivots
  lab.py program-init NAME --question Q --thesis S [--thesis S ..]
  lab.py program-add NAME RUN       map a finished run onto the thesis board
  lab.py program-board NAME
  lab.py program-next NAME          pick the most informative thesis and open a new run with its brief
  lab.py program-run NAME [--cycles 3] [--wall-min 480] [--max-stages 40]
                                    unattended: campaign each run (auto-pick, critic->replan->autofreeze), update
                                    the board, open the next question; ends with programs/NAME/DIGEST.md
  lab.py program-digest NAME        morning digest: board changes, verdicts, automatic decisions, needs-a-human
  lab.py snapshot RUN --name N --dev-end YYYY-MM-DD [--request F] [--force]
                                    data engineer builds data/N (dev.pkl, holdout.pkl, manifest hashes)
  lab.py meta-init | meta-improve [--iters 3] | meta-adopt [--yes]
                                    recursive improvement of harness.json + prompts on replay tasks
  lab.py read --paper ID [--run RUN] [--mode skim|deep] [--thesis S]
                                    alpha full text -> verified reading card in memory/global/papers/
  lab.py paper-status | paper-set ID STAGE --note N --evidence F [--venue V]
  lab.py venue-fit ID [--reviews F ..] | paper-pivot ID --venue V
                                    paper lifecycle board; after a rejection rank other venues and pivot
  lab.py dashboard [--output F]     whole system on one offline page
  lab.py citations                 per-working-paper citation graph from .tex/.bib -> portfolio/citations.json
  lab.py replay [--open]            rebuild replay/index.html (per-stage snapshots; LAB_REPLAY=0 stops recording)
  lab.py kanban-sync                mirror runs onto the research kanban (127.0.0.1:8790, one project per run + LAB)
  lab.py kanban-up | kanban-down    start / stop the bundled kanban board (Node 22.13+)
  lab.py venue NAME [--source ID] [--issn N] [--years 3] [--site URL ..]
                                    collect a venue's recent works (OpenAlex) -> venues/NAME/works.jsonl
  lab.py venue-profile NAME         model-written venue profile (what it accepts, how reviewers judge)
  lab.py methods-scan | methods-review   method ledger → METHODS.md (works / dev-only / fails, recurring findings)
  lab.py kg                         knowledge graph of the papers read -> kg/kg.html, kg/kg.json (board: 지식 그래프)
  lab.py lineage                    lineage.html: run lineage, PERT timeline with critical path, agent-hours
  lab.py combine RUN                untried method × dataset × metric seeds from paper concept graphs (ideate uses them)
  lab.py defects | defect-set ID S  data-defect ledger: recurring critic findings (open → known constraint, confirmed blocks)
  lab.py helix-sync                 one Helix Foundry workspace per paper + an overview graph (localhost:3001)
  lab.py status RUN
  lab.py attach BUNDLE [--name N] [--install] | --list
                                    plug a paper2agent bundle (zip or folder) in: agents/N/, skills/, harness.json "mcp"
  lab.py selftest
  researchlab init [DIR] [--force]  new workspace: harness.json, prompts/, memory/, runs/, programs/
  researchlab demo [quickstart|jev|gerber]  scripted rloop demo from the installed (or repo) examples, no model, no path needed
  researchlab doctor                python, workspace, codex/claude/node/npm, kanban port, UTF-8 (always exits 0)
  researchlab skills [install]      bundled research skills; install copies them into this workspace's .claude/skills
                                    and .agents/skills so direct claude/codex sessions here see them (model calls get them anyway)
"""
import argparse
import json
import locale
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path

from lab import core, director


def cmd_new(a):
    d = core.ROOT / "runs" / a.run
    if (d / "run.json").exists():
        sys.exit(f"{d} exists")
    exp = Path(a.exp_dir).resolve() if a.exp_dir else d / "exp"
    if not a.allow_outside and core.ROOT not in (exp, *exp.parents):
        sys.exit(f"exp-dir {exp} is outside the workspace {core.ROOT}: copy the data into {core.ROOT / 'data'} or pass --allow-outside")
    d.mkdir(parents=True)
    brief = Path(a.brief_file).read_text() if a.brief_file else a.brief
    (d / "brief.md").write_text(brief.strip() + "\n")
    exp.mkdir(parents=True, exist_ok=True)
    core.save_cfg(d, {"id": a.run, "exp_dir": str(exp), "provider": a.provider, "q": a.q, "guard": not a.allow_outside})
    core.event(d, "new", f"run {a.run}, exp_dir {exp}")
    print(director.next_cmd(d))


def cmd_pick(a):
    d = core.run_dir(a.run)
    cands = {c["id"]: c for c in json.loads((d / "candidates.json").read_text())}
    if a.cand not in cands:
        sys.exit(f"unknown candidate; choose from {', '.join(cands)}")
    (d / "pick.json").write_text(json.dumps(cands[a.cand], indent=1, ensure_ascii=False) + "\n")
    core.event(d, "pick", f"user picked {a.cand}: {cands[a.cand]['title']}")


def cmd_freeze(a):
    from lab import critic
    d = core.run_dir(a.run)
    c = core.cfg(d)
    reason = critic.freeze_gate(d)
    if reason and not (a.override or "").strip():
        sys.exit(f"freeze refused: {reason}. Run `lab.py critic {a.run}`, fix, or pass --override \"why\"")
    if reason:
        core.event(d, "freeze_override", reason, override=a.override)
    args = ["init", c["exp_dir"], "--q", c["q"], "--eval", a.eval, "--frozen", *a.frozen,
            "--mutable", *a.mutable, "--min-delta", a.min_delta]
    if c.get("guard"):
        args += ["--guard-root", core.ROOT]
    if a.holdout:
        args += ["--holdout", a.holdout]
    if a.anchor is not None:
        core.save_cfg(d, json.loads((d / "run.json").read_text()) | {"anchor": a.anchor, "anchor_tol": a.anchor_tol})
    core.event(d, "freeze", core.rloop(*args).strip() + (f"; anchor {a.anchor}±{a.anchor_tol}" if a.anchor is not None else ""))


def _listening(port):
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def cmd_init(a):
    d = Path(a.dir).resolve()
    if (d / "harness.json").exists() and not a.force:
        sys.exit(f"{d / 'harness.json'} exists; pass --force to overwrite")
    d.mkdir(parents=True, exist_ok=True)
    shutil.copy2(core.PKG_DEFAULTS / "harness.json", d / "harness.json")
    shutil.copytree(core.PKG_DEFAULTS / "prompts", d / "prompts", dirs_exist_ok=True)
    for sub in ("memory/roles", "memory/global", "runs", "programs"):
        (d / sub).mkdir(parents=True, exist_ok=True)
    for role in json.loads((d / "harness.json").read_text(encoding="utf-8"))["roles"]:
        (d / "memory/roles" / f"{role}.md").touch()
    index = d / "memory/global/INDEX.md"
    if not index.exists() or a.force:
        index.write_text("# Lab memory index\n", encoding="utf-8")
    print(f"workspace ready: {d}\n  cd {d}\n  researchlab demo")
    if os.environ.get("LAB_KANBAN_AUTO", "1") == "1":
        from lab import kanban_up
        port = int(os.environ.get("LAB_KANBAN_PORT", "8790"))
        while _listening(port):  # any listener (another board, another server) is not ours
            port += 1
        os.environ["LAB_KANBAN_PORT"], core.ROOT = str(port), d
        kanban_up.kanban_up()


def cmd_demo(a):
    if a.name == "gerber":  # real data, model-driven: only sets up data + run, then prints (or with --run starts) the campaign
        from lab import gerber_demo
        d = gerber_demo.setup()
        cmd = f"researchlab campaign {d.name} --auto-pick --auto-freeze --wall-min 50"
        print(f"next: {cmd}")
        return main(cmd.split()[1:]) if a.run else None
    script = core.PKG_DEFAULTS / "examples" / {"quickstart": "quickstart", "jev": "jev_gate"}[a.name] / "run_demo.py"
    d = core.ROOT / "runs" / f"demo-{a.name}"
    if core.FOUND:  # inside a workspace the demo stays as a run, so the board and lineage page show it
        shutil.rmtree(d, ignore_errors=True)
        (d / "exp").mkdir(parents=True)
        core.save_cfg(d, {"id": d.name, "exp_dir": str(d / "exp"), "provider": "codex", "q": "Q1"})
        (d / "brief.md").write_text({"quickstart": "# 데모: 고정 평가기로 K 하나 고르기 (모델 없음)\n",
                                     "jev": "# 데모: 과제별 Jev 판정 문턱 고르기 (합성 자료, 모델 없음)\n"}[a.name], encoding="utf-8")
        cand = [{"id": "C1", "title": f"demo {a.name}: candidate fixed by the example", "value": 1}]  # stands in for survey→pick
        for f, v in (("candidates.json", cand), ("pick.json", cand[0]), ("novelty.json", {"demo": True})):
            (d / f).write_text(json.dumps(v, ensure_ascii=False) + "\n", encoding="utf-8")
        core.event(d, "new", f"model-free demo {a.name}")
    r = subprocess.run([sys.executable, str(script), *([str(d / "exp")] if core.FOUND else [])],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    print(r.stdout + r.stderr, end="")
    if r.returncode:
        sys.exit(r.returncode)
    if core.FOUND:
        from lab import kanban
        kanban.refresh()
        print(f"\nboard: {kanban._url()}  (card {d.name})\nlineage: {core.ROOT / 'lineage.html'}")


def cmd_skills(a):
    found = core.skill_dirs()
    for name, src in found.items():
        desc = next((ln.split(": ", 1)[1] for ln in (src / "SKILL.md").read_text(encoding="utf-8").splitlines() if ln.startswith("description: ")), "")
        print(f"{name:<20} {'workspace' if src.parent == core.ROOT / 'skills' else 'package':<9} {desc[:90]}")
    if a.action == "install":
        if not core.FOUND:
            sys.exit("no workspace: run `researchlab init DIR` and cd into it, or set LAB_HOME")
        for home in core.SKILL_HOMES.values():
            for name, src in found.items():
                shutil.copytree(src, core.ROOT / home / name, dirs_exist_ok=True)
            print(f"installed {len(found)} skills -> {core.ROOT / home}")


def cmd_attach(a):
    from lab import attach
    attach.cmd_attach(a)


def cmd_doctor(a):
    port = int(os.environ.get("LAB_KANBAN_PORT", "8790"))
    with socket.socket() as s:
        s.settimeout(0.5)
        kanban = "up" if s.connect_ex(("127.0.0.1", port)) == 0 else "down"
    rows = [("python", sys.version.split()[0] + ("" if sys.version_info >= (3, 10) else "  (need 3.10+)")),
            ("workspace", str(core.ROOT) if core.FOUND else "none found (run `researchlab init DIR`, or set LAB_HOME)"),
            *[(t, shutil.which(t) or "missing") for t in ("codex", "claude", "node", "npm")],
            (f"kanban :{port}", kanban),
            ("utf-8 mode", "on" if sys.flags.utf8_mode else f"off (stdout {sys.stdout.encoding}; set PYTHONUTF8=1)")]
    for k, v in rows:
        print(f"{k:<14} {v}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    from lab.papers import STAGES
    force, out, yes = ("--force", {"action": "store_true"}), ("--output", {}), ("--yes", {"action": "store_true"})
    # command -> arguments: "name" is positional, (flag, kwargs) an option; commands taking only `run` or `name` are one-liners
    cmds = {"new": ["run", ("brief", {"nargs": "?", "default": ""}), "--brief-file", "--exp-dir", ("--allow-outside", {"action": "store_true"}),
                    ("--provider", {"default": "codex"}),
                    ("--q", {"default": "Q1"})],
            "pick": ["run", "cand"],
            "freeze": ["run", ("--eval", {"required": True}), ("--holdout", {"default": ""}), ("--frozen", {"nargs": "+", "required": True}),
                       ("--mutable", {"nargs": "+", "required": True}), ("--min-delta", {"default": "0"}), ("--anchor", {"type": float}),
                       ("--anchor-tol", {"type": float, "default": 0.0}), "--override"],
            **dict.fromkeys(("next", "status", "survey", "read-field", "ideate", "preregister", "screen", "fetch-data", "plan", "critic", "replan", "autofreeze", "analyze", "ablate",
                             "posthoc", "discriminate", "write", "review", "paper", "panel"), ["run"]),
            "novelty": ["run", ("--top", {"type": int, "default": 3})],
            "experiment": ["run", ("--iters", {"type": int}), ("--wall-min", {"type": float})],
            "explore": ["run", ("--iters", {"type": int}), ("--wall-min", {"type": float}), ("--drafts", {"type": int, "default": 3}),
                        ("--debug-depth", {"type": int, "default": 3}), ("--seed", {"type": int, "default": 0}),
                        ("--workers", {"type": int, "default": 1})],
            "final": ["run", force],
            "revise": ["run", ("--rounds", {"type": int, "default": 2})],
            "inspect": ["run", out],
            "campaign": ["run", ("--auto-pick", {"action": "store_true"}), ("--auto-freeze", {"action": "store_true"}),
                         ("--replan-rounds", {"type": int, "default": 6}), ("--pivots", {"type": int, "default": 0}),
                         ("--max-stages", {"type": int, "default": 60}), ("--wall-min", {"type": float, "default": 240}),
                         ("--max-chars", {"type": int, "default": 20_000_000})],
            "hypotheses": ["run", force],
            "program-init": ["name", ("--question", {"required": True}), ("--thesis", {"action": "append", "dest": "theses", "default": []})],
            "program-add": ["name", "run"],
            "program-run": ["name", ("--cycles", {"type": int, "default": 3}), ("--wall-min", {"type": float, "default": 480}),
                            ("--max-stages", {"type": int, "default": 40})],
            **dict.fromkeys(("program-board", "program-next", "program-digest"), ["name"]),
            "snapshot": ["run", ("--name", {"required": True}), ("--dev-end", {"required": True}), "--request", force],
            "meta-init": [], "meta-improve": [("--iters", {"type": int, "default": 3})], "meta-adopt": [yes],
            "dashboard": [out], "replay": [("--open", {"action": "store_true"})], "kanban-sync": [], "kanban-up": [], "kanban-down": [],
            "venue": ["name", "--source", "--issn", ("--years", {"type": int, "default": 3}), ("--site", {"nargs": "*", "default": []})],
            "venue-profile": ["name"], "methods-scan": [], "methods-review": [], "lineage": [], "kg": [], "defects": [],
            "combine": ["run"], "predict": ["run", ("--seed", {"type": int})],
            "defect-set": ["id", ("status", {"choices": ("open", "confirmed", "fixed", "rejected")}), ("--note", {"required": True})],
            "citations": [], "concepts": [], "helix-citations": [],
            "helix-sync": [("--overview-only", {"action": "store_true"}), ("--extract", {"action": "store_true"}),
                           ("--documents", {"action": "store_true"}), ("--concepts", {"action": "store_true"}), "--paper"],
            "paper-status": [],
            "paper-set": ["id", ("stage", {"choices": STAGES}), "--venue", ("--note", {"required": True}), ("--evidence", {"required": True})],
            "venue-fit": ["id", ("--reviews", {"nargs": "*", "default": []})],
            "paper-pivot": ["id", ("--venue", {"required": True})],
            "read": [("--paper", {"required": True}), "--run", ("--mode", {"choices": ("skim", "deep"), "default": "skim"}), "--thesis"],
            "selftest": [], "init": [("dir", {"nargs": "?", "default": "."}), force], "doctor": [],
            "attach": [("bundle", {"nargs": "?"}), "--name", ("--install", {"action": "store_true"}), "--python", ("--list", {"action": "store_true"})],
            "skills": [("action", {"nargs": "?", "choices": ("list", "install"), "default": "list"})],
            "demo": [("name", {"nargs": "?", "choices": ("quickstart", "jev", "gerber"), "default": "quickstart"}), ("--run", {"action": "store_true"})]}
    for name, spec in cmds.items():
        sp = sub.add_parser(name)
        for x in spec:
            flag, kw = (x, {}) if isinstance(x, str) else x
            sp.add_argument(flag, **kw)
    if argv is None and not sys.flags.utf8_mode and locale.getpreferredencoding(False).lower().replace("-", "") != "utf8":
        # the installed researchlab.exe starts in the legacy codepage (cp949 ...); PYTHONUTF8 also reaches child pythons, as lab.cmd/lab.sh set it
        sys.exit(subprocess.call([sys.executable, "-m", "lab.cli", *sys.argv[1:]], env=dict(os.environ, PYTHONUTF8="1")))
    a = ap.parse_args(argv)
    if a.cmd in ("init", "doctor", "demo", "skills"):
        return globals()[f"cmd_{a.cmd}"](a)
    if not core.FOUND:
        sys.exit("no workspace: run `researchlab init DIR` and cd into it, or set LAB_HOME")
    if a.cmd == "next":
        return print(director.next_cmd(core.run_dir(a.run)))
    if a.cmd == "status":
        return director.status(core.run_dir(a.run))
    from lab import stages  # imported here so `next`/`status` skip the stage modules
    if a.cmd == "selftest":
        return stages.selftest()
    if a.cmd == "replay":
        from lab import replay
        return replay.replay_cmd(a)
    if a.cmd == "predict":
        from lab import predict
        return predict.predict_cmd(a)
    fn = globals().get(f"cmd_{a.cmd}") or getattr(stages, a.cmd.replace("-", "_"))
    ok = False
    try:
        fn(a)
        ok = True
    finally:
        if hasattr(a, "run") or a.cmd in ("program-run", "program-next", "program-add", "program-init"):
            from lab import kanban
            kanban.refresh()
            if ok and hasattr(a, "run") and a.cmd != "campaign":  # campaign records its own stages
                from lab import replay
                replay.record(core.run_dir(a.run), a.cmd)


def selftest():
    repo = Path(__file__).resolve().parent.parent  # packaged defaults must not drift from the repo copies
    if (repo / "prompts").is_dir() and (repo / "lab" / "defaults" / "prompts").is_dir():
        for f in (repo / "prompts").rglob("*.md"):
            g = repo / "lab" / "defaults" / "prompts" / f.relative_to(repo / "prompts")
            assert g.is_file() and g.read_bytes() == f.read_bytes(), f"defaults drift: {g} (copy prompts/ to lab/defaults/prompts/)"
        assert (repo / "harness.json").read_bytes() == (repo / "lab" / "defaults" / "harness.json").read_bytes(), "defaults drift: harness.json"
    import contextlib
    import hashlib
    import io
    import tempfile
    from unittest.mock import patch
    with tempfile.TemporaryDirectory() as t:
        t = Path(t).resolve()
        ws, deep, empty = t / "ws", t / "ws/a/b", t / "empty"
        deep.mkdir(parents=True); empty.mkdir()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            main(["init", str(ws)])
        assert (ws / "harness.json").is_file() and (ws / "prompts/critic.md").is_file() and (ws / "memory/global/INDEX.md").is_file()
        assert (ws / "memory/roles/reviewer.md").is_file() and (ws / "runs").is_dir() and (ws / "programs").is_dir()
        try:
            main(["init", str(ws)])
            raise AssertionError("init must refuse to overwrite")
        except SystemExit as e:
            assert "--force" in str(e)
        (ws / "harness.json").write_text("{}")
        with contextlib.redirect_stdout(out):
            main(["init", str(ws), "--force"])
        assert "roles" in json.loads((ws / "harness.json").read_text())
        env = {k: v for k, v in os.environ.items() if k != "LAB_HOME"}
        with patch.dict(os.environ, env, clear=True), patch("os.getcwd", return_value=str(deep)), patch.object(Path, "cwd", return_value=deep):
            assert core.find_root() == (ws, True)  # upward search
            with patch.dict(os.environ, {"LAB_HOME": str(empty)}):
                assert core.find_root() == (empty.resolve(), True)  # LAB_HOME wins
        with patch.dict(os.environ, env, clear=True), patch.object(Path, "cwd", return_value=empty), patch.object(core, "PKG_DEFAULTS", empty / "x" / "y"):
            assert core.find_root() == (empty, False)  # nothing found, not the repo
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            main(["doctor"])
        assert "workspace" in out.getvalue() and "python" in out.getvalue()
        out = io.StringIO()
        with patch.object(core, "ROOT", ws), patch.object(core, "FOUND", True), contextlib.redirect_stdout(out):
            main(["skills"])
            assert not (ws / ".claude").exists()
            main(["skills", "install"])
        names = sorted(core.skill_dirs(ws))
        assert all(n in out.getvalue() for n in names) and len(names) >= 5
        assert all(sorted(os.listdir(ws / h)) == names for h in core.SKILL_HOMES.values())  # inside the workspace only
        for name, last in (("quickstart", "holdout of #2"), ("jev", "per-task thresholds")):  # scripted examples run from wherever the package sits
            out = io.StringIO()
            with patch.object(core, "ROOT", ws), patch.object(core, "FOUND", True), patch.dict(os.environ, {"LAB_KANBAN_AUTO": "0"}), contextlib.redirect_stdout(out):
                main(["demo", name])  # inside the temp workspace: a selftest never writes runs/ of the real one
            assert all(f"#{n} Q1" in out.getvalue() for n in range(1, 6)) and last in out.getvalue(), out.getvalue()
        # exp-dir stays inside the workspace unless --allow-outside; new runs carry guard=true and freeze records the guard root
        out2 = t / "outside_exp"
        with patch.object(core, "ROOT", ws), contextlib.redirect_stdout(io.StringIO()):
            try:
                main(["new", "r0", "b", "--exp-dir", str(out2)])
                raise AssertionError("outside exp-dir accepted")
            except SystemExit as e:
                assert "--allow-outside" in str(e) and "data" in str(e) and not (ws / "runs" / "r0").exists()
            main(["new", "r1", "b"])
            main(["new", "r2", "b", "--exp-dir", str(out2), "--allow-outside"])
            assert json.loads((ws / "runs/r1/run.json").read_text())["guard"] is True
            assert json.loads((ws / "runs/r2/run.json").read_text())["guard"] is False and out2.is_dir()
            old = json.loads((ws / "runs/r1/run.json").read_text()); old.pop("guard")
            (ws / "runs/r3").mkdir(); (ws / "runs/r3/run.json").write_text(json.dumps(old))
            assert not core.cfg(ws / "runs/r3").get("guard")
        for rid, want in (("r1", str(ws)), ("r2", None)):
            exp = Path(core.cfg(ws / "runs" / rid)["exp_dir"])
            (exp / "e.py").write_text("print(1)\n")
            with patch.object(core, "ROOT", ws), contextlib.redirect_stdout(io.StringIO()):
                main(["freeze", rid, "--eval", "python3 e.py", "--frozen", "e.py", "--mutable", "e.py", "--override", "selftest"])
            assert json.loads((exp / "loop.json").read_text())["Q1"].get("guard_root") == want
        # demo gerber: setup only (no model); fake example folder, sha check, interpreter substituted, workspace required
        ex = t / "gex"
        (ex / "data").mkdir(parents=True)
        (ex / "data/a.txt").write_text("x\n")
        (ex / "data/MANIFEST.json").write_text(json.dumps(dict(name="kr_a200_dev", source_url="bundled", license="demo", retrieved_utc="t",
                                                            files=[dict(path="a.txt", sha256=hashlib.sha256(b"x\n").hexdigest(), bytes=2)])))
        (ex / "brief.md").write_text("use {PYTHON}\n")
        from lab import gerber_demo
        for found, root, patch_ex, want in ((False, ws, ex, "researchlab init"), (True, t / "gws", ex, None)):
            (t / "gws").mkdir(exist_ok=True)
            with patch.object(core, "ROOT", root), patch.object(core, "FOUND", found), patch.object(gerber_demo, "_numpy_python", lambda: sys.executable), contextlib.redirect_stdout(io.StringIO()):
                try:
                    gerber_demo.setup(example=patch_ex)
                    assert want is None
                except SystemExit as e:
                    assert want and want in str(e), e
        g = t / "gws"
        assert (g / "data/kr_a200_dev/a.txt").read_text() == "x\n" and (g / "runs/demo-gerber/brief.md").read_text() == f"use {sys.executable}\n"
        assert core.cfg(g / "runs/demo-gerber")["guard"] is True
        from lab import fetchdata  # the copied data must count as present for fetch-data (its MANIFEST schema)
        assert fetchdata._verify(g / "data", "kr_a200_dev", 10**9, prune=False)[1] is None
        real = core.PKG_DEFAULTS / "examples/gerber/data"  # the shipped demo data too
        with tempfile.TemporaryDirectory() as rt:
            shutil.copytree(real, Path(rt) / "kr_a200_dev")
            assert fetchdata._verify(Path(rt), "kr_a200_dev", 10**9, prune=False)[1] is None
        (ex / "data/a.txt").write_text("y\n")
        shutil.rmtree(g / "runs/demo-gerber")
        with patch.object(core, "ROOT", g), patch.object(core, "FOUND", True), patch.object(gerber_demo, "_numpy_python", lambda: sys.executable), contextlib.redirect_stdout(io.StringIO()):
            try:
                gerber_demo.setup(example=ex)
                raise AssertionError("sha mismatch accepted")
            except SystemExit as e:
                assert "sha256" in str(e) and not (g / "runs/demo-gerber").exists()
    print("cli selftest ok")


if __name__ == "__main__":
    main()
