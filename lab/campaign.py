"""Run the director's stages within a budget; candidate choice and automatic freeze are opt-in."""
import json
import os
import shlex
import shutil
import tempfile
import time
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

from lab import core, director, roles, testkit


def _park(d, rounds):
    reason = f"design unresolved after {rounds} replan rounds"
    core.event(d, "park", reason, reason=reason, rounds=rounds)
    return "parked"


def _chars(d):
    """Instruction + reply characters of the run's model calls so far."""
    f = d / "calls.jsonl"
    return sum(r.get("chars_in", 0) + r.get("chars_out", 0) for r in map(json.loads, f.read_text().splitlines())) if f.exists() else 0


def campaign(a):
    from lab import stages, autofreeze, kanban, critic
    d = root = core.run_dir(a.run).resolve()
    original = core.cfg(d)["exp_dir"]
    wall, max_stages, max_chars = getattr(a, "wall_min", 240), getattr(a, "max_stages", 60), getattr(a, "max_chars", 20_000_000)
    rounds = getattr(a, "replan_rounds", 6)
    end, used, pivots = time.monotonic() + wall * 60, 0, 0
    replans = 0
    budget = dict(wall_min=wall, max_stages=max_stages, max_chars=max_chars, replan_rounds=rounds)
    core.event(d, "campaign", "campaign start", **budget)

    def exhausted():
        why = "time" if time.monotonic() >= end else "stages" if used >= max_stages else "chars" if _chars(d) > max_chars else ""
        if why:
            core.event(d, "campaign", "budget exhausted" + (" (chars)" if why == "chars" else ""), attempts=used, chars=_chars(d), **budget)
        return bool(why)
    while True:
        if exhausted():
            return
        cmd, tagged_freeze = director.next_cmd(d), False
        core.event(d, "campaign", cmd)
        kanban.mirror()  # the board follows every stage change
        if cmd.startswith("USER:"):
            if getattr(a, "auto_freeze", False) and (cmd.startswith("USER: fix the design first") or
                                                     (cmd.startswith("USER: read ") and " freeze " in cmd)):
                critique = json.loads((d / "critique.json").read_text())
                dirty = any(critic.triage(critique)[:2])  # limitation majors alone do not block a freeze
                if dirty and replans >= rounds:  # end of the rounds: freeze with the unresolved findings tagged unless the design is invalid
                    if critic.validity(critique):
                        return _park(d, replans)
                    tagged_freeze = True
                cmd = f"./lab.py {'replan' if dirty and not tagged_freeze else 'autofreeze'} {d.name}"
            elif "choose a candidate" in cmd and getattr(a, "auto_pick", False):
                novelty = json.loads((d / "novelty.json").read_text())
                candidates = json.loads((d / "candidates.json").read_text())
                eligible = [c for c in candidates if not c.get("predictable")
                            and novelty.get(c["id"], {}).get("verdict") != "prior_work"]
                if not eligible:
                    core.event(d, "campaign", "stop: no eligible candidate")
                    return
                from lab import predict  # discounted Thompson sampling over KG/ledger posteriors, not self-reported odds
                combos = json.loads((d / "combos.json").read_text(encoding="utf-8")) if (d / "combos.json").exists() else []
                post = predict.posterior(predict.load_ledger(), predict.load_concepts())
                pick, _ = predict.choose(eligible, post, combos=combos)
                predict.record(d, pick["id"], predict.predict(pick, post, combos)[0], pick.get("p_success"))
                (d / "pick.json").write_text(json.dumps(pick, indent=1, ensure_ascii=False) + "\n")
                core.event(d, "campaign", "auto-pick by campaign", candidate=pick["id"])
                continue
            else:
                return
        if cmd.startswith("DONE"):
            c = core.cfg(d)
            if c.get("program") and not any(e.get("stage") == "program" for e in map(json.loads, (d / "events.jsonl").read_text().splitlines())):
                stages.program_add(Namespace(name=c["program"], run=str(d)))  # the finished run updates its thesis board
            verdict = director._verdict(Path(c["exp_dir"]), c["q"])
            if verdict not in ("no_improvement", "inconclusive") or pivots >= getattr(a, "pivots", 0):
                return
            pivots += 1
            sibling, exp = root.with_name(f"{root.name}-p{pivots}"), Path(f"{original}-p{pivots}")
            if sibling.exists() or exp.exists():
                core.event(d, "campaign", f"stop: pivot destination exists: {sibling}, {exp}")
                return
            picked = json.loads((d / "pick.json").read_text())["id"]
            candidates = [x for x in json.loads((d / "candidates.json").read_text()) if x["id"] != picked]
            sibling.mkdir()
            exp.mkdir(parents=True)
            for name in ("brief.md", "field.md", "field_papers.md", "search.json", "novelty.json", "run.json"):
                if (d / name).exists():
                    shutil.copy2(d / name, sibling / name)
            if (d / "screen").is_dir():  # the sealed candidate scores carry over; re-screening would reorder
                shutil.copytree(d / "screen", sibling / "screen")
            (sibling / "candidates.json").write_text(json.dumps(candidates, indent=1, ensure_ascii=False) + "\n")
            core.save_cfg(sibling, c | {"id": sibling.name, "exp_dir": str(exp)})
            core.event(d, "campaign", f"pivot to {sibling.name}", excluded=picked)
            d, replans = sibling, 0
            core.event(d, "campaign", "pivot started", excluded=picked)
            continue
        parts = shlex.split(cmd.split("   (", 1)[0])
        if len(parts) != 3 or parts[0] != "./lab.py" or parts[1] not in (
                "survey", "read-field", "ideate", "preregister", "screen", "novelty", "fetch-data", "plan", "critic", "explore", "experiment", "analyze", "ablate",
                "final", "posthoc", "hypotheses", "discriminate", "write", "review", "paper", "panel", "revise",
                "inspect", "replan", "autofreeze"):
            core.event(d, "campaign", f"stop: unsupported command: {cmd}")
            return
        stage = parts[1]
        ns = Namespace(run=str(d), iters=None, wall_min=None, top=3, drafts=3, debug_depth=3,
                       seed=0, rounds=2, force=False, workers=1, output=None, tagged=tagged_freeze)
        previous = os.environ.pop("LAB_LAST_ERROR", None)
        try:
            for attempt in range(1, 4):
                if exhausted():
                    return
                if stage == "replan" and attempt == 1:  # self-heal retries are not new rounds
                    replans += 1
                fallback = roles.alternate(core.cfg(d).get("provider")) if attempt == 3 else None  # failed twice: one try with the other CLI
                used += 1
                core.event(d, "campaign", f"run {stage}", attempt=attempt, last_error=os.environ.get("LAB_LAST_ERROR"),
                           **(dict(fallback=fallback) if fallback else {}))
                if fallback:
                    os.environ["LAB_STAGE_FALLBACK"] = "1"
                try:
                    getattr(autofreeze if stage in ("replan", "autofreeze") else stages, stage.replace("-", "_"))(ns)
                except (Exception, SystemExit) as e:
                    os.environ["LAB_LAST_ERROR"] = str(e)
                    core.event(d, "campaign", f"{stage} failed", attempt=attempt, error=str(e))
                    if attempt == 3:
                        core.event(d, "campaign", f"stop: {stage} failed 3 times")
                        return
                else:
                    core.event(d, "campaign", f"{stage} completed", attempt=attempt)
                    from lab import kanban, replay
                    kanban.refresh()
                    replay.record(d, stage)
                    break
                finally:
                    os.environ.pop("LAB_STAGE_FALLBACK", None)
        finally:
            os.environ.pop("LAB_LAST_ERROR", None)
            if previous is not None:
                os.environ["LAB_LAST_ERROR"] = previous


