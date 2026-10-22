"""predict: score ideas by predicted chance of a supported result, learned online from the lab's verdicts; pick with discounted Thompson sampling. No model calls.

Why forgetting: discounted / sliding-window Thompson sampling matches the regret lower bound up to log factors in piecewise-stationary
bandits (Qi, Guo, Zhu 2025, Entropy 27(1):51, doi 10.3390/e27010051). In nonstationary bandits regret scales with the entropy rate of the
optimal-action sequence, and forgetting is how a sampler tracks that drift (Min & Russo 2023, arXiv:2302.04452). So gamma should follow how
often the best direction changes; default 0.9 per observation, env LAB_TS_GAMMA.
"""
import json
import os
import random
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from lab import core, util

STOP = set("""that this with from have been were than then into over under also each such only more most some other which their there these those
about after before between during while would could should will shall using used uses based both either does done same very when where whose what""".split())
PRIOR = 2  # the candidate's own p_success counts as this many observations
HALF = {"small", "inconclusive"}


def _root(root):
    return Path(root) if root else core.ROOT


def gamma():
    try:
        return min(1.0, max(0.0, float(os.environ.get("LAB_TS_GAMMA", 0.9))))
    except ValueError:
        return 0.9


def load_ledger(root=None):
    return util.lines(_root(root) / "methods" / "ledger.jsonl")


def load_concepts(root=None):
    """Lowercase method/dataset/metric names from every paper's concept graph."""
    names = set()
    for p in sorted((_root(root) / "helix").glob("*/concepts.jsonl")):
        names |= {str(c.get("name", "")).lower() for c in util.lines(p) if c.get("type") in ("method", "dataset", "metric")}
    return {n for n in names if len(n) >= 4}


def tokens(text):
    return {w for w in re.findall(r"[^\W\d_]{4,}", str(text).lower()) if w not in STOP}


def features(c, combos=None):
    ks = tokens(f"{c.get('title', '')} {c.get('hypothesis', '')}")
    combo = next((k for k in combos or [] if c.get("seed") and k.get("id") == c["seed"]), None)
    for f in ("method", "dataset", "metric"):
        if combo and combo.get(f):
            ks.add("c:" + str(combo[f]).lower())
    return ks


def _keys(text, concepts):
    low = str(text).lower()
    return tokens(low) | {"c:" + n for n in concepts if n in low}


def _outcome(verdict):
    v = str(verdict).lower()
    if v in HALF or any(w in v for w in HALF):
        return 0.5
    if any(w in v for w in ("no_improvement", "no_effect", "worse", "refuted")):
        return 0.0
    return 1.0 if "supported" in v or "large" in v else None


def posterior(ledger, concepts=(), g=None):
    """key -> (alpha, beta), prior (1, 1); rows in file order; every row first pulls all keys towards the prior by g."""
    g = gamma() if g is None else g
    post, texts = {}, {}
    for r in ledger:
        if r.get("kind") == "trial" and r.get("method") != "baseline":
            texts.setdefault((r.get("run"), r.get("q")), []).append(str(r.get("method", "")))
    for r in ledger:
        if r.get("kind") == "trial" and r.get("method") != "baseline":
            d = r.get("delta")
            y, w, text = float(r.get("status") in ("keep", "holdout") and isinstance(d, (int, float)) and d > 0), 1, r.get("method", "")
        elif r.get("kind") == "verdict" and _outcome(r.get("verdict")) is not None:
            y, w = _outcome(r["verdict"]), 3
            text = " ".join(texts.get((r.get("run"), r.get("q")), [])) or r.get("reason", "")
        else:
            continue
        for k, (a, b) in post.items():
            post[k] = (1 + g * (a - 1), 1 + g * (b - 1))
        for k in _keys(text, concepts):
            a, b = post.get(k, (1.0, 1.0))
            post[k] = (a + w * y, b + w * (1 - y))
    return post


def _blend(c, post, combos):
    """(alpha, beta, p, evidence): matched key posteriors averaged by evidence, plus the self-reported p_success as PRIOR observations."""
    hit = [(a - 1 + b - 1, a / (a + b)) for k in features(c, combos) for a, b in [post.get(k, (1, 1))] if a + b > 2]
    s = sum(e for e, _ in hit)
    pp = sum(e * m for e, m in hit) / s if s else 0.0
    ev = sum(e * e for e, _ in hit) / s if s else 0.0
    ps = c.get("p_success", 0.5)
    a, b = PRIOR * ps + ev * pp, PRIOR * (1 - ps) + ev * (1 - pp)
    return a, b, (a / (a + b) if a + b else ps), ev


def predict(c, post, combos=None):
    _, _, p, ev = _blend(c, post, combos)
    return p, ev


def sample(c, post, rng, combos=None):
    a, b, _, _ = _blend(c, post, combos)
    return rng.betavariate(max(a, 1e-3), max(b, 1e-3))


def choose(cands, post, seed=None, combos=None):
    """Candidate maximising impact * draw * soundness/5; returns (choice, {id: draw})."""
    rng = random.Random(seed)
    draws = {c["id"]: sample(c, post, rng, combos) for c in cands}
    return max(cands, key=lambda c: c.get("impact", 1) * draws[c["id"]] * c.get("soundness", 5) / 5), draws


