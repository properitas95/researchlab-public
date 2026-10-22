"""Research programs: accumulate thesis evidence and sample the next question."""
import hashlib
import json
import random
import re
import shutil
import tempfile
import time
from argparse import Namespace
from pathlib import Path

from lab import core, roles, director


def _dir(name):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", name):
        raise ValueError("program name must contain only letters, digits, _ or -")
    return core.ROOT / "programs" / name


def _counts(t):
    return tuple(sum(e["direction"] == d for e in t["evidence"]) for d in ("supports", "refutes"))


def _status(t):
    s, r = _counts(t)
    if any(e["direction"] == "refutes" and e["verdict"] == "supported_within_scope" for e in t["evidence"]) or (r >= 2 and not s):
        return "refuted"
    return ("contested" if s and r else "supported" if s >= 2 else "leaning" if s == 1
            else "doubtful" if r == 1 else "open")


def _lines(p):
    return [f"{t['id']} {t['status']} supports/refutes={_counts(t)[0]}/{_counts(t)[1]} {t['statement']}" for t in p["theses"]]


def _save(p):
    d = _dir(p["name"])
    for t in p["theses"]:
        t["status"] = _status(t)
    (d / "program.json").write_text(json.dumps(p, ensure_ascii=False, indent=1) + "\n")
    (d / "BOARD.md").write_text(f"# {p['name']}\n\n{p['question']}\n\n" + "\n".join(_lines(p)) + "\n")


def _load(name):
    return json.loads((_dir(name) / "program.json").read_text())


def _ask(p, files, mode, target="", verdict="", run=None):
    with tempfile.TemporaryDirectory(prefix="program-") as tmp:
        for f in [_dir(p["name"]) / "program.json", _dir(p["name"]) / "BOARD.md", *files]:
            if f.exists():
                shutil.copy2(f, Path(tmp) / f.name)
        return roles.call("program", core.prompt("next_question", mode=mode, target=target, verdict=verdict), cwd=tmp, run=run, write=False)


def program_init(a):
    if not a.question.strip() or not a.theses or not all(s.strip() for s in a.theses):
        raise ValueError("question and theses must be nonempty")
    _dir(a.name).mkdir(parents=True)
    _save(dict(name=a.name, question=a.question, theses=[dict(id=f"T{i}", statement=s, status="open", evidence=[])
           for i, s in enumerate(a.theses, 1)], runs=[], history=[dict(action="init", utc=core.now())]))


def program_add(a):
    p, d = _load(a.name), core.run_dir(a.run)
    rid, c = d.name, core.cfg(d)
    if any(e["run"] == rid for t in p["theses"] for e in t["evidence"]) or any(
            h.get("action") == "add" and h.get("run") == rid for h in p["history"]):
        return
    exp = Path(c["exp_dir"])
    verdict = director._verdict(exp, c["q"])
    if not verdict:
        raise ValueError("run has no verdict")
    (d / "pick.json").read_text()
    rows = core.last_json(_ask(p, [d / "pick.json", exp / "DECISIONS.md", d / "RESULT.md"], "add", verdict=verdict, run=d))
    if not isinstance(rows, list):
        raise ValueError("evidence must be a JSON list")
    by, seen = {t["id"]: t for t in p["theses"]}, set()
    utc = core.now()
    for row in rows:
        if not isinstance(row, dict) or row.get("direction") not in ("supports", "refutes", "neutral") or not isinstance(row.get("note"), str) or not row["note"].strip():
            raise ValueError("invalid evidence direction or note")
        tid = row.get("thesis")
        if tid == "NEW":
            if not isinstance(row.get("statement"), str) or not row["statement"].strip():
                raise ValueError("NEW needs a statement")
            tid = f"T{len(by) + 1}"
            by[tid] = dict(id=tid, statement=row["statement"], evidence=[])
            p["theses"].append(by[tid])
        if not isinstance(tid, str) or tid not in by or tid in seen:
            raise ValueError("unknown or duplicate thesis")
        seen.add(tid)
        by[tid]["evidence"].append(dict(run=rid, verdict=verdict, direction=row["direction"], note=row["note"], utc=utc))
    if rid not in p["runs"]:
        p["runs"].append(rid)
    p["history"].append(dict(action="add", run=rid, utc=utc))
    _save(p)
    core.event(d, "program", f"{a.name}: added {rid}", program=a.name)


def program_board(a):
    print("\n".join(_lines(_load(a.name))))


