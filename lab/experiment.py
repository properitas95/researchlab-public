"""Stages plan / experiment / analyze: model drafts and proposes, rloop measures, rules decide."""
import hashlib
import json
import subprocess
import sys
import tempfile
import time
from argparse import Namespace
from pathlib import Path

from lab import core, roles, director, fetchdata, critic, rloop as rl

SKIP = {"results.tsv", "loop.json", "DECISIONS.md", "runs", ".rloop", "__pycache__", ".lab_last_message.md"}


def _hashes(exp, mutable, skip=SKIP):
    return {str(p.relative_to(exp)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(exp.rglob("*")) if p.is_file()
            and not set(p.relative_to(exp).parts) & set(skip) and str(p.relative_to(exp)) not in mutable}


def _memory(exp):
    f = exp / "memory.md"
    return "\n".join(f.read_text().splitlines()[-40:]) if f.exists() else ""


def _remember(exp, row, parent=None, kind="baseline", reply=""):
    delta = f"{float(row['score']) - float(parent['score']):+.6g}" if parent and parent['score'] and row['score'] else "n/a"
    idea = row["desc"]
    prefix = f"[{kind} from #{parent['n'] if parent else '-'}]"
    if idea.startswith(prefix):
        idea = idea[len(prefix):].strip()
    with (exp / "memory.md").open("a") as fh:
        fh.write(f"#{row['n']} {prefix} {idea} -> {row['status']} {row['score'] or 'n/a'} (Δ {delta} vs parent)\n")
        for ln in reply.splitlines():
            if ln.startswith("LESSON:"):
                fh.write(ln + "\n")


def _ctx(a):
    d = core.run_dir(a.run)
    c = core.cfg(d)
    return d, c, Path(c["exp_dir"]), c["q"]


def plan(a):
    d, c, exp, _ = _ctx(a)
    fetchdata.materialize(d, exp)  # data sits inside the experiment folder, so it can be frozen and guarded
    instr = core.prompt("plan", brief=(d / "brief.md").read_text(), pick=(d / "pick.json").read_text(), data=fetchdata.plan_input(d), guard=critic.design_notes(d))
    (d / "plan_reply.md").write_text(roles.call("plan", instr, cwd=exp, run=d, write=True))
    if not (exp / "PROTOCOL.md").exists():
        raise RuntimeError(f"model did not write {exp}/PROTOCOL.md")
    core.event(d, "plan", f"drafted {exp}/PROTOCOL.md; review it, then `lab.py freeze`")


def experiment(a):
    d, c, exp, q = _ctx(a)
    iters, end = a.iters or c["iters"], time.time() + (a.wall_min or c["wall_min"]) * 60
    cfg = json.loads((exp / "loop.json").read_text())[q]
    mutable = cfg["mutable"]
    tag = f'{c["provider"]}-{core.prompt_hash("propose")}'  # editing propose.md = new procedure
    run = lambda *x: core.rloop("run", exp, "--q", q, *x, "--procedure", tag, check=False)  # noqa: E731
    n, reason = 0, "cap: iterations"
    if not [r for r in rl.rows(exp) if r["q"] == q]:
        print(run("-m", "baseline"))
        rows = [r for r in rl.rows(exp) if r["q"] == q]
        if rows:
            _remember(exp, rows[-1])
    problem = director.anchor_problem(c, [r for r in rl.rows(exp) if r["q"] == q])
    if problem:  # no search on a setup that does not reproduce its anchor
        core.event(d, "experiment", f"STOP: {problem}; 0 trials")
        return
    while n < iters:
        if time.time() > end:
            reason = "cap: wall clock"
            break
        parent = rl.best(exp, q, cfg["direction"])
        previous = len(rl.rows(exp))
        tsv = exp / "results.tsv"
        code = "\n".join(f"### {p}\n```\n{(exp / p).read_text()}\n```" for p in mutable)
        instr = core.prompt("propose", protocol=(exp / "PROTOCOL.md").read_text(),
                            status=core.rloop("status", exp, check=False),
                            recent="\n".join(tsv.read_text().splitlines()[-12:]) if tsv.exists() else "",
                            mutable=", ".join(mutable), code=code, memory=_memory(exp))
        before = _hashes(exp, mutable)
        try:
            reply = roles.call("experiment", instr, cwd=exp, run=d, write=True)
        except (RuntimeError, subprocess.TimeoutExpired) as e:
            reason = f"error: {e}"
            break
        after = _hashes(exp, mutable)
        bad = sorted(k for k in before.keys() | after.keys() if before.get(k) != after.get(k))
        if bad:  # cannot undo generically, so stop instead of scoring a tampered setup
            reason = f"STOP: proposer touched non-mutable files: {', '.join(bad)}"
            break
        n += 1
        ideas = [ln[5:].strip() for ln in reply.splitlines() if ln.startswith("IDEA:")]
        out = run("-m", ideas[-1] if ideas else reply.strip()[:100])
        print(out)
        rows = rl.rows(exp)
        if len(rows) > previous:
            _remember(exp, rows[-1], parent, "improve", reply)
        if " keep " not in out:
            core.rloop("revert", exp, "--q", q, check=False)  # next proposal starts from the best
        if "STOP:" in out:
            reason = next(ln for ln in out.splitlines() if "STOP:" in ln)
            break
    core.event(d, "experiment", f"{reason}; {n} trials")


def _effect(data, baseline, min_delta, sign=1):
    """Pure verdict on evaluator output; absent interval endpoints stay unknown."""
    gain = 1 - data["ratio"] if "ratio" in data and sign == 1 else sign * (float(data["score"]) - baseline)
    low = 1 - data["bootstrap_upper95"] if "bootstrap_upper95" in data else None
    high = 1 - data["bootstrap_lower95"] if "bootstrap_lower95" in data else None
    if low is None and high is None and "se" in data:
        low, high = gain - 2 * data["se"], gain + 2 * data["se"]
    periods = data.get("period_ratios", [])
    if low is None and high is None:
        fine = "supported" if gain > min_delta else "no_improvement"
        why = f"구간 없음; 효과 {gain:+.4f}, min_delta {min_delta:g}"
    elif low is not None and low > 0:
        fine = ("large" if gain >= 2 * min_delta and periods and all(x < 1 for x in periods)
                else "supported" if gain >= min_delta else "small")
        why = f"구간 하한 {low:+.4f} > 0; 효과 {'<' if gain < min_delta else '>='} min_delta {min_delta:g}"
    elif high is not None and high < 0:
        fine, why = "worse", f"구간 상한 {high:+.4f} < 0"
    elif gain < 0 and high is None and periods and all(x > 1 for x in periods):
        # An upper ratio bound cannot prove harm; report the observed period evidence separately.
        fine, why = "worse", f"{len(periods)}개 기간 모두 악화; 구간으로 악화 확정 불가"
    elif low is not None and high is not None and low <= 0 <= min_delta <= high:
        fine, why = "inconclusive", f"구간이 0과 min_delta {min_delta:g}를 모두 포함"
    elif low is not None and high is not None and -min_delta <= low <= high <= min_delta:
        fine, why = "no_effect", f"구간 [{low:+.4f}, {high:+.4f}]이 ±min_delta {min_delta:g} 안"
    else:
        fine, why = "inconclusive", "구간이 개선·동등성·악화를 확정하지 못함"
    return dict(fine=fine, effect=gain, low=low, high=high, reason=why)


def analyze(a):
    d, c, exp, q = _ctx(a)
    lc = json.loads((exp / "loop.json").read_text())[q]
    lock = exp / ".rloop" / f"holdout_{q}.json"
    sign = 1 if lc["direction"] == "max" else -1

    def evidence(row, baseline):
        data = {"score": float(row["score"])}
        log = exp / "runs" / f"{row['n']}.log"
        for line in log.read_text().splitlines() if log.exists() else []:
            try:
                value = json.loads(line)
            except ValueError:
                continue
            if isinstance(value, dict):
                data.update(value)
        return dict(n=row["n"], status=row["status"], evaluator=data,
                    **_effect(data, baseline, lc["min_delta"], sign))

    trials = [r for r in rl.rows(exp) if r["q"] == q and r["status"] in ("keep", "discard")
              and r["score"] != "" and not r["desc"].startswith("holdout of #")]
    base = next((r for r in trials if r["status"] == "keep"), None)
    candidates = [evidence(r, float(base["score"])) for r in trials if base and r["n"] != base["n"]]
    best = max(candidates, key=lambda r: r["effect"], default=None)
    worst = min(candidates, key=lambda r: r["effect"], default=None)
    worst = worst if worst and worst["fine"] == "worse" else None
    chosen, tag, note = best, "dev", ""
    if best and best["fine"] in ("small", "supported", "large") and not lock.exists():
        kept = rl.best(exp, q, lc["direction"])
        if not kept or kept["n"] != best["n"]:
            note = ("holdout eligible but best trial was not kept; rerun it with --min-delta lowered "
                    "under a new question id to confirm")
        elif lc["holdout"]:
            core.rloop("revert", exp, "--q", q)
            print(core.rloop("holdout", exp, "--q", q, check=False))
            if not lock.exists():
                note = "holdout failed before opening; dev evidence only"
        else:
            note = "no holdout: dev evidence only"
    if best and lock.exists():
        lk = json.loads(lock.read_text())
        h = {k: next((r for r in rl.rows(exp) if r["q"] == q and r["status"] == "holdout"
                     and r["desc"].startswith(f"holdout of #{lk.get(k)} ") and r["score"]), None)
             for k in ("best", "base")}
        if lk.get("best") == best["n"] and lk.get("base") == base["n"] and all(h.values()):
            chosen = evidence(h["best"], float(h["base"]["score"])) | {"n": best["n"]}
            best["holdout"], tag = chosen, "confirmed"
        else:
            chosen = best | {"fine": "inconclusive", "reason": "holdout 점수 누락 또는 최고안 불일치"}
            note = "holdout already opened; never reopen"
    fine = chosen["fine"] if chosen else "no_improvement" if base else "invalid"
    coarse = {"large": "supported_within_scope", "supported": "supported_within_scope",
              "small": "inconclusive", "no_effect": "no_improvement", "worse": "no_improvement"}.get(fine, fine)
    why = (f"[{fine}·{tag}] #{chosen['n']} {chosen['effect']:+.4f} ({chosen['reason']})" if chosen
           else f"[{fine}·dev] 유효한 비기준선 시행 없음")
    if worst and (not chosen or worst["n"] != chosen["n"] or tag != "dev"):
        why += f"; [worse·dev] #{worst['n']} {worst['effect']:+.4f} ({worst['reason']})"
    if note:
        why += "; " + note
    verdict = dict(fine=fine, coarse=coarse, tag=tag, best=best, worst=worst, message=why)
    if note:
        verdict["holdout_note"] = note
    (d / "verdict.json").write_text(json.dumps(verdict, ensure_ascii=False, indent=1) + "\n")
    core.rloop("decide", exp, "--q", q, "--verdict", coarse, "-m", why)
    core.event(d, "analyze", why)


def selftest():
    py = sys.executable
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        def fake(name, body):
            (t / name).write_text(body)
            return f"cmd:{py} {t / name}"

        good = fake("good.py", f"""import sys,pathlib
s=sys.stdin.read()
if 'IDEA:' not in s:
    pathlib.Path('PROTOCOL.md').write_text('# q\\nmin_delta 0\\n')
    pathlib.Path('x.txt').write_text('0')
    pathlib.Path('eval.py').write_text('import json,sys\\nx=float(open("x.txt").read())\\nt=2.5 if sys.argv[-1]=="holdout" else 3\\nprint(json.dumps({{"score":-(x-t)**2,"pass":True}}))\\n')
else:
    c=pathlib.Path({str(t / "cnt")!r}); i=int(c.read_text()) if c.exists() else 0; c.write_text(str(i+1))
    v=[2,1,2.5,3][i]; pathlib.Path('x.txt').write_text(str(v)); print('LESSON: try a new value\\nIDEA: set x=%s' % v)
""")
        evil = fake("evil.py", "import pathlib;pathlib.Path('eval.py').write_text('#');print('IDEA: cheat')\n")

        def mk(name, prov):
            d = t / name
            d.mkdir()
            for f, body in (("brief.md", "b\n"), ("candidates.json", "[]"), ("novelty.json", "[]"),
                            ("pick.json", '{"title": "t"}')):
                (d / f).write_text(body)
            (d / "exp").mkdir()
            core.save_cfg(d, {"id": name, "exp_dir": str(d / "exp"), "provider": prov, "q": "Q1"})
            ns = Namespace(run=str(d), iters=4, wall_min=None)
            plan(ns)
            core.rloop("init", d / "exp", "--q", "Q1", "--eval", f"{py} eval.py dev", "--frozen", "eval.py", "--mutable", "x.txt")
            return d, ns

        d, ns = mk("r1", good)
        experiment(ns)
        analyze(ns)
        dec = (d / "exp" / "DECISIONS.md").read_text()
        assert " Q1 **supported_within_scope**" in dec, dec
        (t / "memory.md").write_text("\n".join(map(str, range(50))))
        assert _memory(t).splitlines() == list(map(str, range(10, 50)))
        mem = (d / "exp" / "memory.md").read_text()
        assert "#3 [improve from #2]" in mem and "LESSON: try a new value" in mem, mem
        assert director.next_cmd(d).startswith("./lab.py ablate"), director.next_cmd(d)
        d2, ns2 = mk("r2", good)
        core.save_cfg(d2, core.cfg(d2) | {"provider": evil})
        experiment(ns2)
        assert "touched non-mutable" in (d2 / "events.jsonl").read_text().splitlines()[-1]
        def toy(name, values, fine, holdout=False):
            d, e = t / name, t / name / "exp"
            e.mkdir(parents=True)
            core.save_cfg(d, {"id": name, "exp_dir": str(e), "provider": good, "q": "Q1"})
            (e / "x.json").write_text('{"score": 0}')
            (e / "eval.py").write_text(
                "import json\nx=json.load(open('x.json'))\n"
                "print(json.dumps({k:v for k,v in x.items() if k!='score'}))\n"
                "print(json.dumps({'score':x['score'],'pass':True}))\n")
            args = ["--holdout", f"{py} eval.py"] if holdout else []
            core.rloop("init", e, "--q", "Q1", "--eval", f"{py} eval.py", "--frozen", "eval.py",
                       "--mutable", "x.json", "--min-delta", ".03", *args)
            core.rloop("run", e, "--q", "Q1", "-m", "baseline")
            for value in values:
                (e / "x.json").write_text(json.dumps(value))
                core.rloop("run", e, "--q", "Q1", "-m", "candidate")
            ns = Namespace(run=str(d))
            analyze(ns)
            v = json.loads((d / "verdict.json").read_text())
            assert v["fine"] == fine, v
            before = (e / "results.tsv").read_bytes()
            analyze(ns)
            assert (e / "results.tsv").read_bytes() == before
            assert json.loads((d / "verdict.json").read_text()) == v
            return v, e

        small = dict(score=.0225511, ratio=.9774489, bootstrap_upper95=.995353,
                     period_ratios=[.98, .99, .97])
        harm = dict(score=-.0259471, ratio=1.0259471, bootstrap_upper95=1.0461,
                    period_ratios=[1.0347, 1.0183, 1.0259])
        v, e = toy("small", [harm, small], "small", True)
        assert v["coarse"] == "inconclusive" and v["best"]["n"] == "3" and v["worst"]["n"] == "2"
        assert abs(v["best"]["low"] - .004647) < 1e-9 and v["best"]["high"] is None
        assert "best trial was not kept" in v["holdout_note"] and not (e / ".rloop/holdout_Q1.json").exists()
        v, _ = toy("harm", [harm], "worse")
        assert v["coarse"] == "no_improvement" and "3개 기간 모두 악화" in v["message"]
        v, _ = toy("no_interval", [dict(score=.02)], "no_improvement")
        assert v["best"]["low"] is None and v["tag"] == "dev"
        large = dict(score=.08, ratio=.92, bootstrap_upper95=.95, bootstrap_lower95=.89,
                     period_ratios=[.91, .92, .93])
        v, e = toy("large", [large], "large", True)
        assert v["tag"] == "confirmed" and v["coarse"] == "supported_within_scope"
        assert len([r for r in rl.rows(e) if r["status"] == "holdout"]) == 2
        for data, fine in ((dict(score=.04, se=.005), "supported"),
                           (dict(score=.01, se=.02), "inconclusive"),
                           (dict(score=0, se=.005), "no_effect"),
                           (dict(score=-.04, se=.005), "worse")):
            assert _effect(data, 0, .03)["fine"] == fine
        assert _effect(dict(score=0, se=.015), 0, .03)["fine"] == "inconclusive"
        assert _effect(small | {"se": 1}, 0, .03)["fine"] == "small"
        assert _effect(large | {"period_ratios": [.9, 1.1]}, 0, .03)["fine"] == "supported"
        assert _effect(harm | {"bootstrap_lower95": 1.01}, 0, .03)["high"] < 0
        assert _effect(dict(score=9.9, se=.01), 10, .03, -1)["fine"] == "supported"
    print("experiment selftest ok")