FAKE = r'''import json, pathlib, sys
s = sys.stdin.read().split(chr(10)*2 + "---" + chr(10)*2, 1)[-1]
if s.startswith('TASK: replan'):
    pathlib.Path('candidate.py').write_text('x=1')
    print('FILES: evaluate.py candidate.py\nEVAL: python3 evaluate.py --split dev')
elif s.startswith('You are the independent adversarial design critic'):
    import os
    findings = [dict(id='design',severity='major',claim='fix',evidence='candidate',fix='revise')] if (
        '1: x=0' in s or os.environ.get('LAB_TEST_DIRTY')) else []
    print(json.dumps(dict(verdict='fix_first' if findings else 'freeze_ok', findings=findings)))
elif s.startswith('TASK: panel meta'):
    print(json.dumps(dict(decision='accept', surviving_major=[], dismissed=[])))
elif s.startswith('TASK: panel'):
    print(json.dumps(dict(soundness=3, significance=3, clarity=3, overall=6, confidence=4,
                         major=[], minor=[], decision='accept')))
elif s.startswith('TASK: paper'):
    pathlib.Path('paper.tex').write_text('Development only; no improvement.')
elif s.startswith('TASK: write'):
    print('Development only; no improvement.')
elif s.startswith('TASK: review'):
    print('VERDICT: holds')
elif s.startswith('TASK: fetch-data'):
    pathlib.Path('_plan.json').write_text(json.dumps(dict(needed=False, reason='toy')))
elif '## Produce exactly these files' in s:
    pathlib.Path('PROTOCOL.md').write_text('human freeze required')
else:
    print('IDEA: unchanged baseline')
'''