def _target(p):
    weights = [{"contested": 3, "open": 2, "leaning": 2, "doubtful": 2, "supported": .5, "refuted": 0}[t["status"]]
               * (1 + 1 / (1 + len(t["evidence"]))) for t in p["theses"]]
    if not sum(weights):
        raise ValueError("no eligible thesis")
    return random.Random(len(p["history"])).choices(p["theses"], weights=weights)[0]


def program_next(a):
    p = _load(a.name)
    if not p["runs"]:
        raise ValueError("add a completed run first")
    target, latest = _target(p), core.ROOT / "runs" / p["runs"][-1]
    c = core.cfg(latest)
    rid = f"{a.name}-{len(p['runs']) + 1:02d}"
    d, exp = core.ROOT / "runs" / rid, Path(c["exp_dir"]).parent / f"{rid}_lab"
    if d.exists() or exp.exists():
        raise FileExistsError(f"run or experiment already exists: {rid}")
    brief = _ask(p, [latest / "RESULT.md", latest / "field.md", latest / "LEDGER.md", Path(c["exp_dir"]) / "PROTOCOL.md"],
                 "next", target=target["id"], run=latest).strip()
    if not brief:
        raise ValueError("empty brief")
    d.mkdir(parents=True); exp.mkdir(parents=True)
    (d / "brief.md").write_text(brief + "\n")
    core.save_cfg(d, dict(id=rid, exp_dir=str(exp), provider=c["provider"], model=c["model"], q=c["q"], program=a.name))
    p["runs"].append(rid)
    p["history"].append(dict(action="next", run=rid, thesis=target["id"], utc=core.now()))
    _save(p)
    core.event(d, "program", f"{a.name}: next question for {target['id']}", program=a.name)
    print(f"{rid}\n./lab.py next {rid}")


def _events(d):
    f = d / "events.jsonl"
    return [json.loads(line) for line in f.read_text().splitlines() if line.strip()] if f.exists() else []


def _record(name, action, **fields):
    p = _load(name)
    p["history"].append(dict(action=action, utc=core.now(), **fields))
    _save(p)
    core.event(_dir(name), "program", action, **fields)


def _parked(d):
    return any(e.get("stage") == "park" or e.get("action") == "park" for e in _events(d))


def program_run(a):
    from lab.campaign import campaign
    end = time.monotonic() + getattr(a, "wall_min", 480) * 60
    failed, reason = 0, "cycles exhausted"
    try:
        for _ in range(getattr(a, "cycles", 3)):
            p = _load(a.name)
            if all(t["status"] in ("supported", "refuted") for t in p["theses"]):
                reason = "program concluded"; break
            if time.monotonic() >= end:
                reason = "wall clock exhausted"; break
            if not p["runs"]:
                reason = "needs a seed run: program-add first"; break
            rid = p["runs"][-1]
            added = any(e["run"] == rid for t in p["theses"] for e in t["evidence"])
            handled = any(h.get("run") == rid and h["action"] in ("add", "park", "failed") for h in p["history"])
            if added or handled:
                program_next(a)
                rid = _load(a.name)["runs"][-1]
                if time.monotonic() >= end:
                    reason = "wall clock exhausted"; break
            d = core.run_dir(rid)
            try:
                if not _parked(d) and not director.next_cmd(d).startswith("DONE"):
                    _record(a.name, "campaign", run=rid, auto_pick=True, auto_freeze=True, pivots=0)
                    result = campaign(Namespace(run=str(d), auto_pick=True, auto_freeze=True, pivots=0,
                                       replan_rounds=getattr(a, "replan_rounds", 6),
                                       max_stages=getattr(a, "max_stages", 40), wall_min=max(0, (end-time.monotonic())/60)))
                    if result == "parked" and not _parked(d):
                        core.event(d, "park", "campaign returned parked")
                if _parked(d):
                    outcome = "park"
                elif director.next_cmd(d).startswith("DONE"):
                    program_add(Namespace(name=a.name, run=rid))
                    outcome = "done"
                else:
                    ev = _events(d)
                    if time.monotonic() >= end or (ev and ev[-1].get("msg") == "budget exhausted"):
                        reason = "campaign budget exhausted"; break
                    outcome = "failed"
                _record(a.name, outcome, run=rid)
            except (Exception, SystemExit) as exc:
                outcome = "failed"
                _record(a.name, outcome, run=rid, error=str(exc))
            finally:
                p = _load(a.name)
                seen = {h.get("event_index") for h in p["history"] if h.get("run") == rid and h["action"] == "automatic"}
                for i, e in enumerate(_events(d)):
                    if i not in seen and e.get("stage") in ("campaign", "autofreeze", "auto_freeze", "freeze", "park", "replan"):
                        _record(a.name, "automatic", run=rid, event_index=i, event=e)
            failed = failed + 1 if outcome in ("park", "failed") else 0
            if failed >= 2:
                reason = "two consecutive parked/failed runs"; break
        if all(t["status"] in ("supported", "refuted") for t in _load(a.name)["theses"]):
            reason = "program concluded"
        _record(a.name, "stop", reason=reason)
    except (Exception, SystemExit) as exc:
        _record(a.name, "stop", reason="program error", error=str(exc))
    finally:
        program_digest(a)


