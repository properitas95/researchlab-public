"""Tree-search stage (AI-Scientist-v2 style): every rloop trial is a node; drafts, improves of top-3 nodes, debugs of failures."""
import json
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time
from argparse import Namespace
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from lab import core, roles, director, rloop as rl
from lab.experiment import _ctx, _hashes, _memory, _remember


def _g(a, k, default):
    v = getattr(a, k, None)
    return default if v is None else v


def _load(exp):
    f = exp / "tree.jsonl"
    return [json.loads(x) for x in f.read_text().splitlines() if x] if f.exists() else []


def _add(exp, nodes, n, parent, kind):
    r = next(r for r in rl.rows(exp) if int(r["n"]) == n)
    nodes.append({"n": n, "parent": parent, "kind": kind, "score": float(r["score"]) if r["score"] else None,
                  "status": r["status"]})
    with (exp / "tree.jsonl").open("a") as fh:
        fh.write(json.dumps(nodes[-1]) + "\n")


def _trial(exp, q, tag, desc):
    out = core.rloop("run", exp, "--q", q, "-m", desc, "--procedure", tag, check=False)
    m = re.search(r"^#(\d+) \S+ (\w+) score=", out, re.M)
    return out, int(m[1]) if m else None


def _depth(by, node):  # consecutive debug nodes ending at node
    k = 0
    while node and node["kind"] == "debug":
        k, node = k + 1, by.get(node["parent"])
    return k


def _pick(nodes, a, sign, rng, used=(), drafted=0):
    by = {x["n"]: x for x in nodes}
    root = nodes[0]["n"]
    if sum(x["parent"] is not None and x["kind"] == "draft" for x in nodes) + drafted < _g(a, "drafts", 3):
        return "draft", root
    kids = {x["parent"] for x in nodes}
    bad = [x for x in nodes if x["status"] in ("crash", "invalid") and x["n"] not in kids
           and _depth(by, x) < _g(a, "debug_depth", 3)]
    ok = sorted((x for x in nodes if x["status"] in ("keep", "discard") and x["score"] is not None),
                key=lambda x: -sign * x["score"])[:3]
    fresh_bad, fresh_ok = [x for x in bad if x["n"] not in used], [x for x in ok if x["n"] not in used]
    bad = fresh_bad if fresh_bad or fresh_ok else bad
    if bad and (not fresh_ok or rng.random() < 0.5):
        return "debug", max(bad, key=lambda x: x["n"])["n"]
    choices = [(x, w) for x, w in zip(ok, [3, 2, 1]) if x["n"] not in used]
    if not choices:
        choices = list(zip(ok, [3, 2, 1]))
    if not ok:
        return "draft", root
    return "improve", rng.choices([x for x, _ in choices], weights=[w for _, w in choices])[0]["n"]


def _propose(exp, work, cfg, nodes, kind, par, run):
    mutable = cfg["mutable"]
    by, rows = {x["n"]: x for x in nodes}, {int(r["n"]): r for r in rl.rows(exp)}
    chain, p = [], par
    while p is not None:
        chain.append(p)
        p = by[p]["parent"]
    branch = "\n".join(f'#{i} {by[i]["kind"]} {by[i]["score"]} {rows[i]["desc"]}' for i in reversed(chain))
    status = core.rloop("status", exp, check=False)
    if kind == "debug":
        log = exp / "runs" / f"{par}.log"
        status += f"\nLog tail of failed #{par}:\n" + "\n".join(log.read_text().splitlines()[-15:]) if log.exists() else ""
    for m in mutable:
        shutil.copy2(exp / "runs" / str(par) / m, work / m)
    code = "\n".join(f"### {m}\n```\n{(work / m).read_text()}\n```" for m in mutable)
    instr = core.prompt("explore", protocol=(exp / "PROTOCOL.md").read_text(), status=status, branch=branch,
                        action=kind, mutable=", ".join(mutable), code=code, memory=_memory(exp))
    before = _hashes(work, mutable, **({"skip": ()} if work != exp else {}))
    reply = roles.call("explore", instr, cwd=work, run=run, write=True)
    return reply, before