def selftest():
    from lab import stages, paper
    with tempfile.TemporaryDirectory() as t:
        root = Path(t)
        d, exp = root / "toy", root / "exp"
        d.mkdir(); exp.mkdir()
        fake = testkit.fake(root, FAKE)
        core.save_cfg(d, dict(id="toy", exp_dir=str(exp), provider=fake, reviewer=fake, critic=[fake, None], iters=1,
                              panel=[[fake, None, "methodologist"], [fake, None, "skeptic"]]))
        candidates = [dict(id=x, title=x, value=v, predictable=p) for x, v, p in
                      (("predictable", 99, True), ("prior", 98, False), ("C1", 3, False), ("C2", 2, False))]
        for name, value in (("candidates.json", candidates), ("novelty.json", {"prior": {"verdict": "prior_work"}}),
                            ("pick.json", candidates[2])):
            (d / name).write_text(json.dumps(value))
        (d / "brief.md").write_text("toy")
        (d / "field.md").write_text("toy field")
        for name in ("hypotheses.json", "discrimination.json"):  # covered by discriminate.selftest
            (d / name).write_text("{}")
        (d / "screen").mkdir()
        for k in (1, 2):
            (d / "screen" / f"S{k}.json").write_text("[]")
        (exp / "PROTOCOL.md").write_text("toy protocol")
        (exp / "x.txt").write_text("0")
        (exp / "eval.py").write_text('print(\'{"score":0,"pass":true}\')\n')
        core.rloop("init", exp, "--q", "Q1", "--eval", "python3 eval.py", "--frozen", "eval.py", "--mutable", "x.txt")
        a = Namespace(run=str(d), max_stages=20, wall_min=2, pivots=0, auto_pick=True)
        review, next_cmd, attempts = stages.review, director.next_cmd, []
        def flaky(ns):
            attempts.append(os.environ.get("LAB_LAST_ERROR"))
            if len(attempts) < 3:
                raise RuntimeError("temporary failure")
            review(ns)
        def linear(run):
            return next_cmd(run).replace("./lab.py explore", "./lab.py experiment")
        with patch.object(stages, "review", flaky), patch.object(director, "next_cmd", linear), \
                patch.object(paper, "_python", return_value=None), \
                patch.object(paper, "_compile", return_value=(None, "offline test")):
            campaign(a)
        assert attempts == [None, "temporary failure", "temporary failure"], attempts
        events = [json.loads(x) for x in (d / "events.jsonl").read_text().splitlines()]
        assert sum(e["msg"] == "review failed" for e in events) == 2
        assert director.next_cmd(d).startswith("DONE")
        assert not (exp / ".rloop/holdout_Q1.json").exists()
        a.pivots = 1
        campaign(a)
        sibling = root / "toy-p1"
        assert (sibling / "pick.json").exists() and json.loads((sibling / "pick.json").read_text())["id"] == "C2"
        assert (sibling / "field.md").read_text() == "toy field"
        assert not (root / "exp-p1/loop.json").exists()
        assert director.next_cmd(sibling).startswith(("USER:", "./lab.py critic")), director.next_cmd(sibling)
        (sibling / "pick.json").unlink()
        a.run, a.auto_pick = str(sibling), False
        campaign(a)
        assert not (sibling / "pick.json").exists()
        a.auto_pick, a.max_stages = True, 0
        campaign(a)
        assert not (sibling / "pick.json").exists()
        a.max_stages = 20
        with patch.object(director, "next_cmd", return_value="./lab.py review toy-p1"), \
                patch.object(stages, "review", side_effect=RuntimeError("permanent")) as fail, \
                patch.dict(os.environ, {"LAB_LAST_ERROR": "outer"}):
            campaign(a)
            assert fail.call_count == 3 and os.environ["LAB_LAST_ERROR"] == "outer"
        assert "failed 3 times" in (sibling / "events.jsonl").read_text()
        seen = []
        def third_time_lucky(ns):
            seen.append(os.environ.get("LAB_STAGE_FALLBACK"))
            if len(seen) < 3:
                raise RuntimeError("temporary")
        with patch.object(director, "next_cmd", side_effect=lambda *_: "./lab.py review toy-p1" if len(seen) < 3 else "USER: stop"), \
                patch.object(stages, "review", third_time_lucky), \
                patch.dict(os.environ, {"LAB_FALLBACK_PROVIDER": "cmd:other"}):  # failed twice: the third try runs on the other provider
            campaign(a)
        assert seen == [None, None, "1"] and "LAB_STAGE_FALLBACK" not in os.environ
        runs = [json.loads(x) for x in (sibling / "events.jsonl").read_text().splitlines()]
        assert [e.get("fallback") for e in runs if e["msg"] == "run review"][-3:] == [None, None, "cmd:other"]
        rows = [dict(chars_in=60, chars_out=60)] * 2
        (sibling / "calls.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
        a.max_chars = 300
        campaign(a)
        assert not any(e["msg"].startswith("budget exhausted") for e in map(json.loads, (sibling / "events.jsonl").read_text().splitlines()[len(runs):]))
        a.max_chars = 200
        campaign(a)
        last = json.loads((sibling / "events.jsonl").read_text().splitlines()[-1])
        assert last["msg"] == "budget exhausted (chars)" and last["max_chars"] == 200 and last["chars"] == 240, last
    print("campaign selftest ok")