def program_digest(a):
    p, d = _load(a.name), _dir(a.name)
    previous = p.get("digest", {})
    body = [f"# {a.name} digest", f"Period: {previous.get('utc', 'start')} → {core.now()}",
            "## Board", "| Thesis | Statement | Status | Previous |", "|---|---|---|---|"]
    cell = lambda x: str(x).replace("|", "\\|").replace("\n", " ")
    body += [f"| {t['id']} | {cell(t['statement'])} | {t['status']} | {previous.get('board', {}).get(t['id'], '—')} |" for t in p["theses"]]
    needs = [f"{t['id']}: contested thesis" for t in p["theses"] if t["status"] == "contested"]
    if not p["runs"]:
        needs.append("Seed run required: program-add NAME RUN")
    needs += [h["error"] for h in p["history"][previous.get("history", 0):] if h.get("action") == "stop" and h.get("error")]
    snapshots = {}
    for rid in p["runs"]:
        run = core.run_dir(rid)
        c = core.cfg(run)
        exp = Path(c["exp_dir"])
        paths = [run / "brief.md", exp / "DECISIONS.md", run / "discrimination.json", run / "critique.json", exp / "PROTOCOL.md"]
        brief, decisions, discrimination, critique, protocol = [f.read_text() if f.exists() else "" for f in paths]
        events = _events(run)
        raw = "\n".join(json.dumps(e, ensure_ascii=False) for e in events)
        snapshots[rid] = hashlib.sha256(("\n".join([brief, decisions, discrimination, critique, protocol]) + raw).encode()).hexdigest()
        if _parked(run):
            needs.append(f"{rid}: parked run")
        if any(h.get("run") == rid and h["action"] == "failed" for h in p["history"]):
            needs.append(f"{rid}: failed run")
        warnings = [line for line in (protocol + "\n" + raw).splitlines()
                    if re.search(r"holdout|confirmation|확인 표본|홀드아웃", line, re.I)
                    and re.search(r'not virgin|non.?virgin|virgin["\s:=]+false|contaminat|retrospective|already|may have|unknown|불명|재사용|오염', line, re.I)]
        if warnings:
            needs.append(f"{rid}: holdout not virgin or virginity unresolved — " + " ".join(warnings))
        if previous.get("runs", {}).get(rid) == snapshots[rid]:
            continue
        body += [f"## Run {rid}", "### Question", brief or "(not recorded)", "### Verdict / key numbers (DECISIONS.md)", decisions or "(pending)"]
        if discrimination:
            body += ["Survivors: " + json.dumps(json.loads(discrimination).get("survivors", []), ensure_ascii=False)]
        findings = json.loads(critique).get("findings", []) if critique else []
        accepted = [e["accepted_limitations"] for e in events if e.get("accepted_limitations")]
        accepted += [f for f in findings if f.get("accepted_as_limitation") or f.get("status") == "accepted_as_limitation"]
        body += ["### Accepted critic limitations", json.dumps(accepted, ensure_ascii=False) if accepted else "(none explicitly recorded)"]
        body += [line for line in protocol.splitlines() if re.search(r"accepted.*limitation|limitation.*accepted|수용.*한계", line, re.I)]
        body += ["### Automatic decisions (timestamped events)"]
        start = previous.get("offsets", {}).get(rid, 0)
        body += ["- " + json.dumps(e, ensure_ascii=False) for e in events[start:]
                 if e.get("stage") in ("campaign", "autofreeze", "auto_freeze", "freeze", "park", "replan")
                 or re.search(r"auto.?pick|auto.?freeze|pivot|limitation", json.dumps(e), re.I)]
    body += ["## Program decisions"] + ["- " + json.dumps(h, ensure_ascii=False) for h in p["history"][previous.get("history", 0):]]
    body += ["## Needs a human"] + (["- " + n for n in needs] or ["- None recorded."])
    text = "\n".join(body) + "\n"
    if getattr(a, "summary", False) and p["runs"]:
        try:
            with tempfile.TemporaryDirectory(prefix="digest-") as tmp:
                summary = roles.call("digest", core.prompt("digest", digest=text), cwd=tmp, run=core.run_dir(p["runs"][-1]), write=False)
            text = "\n".join(summary.strip().splitlines()[:8]) + "\n\n" + text
        except Exception:
            pass
    (d / "DIGEST.md").write_text(text)
    p["digest"] = dict(utc=core.now(), board={t["id"]: t["status"] for t in p["theses"]}, runs=snapshots,
                       offsets={rid: len(_events(core.run_dir(rid))) for rid in p["runs"]}, history=len(p["history"]))
    _save(p)