def explore(a):
    d, c, exp, q = _ctx(a)
    iters, end = _g(a, "iters", c["iters"]), time.time() + _g(a, "wall_min", c["wall_min"]) * 60
    cfg = json.loads((exp / "loop.json").read_text())[q]
    mutable, sign = cfg["mutable"], 1 if cfg["direction"] == "max" else -1
    tag = f'{c["provider"]}-tree-{core.prompt_hash("explore")}'
    nodes, n, reason = _load(exp), 0, "cap: iterations"
    if not nodes:
        mine = [r for r in rl.rows(exp) if r["q"] == q]
        if not mine:
            print(_trial(exp, q, tag, "baseline")[0])
            mine = [r for r in rl.rows(exp) if r["q"] == q]
            if mine:
                _remember(exp, mine[-1], kind="draft")
        if not mine:
            raise RuntimeError("baseline trial failed")
        _add(exp, nodes, int(mine[0]["n"]), None, "draft")
    problem = director.anchor_problem(c, [r for r in rl.rows(exp) if r["q"] == q])
    if problem:  # no search on a setup that does not reproduce its anchor
        core.event(d, "experiment", f"STOP: {problem}; 0 trials")
        return
    while n < iters:
        if time.time() > end:
            reason = "cap: wall clock"
            break
        count = min(max(1, _g(a, "workers", 1)), iters - n, core.slots())  # free memory caps parallel proposers
        targets, used, drafted = [], set(), 0
        rng = random.Random(_g(a, "seed", 0) + len(nodes))
        for _ in range(count):
            kind, par = _pick(nodes, a, sign, rng, used, drafted)
            targets.append((kind, par)); used.add(par)
            drafted += kind == "draft"
        try:
            with tempfile.TemporaryDirectory(prefix="explore-") as tmp, ThreadPoolExecutor(max_workers=count) as pool:
                jobs = []
                for i, (kind, par) in enumerate(targets):
                    work = Path(tmp) / str(i) if _g(a, "workers", 1) > 1 else exp
                    if work != exp:
                        shutil.copytree(exp, work, ignore=shutil.ignore_patterns("runs", ".rloop", "memory.md"))
                    jobs.append((work, pool.submit(_propose, exp, work, cfg, nodes, kind, par, d)))
                # All proposers finish before any scoring mutates the source experiment.
                replies = [job.result() for _, job in jobs]
                for (kind, par), (work, _), (reply, before) in zip(targets, jobs, replies):
                    if time.time() > end:
                        reason = "cap: wall clock"
                        break
                    after = _hashes(work, mutable, **({"skip": ()} if work != exp else {}))
                    bad = sorted(k for k in before.keys() | after.keys() if before.get(k) != after.get(k))
                    if bad:
                        reason = f"STOP: proposer touched non-mutable files: {', '.join(bad)}"
                        break
                    if work != exp:
                        for m in mutable:
                            shutil.copy2(work / m, exp / m)
                    ideas = [ln[5:].strip() for ln in reply.splitlines() if ln.startswith("IDEA:")]
                    out, num = _trial(exp, q, tag, f"[{kind} from #{par}] {ideas[-1] if ideas else reply.strip()[:100]}")
                    print(out)
                    if num is None:
                        reason = f"error: rloop run failed: {out.strip()[:200]}"
                        break
                    n += 1
                    _add(exp, nodes, num, par, kind)
                    rows = {int(r["n"]): r for r in rl.rows(exp)}
                    _remember(exp, rows[num], rows[par], kind, reply)
                    core.rloop("revert", exp, "--q", q, check=False)
                    if "STOP: 3 crashes" in out:
                        reason = next(ln for ln in out.splitlines() if "STOP: 3 crashes" in ln)
                        break
        except (RuntimeError, subprocess.TimeoutExpired, OSError) as e:
            reason = f"error: {e}"
        if reason != "cap: iterations":
            break
    core.rloop("revert", exp, "--q", q, check=False)
    core.event(d, "experiment", f"{reason}; {n} trials (tree)")