def _log(root=None):
    return _root(root) / "predictions.jsonl"


def _append(row, root=None):
    with _log(root).open("a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(dict(row, utc=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")), ensure_ascii=False) + "\n")


def record(run_dir, candidate_id, p, self_p=None, root=None):
    _append({"type": "pred", "run": Path(run_dir).name, "id": candidate_id, "p": round(p, 4), "self": self_p}, root)


def resolve(run_dir, verdict, root=None):
    """Append the outcome of the run's latest prediction for its picked candidate (1 supported, 0 failed, 0.5 inconclusive)."""
    d, y = Path(run_dir), _outcome(verdict)
    pick = util.load(d / "pick.json")
    pred = [r for r in util.lines(_log(root)) if r.get("type") == "pred" and r.get("run") == d.name and r.get("id") == pick.get("id")]
    if pred and y is not None:
        _append({"type": "outcome", "run": d.name, "id": pick["id"], "y": y, "p": pred[-1]["p"], "self": pred[-1].get("self")}, root)


def brier(log):
    """(model MSE, self-reported p_success baseline MSE, n) over resolved predictions; None when nothing is resolved."""
    r = [x for x in log if x.get("type") == "outcome" and isinstance(x.get("self"), (int, float))]
    if not r:
        return None, None, 0
    return (sum((x["p"] - x["y"]) ** 2 for x in r) / len(r), sum((x["self"] - x["y"]) ** 2 for x in r) / len(r), len(r))


def predict_cmd(a):
    d = core.run_dir(a.run)
    cands, combos = util.load(d / "candidates.json", []), util.load(d / "combos.json", [])
    post, rng = posterior(load_ledger(), load_concepts()), random.Random(getattr(a, "seed", None))
    print(f"{'id':<5}{'self':>6}{'pred':>7}{'evid':>7}{'draw':>7}  title")
    for c in cands:
        p, ev = predict(c, post, combos)
        print(f"{c['id']:<5}{c.get('p_success', 0):>6.2f}{p:>7.2f}{ev:>7.1f}{sample(c, post, rng, combos):>7.2f}  {c['title'][:60]}")


def selftest():
    def row(m, ok, i):
        return {"kind": "trial", "run": "t", "q": "Q", "n": i, "method": m, "status": "keep" if ok else "discard", "delta": 0.1 if ok else -0.1}
    rank, shr = "Use rank correlation of returns", "Apply shrinkage estimator to covariance"
    led = []
    for i in range(12):
        led += [row(rank, i == 0, 2 * i), row(shr, i != 0, 2 * i + 1)]
    post = posterior(led)
    ma, ms = (lambda a, b: a / (a + b))(*post["rank"]), (lambda a, b: a / (a + b))(*post["shrinkage"])
    assert ma < 0.4 < 0.6 < ms, (ma, ms)
    # forgetting: identical counts, older evidence sits closer to the prior
    fill = [row("unrelated filler words", True, i) for i in range(10)]
    fails = [row("zebra stripes", False, i) for i in range(5)]
    old, new = posterior(fails + fill)["zebra"], posterior(fill + fails)["zebra"]
    assert old[1] < new[1], (old, new)
    # no evidence -> self prior; verdict weighs 3
    c0 = {"id": "X", "title": "quantum teleportation", "hypothesis": "nothing known", "p_success": 0.3}
    assert predict(c0, post) == (0.3, 0.0), predict(c0, post)
    v = posterior([row("alpha beta", True, 0), {"kind": "verdict", "run": "t", "q": "Q", "verdict": "supported"}], g=1.0)["alpha"]
    assert v == (5.0, 1.0), v
    cb = {"id": "B", "title": "rank correlation", "hypothesis": "rank", "p_success": 0.5, "impact": 4, "soundness": 4}
    cs = {"id": "S", "title": "shrinkage estimator", "hypothesis": "shrinkage", "p_success": 0.5, "impact": 4, "soundness": 4}
    assert predict(cs, post)[0] > predict(cb, post)[0] and predict(cs, post)[1] > 0
    wins = sum(choose([cb, cs], post, s)[0]["id"] == "S" for s in range(200))
    assert wins > 140, wins
    with tempfile.TemporaryDirectory() as t:
        root, run = Path(t), Path(t) / "r1"
        run.mkdir()
        (run / "pick.json").write_text(json.dumps(cs), encoding="utf-8")
        record(run, "S", 0.8, 0.5, root)
        resolve(run, "supported", root)
        record(run, "S", 0.2, 0.5, root)
        (run / "pick.json").write_text(json.dumps({"id": "Z"}), encoding="utf-8")
        resolve(run, "no_improvement", root)  # no prediction for Z: ignored
        m, base, n = brier(util.lines(_log(root)))
    assert n == 1 and abs(m - 0.04) < 1e-9 and abs(base - 0.25) < 1e-9, (m, base, n)
    print(f"predict selftest ok (rank {ma:.2f} < shrinkage {ms:.2f}, shrinkage chosen {wins}/200, brier {m:.2f} vs baseline {base:.2f})")