def selftest():
    from unittest.mock import patch
    invoke = core.invoke
    fake = '''import json, pathlib, sys
s = sys.stdin.read(); p = pathlib.Path
assert p('program.json').exists() and p('BOARD.md').exists()
if 'MODE: add' in s:
    v = 'supported_within_scope' in p('DECISIONS.md').read_text()
    print(json.dumps([dict(thesis='T1', direction='supports' if v else 'refutes', note='tested improvement'),
                      dict(thesis='T2', direction='refutes', note='counterexample')]))
else:
    assert p('RESULT.md').read_text() == 'result' and p('field.md').read_text() == 'field'
    print('# Question\\n' + s.split('TARGET: ')[1].splitlines()[0] + '\\nSupport: improves. Refute: does not.')
'''
    def staged(provider, instruction, cwd, model, write, timeout=1800, env=None):
        assert provider == "cmd:python3 fake.py" and write is False
        (Path(cwd) / "fake.py").write_text(fake)
        return invoke(provider, instruction, cwd, model, write, env=env)
    with tempfile.TemporaryDirectory() as tmp, patch.object(core, "ROOT", Path(tmp)), patch.object(core, "invoke", staged):
        a = Namespace(name="toy", question="Improve?", theses=["A improves", "B improves"])
        program_init(a)
        for i, v in enumerate(("supported_within_scope", "no_improvement"), 1):
            d = core.ROOT / "runs" / f"r{i}"; exp = core.ROOT / f"exp{i}"
            d.mkdir(parents=True); exp.mkdir()
            core.save_cfg(d, dict(id=d.name, exp_dir=str(exp), provider="cmd:python3 fake.py", q="Q1"))
            for f, text in (("pick.json", '{}'), ("RESULT.md", "result"), ("field.md", "field")):
                (d / f).write_text(text)
            (exp / "DECISIONS.md").write_text(f"- today Q1 **{v}**: reason\n")
            program_add(Namespace(name="toy", run=d.name))
        p = _load("toy")
        assert [t["status"] for t in p["theses"]] == ["contested", "refuted"]
        program_add(Namespace(name="toy", run="r2")); assert _load("toy") == p
        s = dict(direction="supports", verdict="supported_within_scope")
        assert [_status(dict(evidence=e)) for e in ([], [s], [s, s])] == ["open", "leaning", "supported"]
        program_next(a)
        d = core.ROOT / "runs" / "toy-03"
        brief = (d / "brief.md").read_text()
        assert "T1" in brief and Path(core.cfg(d)["exp_dir"]).is_dir()
        shutil.rmtree(Path(core.cfg(d)["exp_dir"])); shutil.rmtree(d); _save(p)
        program_next(a); assert (d / "brief.md").read_text() == brief
        board = (_dir("toy") / "BOARD.md").read_text()
        assert "T1 contested supports/refutes=1/1" in board and "T2 refuted" in board
        assert core.cfg(d)["program"] == "toy" and '"stage": "program"' in (d / "events.jsonl").read_text()
        shutil.copytree(core.ROOT / "runs" / "r2", core.ROOT / "runs" / "r3")
        before, original = _load("toy"), fake
        fake = original.replace("thesis='T1'", "thesis='T999'")
        try:
            program_add(Namespace(name="toy", run="r3"))
        except ValueError:
            pass
        else:
            raise AssertionError("unknown thesis accepted")
        assert _load("toy") == before
        fake = original.replace("thesis='T1'", "thesis='NEW', statement='C improves'")
        program_add(Namespace(name="toy", run="r3"))
        assert _load("toy")["theses"][-1]["id"] == "T3"
        assert re.search(r"T3 (open|doubtful)", (_dir("toy") / "BOARD.md").read_text())
        from lab import campaign as campaigns
        fake = original
        calls, outcomes = [], []
        def campaign(ns):
            assert ns.auto_pick and ns.auto_freeze and ns.pivots == 0 and ns.replan_rounds == 6
            assert ns.max_stages == 40 and 0 < ns.wall_min <= 480
            run = core.run_dir(ns.run); exp = Path(core.cfg(run)["exp_dir"])
            assert core.cfg(run)["program"] == a.name
            calls.append(run.name)
            outcome = outcomes.pop(0)
            core.event(run, "campaign", "auto-pick", candidate="C1")
            core.event(run, "autofreeze", "auto-freeze", spec={"eval": "python3 eval.py"}, limitations=["small sample"])
            core.event(run, "campaign", "pivot disabled", pivots=0)
            for f, text in (("pick.json", '{}'), ("RESULT.md", "result"), ("field.md", "field")):
                (run / f).write_text(text)
            (exp / "PROTOCOL.md").write_text("holdout is not virgin: retrospective reuse")
            (run / "critique.json").write_text(json.dumps({"findings": [{"claim": "small sample", "accepted_as_limitation": True}]}))
            if outcome == "park":
                core.event(run, "park", "critic blockers remain")
                return "parked"
            elif outcome == "budget":
                core.event(run, "campaign", "budget exhausted")
            elif outcome == "fail":
                raise RuntimeError("failed model")
            else:
                (run / "discrimination.json").write_text('{"survivors":["H2"]}')
                (exp / "DECISIONS.md").write_text(f"- today Q1 **{outcome}**: gain 0.125 > 0.03\n")
                (run / "DONE").touch()
                program_add(Namespace(name=a.name, run=run.name))
        def next_cmd(run):
            return "DONE" if (run / "DONE").exists() else "./lab.py survey " + run.name
        def seed(name):
            nonlocal a
            a = Namespace(name=name, question="Improve?", theses=["A improves", "B improves"])
            program_init(a)
            program_add(Namespace(name=name, run="r1"))
        with patch.object(campaigns, "campaign", campaign), patch.object(director, "next_cmd", next_cmd):
            seed("decided"); outcomes[:] = ["supported_within_scope"]
            program_digest(a)
            program_run(a)
            state = _load(a.name)
            assert len(calls) == 1 and state["history"][-1]["reason"] == "program concluded"
            assert len(state["theses"][0]["evidence"]) == 2
            assert sum(h["action"] == "add" for h in state["history"]) == 2
            text = (_dir(a.name) / "DIGEST.md").read_text()
            assert "| supported | leaning |" in text
            assert all(x in text for x in ("auto-pick", "auto-freeze", "python3 eval.py", "pivot", "0.125", "H2", "small sample", "not virgin"))
            program_run(a); assert len(calls) == 1
            program_digest(a)
            assert "## Run" not in (_dir(a.name) / "DIGEST.md").read_text()
            seed("existing"); calls.clear()
            program_next(a)
            run = core.run_dir(_load(a.name)["runs"][-1])
            exp = Path(core.cfg(run)["exp_dir"])
            (run / "DONE").touch(); (run / "pick.json").write_text('{}')
            (exp / "DECISIONS.md").write_text('- today Q1 **supported_within_scope**: 0.125')
            program_run(a)
            assert not calls and len(_load(a.name)["theses"][0]["evidence"]) == 2
            seed("parked"); calls.clear(); outcomes[:] = ["park", "park", "supported_within_scope"]
            program_run(a)
            assert len(calls) == 2 and _load(a.name)["history"][-1]["reason"] == "two consecutive parked/failed runs"
            assert "parked run" in (_dir(a.name) / "DIGEST.md").read_text()
            seed("mixed"); calls.clear(); outcomes[:] = ["park", "supported_within_scope"]
            program_run(a)
            assert len(calls) == 2 and _load(a.name)["history"][-1]["reason"] == "program concluded"
            seed("limited"); calls.clear(); outcomes[:] = ["no_improvement"]
            a.cycles = 1
            program_run(a); assert len(calls) == 1
            assert "contested thesis" in (_dir(a.name) / "DIGEST.md").read_text()
            a.wall_min = 0
            program_run(a); assert len(calls) == 1
            assert _load(a.name)["history"][-1]["reason"] == "wall clock exhausted"
            seed("budget"); calls.clear(); outcomes[:] = ["budget"]
            program_run(a); assert len(calls) == 1
            assert _load(a.name)["history"][-1]["reason"] == "campaign budget exhausted"
            outcomes[:] = ["supported_within_scope"]
            program_run(a); assert calls[0] == calls[1]
            fake = "import sys; sys.stdin.read(); print('요약\\n' * 10)"
            a.summary = True
            program_digest(a)
            assert len((_dir(a.name) / "DIGEST.md").read_text().split("# budget digest")[0].strip().splitlines()) == 8
            fake = "raise RuntimeError('offline')"
            program_digest(a)
            assert (_dir(a.name) / "DIGEST.md").read_text().startswith("# budget digest")
    print("program selftest ok")