def selftest():
    py = sys.executable
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        (t / "fake.py").write_text(f"""import sys,re,pathlib
s=sys.stdin.read(); k=re.search(r'Your action: (\\w+)',s)[1]
x=float(re.search(r'```\\n(\\S+)\\n```',s.split('Code to start from')[1])[1]) if k!='debug' else 0
c=pathlib.Path({str(t / "cnt")!r}); i=int(c.read_text()) if c.exists() else 0; c.write_text(str(i+1))
v={{'draft':[2,'garbage',1]}}.get(k,[0])[i%3] if k=='draft' and i<3 else x+1
if k=='debug': v=4
pathlib.Path('x.txt').write_text(str(v)); print('IDEA: %s x=%s' % (k,v))
""")
        (t / "evil.py").write_text("import pathlib;pathlib.Path('eval.py').write_text('#');print('IDEA: cheat')\n")
        d, e = t / "r", t / "r" / "exp"
        e.mkdir(parents=True)
        for f, body in (("brief.md", "b\n"), ("exp/PROTOCOL.md", "# q\n"), ("exp/x.txt", "0"), ("exp/eval.py",
                        "import json\nx=float(open('x.txt').read())\nprint(json.dumps({'score':-(x-5)**2,'pass':True}))\n")):
            (d / f).write_text(body)
        core.save_cfg(d, {"id": "r", "exp_dir": str(e), "provider": f"cmd:{py} {t / 'fake.py'}", "q": "Q1"})
        core.rloop("init", e, "--q", "Q1", "--eval", f"{py} eval.py", "--frozen", "eval.py", "--mutable", "x.txt",
                   "--stop-after", "99")
        ns = Namespace(run=str(d), iters=12, wall_min=None, drafts=3, debug_depth=3, seed=0)
        explore(ns)
        nodes = _load(e)
        kinds = {x["kind"] for x in nodes}
        assert {"draft", "improve", "debug"} <= kinds, [(x["n"], x["kind"], x["status"]) for x in nodes]
        ids = {x["n"] for x in nodes}
        assert all(x["parent"] in ids for x in nodes[1:]) and nodes[0]["parent"] is None
        keeps = [r for r in rl.rows(e) if r["status"] == "keep"]
        b = max(keeps, key=lambda r: float(r["score"]))
        assert (e / "x.txt").read_text() == (e / "runs" / b["n"] / "x.txt").read_text()
        (t / "parallel.py").write_text(f"""import sys,pathlib,time
s=sys.stdin.read(); assert 'Optimizer memory' in s and '{{{{memory}}}}' not in s
assert not pathlib.Path('runs').exists() and not pathlib.Path('memory.md').exists()
marks=pathlib.Path({str(t / 'marks')!r}); marks.mkdir(exist_ok=True)
(marks/pathlib.Path.cwd().name).touch(); end=time.monotonic()+5
while len(list(marks.iterdir()))<3 and time.monotonic()<end: time.sleep(.01)
assert len(list(marks.iterdir()))==3, 'proposers were not concurrent'
p=pathlib.Path('x.txt'); p.write_text(str(float(p.read_text())+1))
print('LESSON: parent plus one\\nIDEA: parallel increment')
""")
        core.save_cfg(d, core.cfg(d) | {"provider": f"cmd:{py} {t / 'parallel.py'}"})
        start = len(nodes)
        slots0, core.slots = core.slots, lambda *_, **__: 8  # deterministic test regardless of free memory
        explore(Namespace(run=str(d), iters=4, workers=3, drafts=3))
        batch = _load(e)[start:]
        assert len(batch) == 4 and len({x["parent"] for x in batch[:3]}) == 3, batch
        assert len({x["n"] for x in batch}) == 4
        for node in batch:
            val = lambda n: float((e / "runs" / str(n) / "x.txt").read_text())
            assert val(node["n"]) == val(node["parent"]) + 1, node
        assert "LESSON: parent plus one" in _memory(e)
        core.save_cfg(d, core.cfg(d) | {"provider": f"cmd:{py} {t / 'evil.py'}"})
        before = (e / "eval.py").read_bytes(), len(rl.rows(e))
        explore(Namespace(run=str(d), iters=2, workers=3))
        core.slots = slots0
        assert before == ((e / "eval.py").read_bytes(), len(rl.rows(e)))
        assert "touched non-mutable" in (d / "events.jsonl").read_text().splitlines()[-1]
    print("tree selftest ok")
