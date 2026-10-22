"""Method review ledger: every trial/verdict/finding/hypothesis becomes a row; review re-judges all methods from all evidence (P21)."""
import json
import re
from pathlib import Path

from lab import core, director

SCHEMA = "researchlab_method/v1"


def _ledger():
    return core.ROOT / "methods" / "ledger.jsonl"


def _rows():
    f = _ledger()
    return [json.loads(x) for x in f.read_text().splitlines()] if f.exists() else []


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


TOPICS = (("look-ahead", r"look-?ahead|membership|point-in-time|formation"), ("survivorship", r"surviv|delist"),
          ("ridge-scale", r"ridge"), ("dividend-basis", r"dividend|ex-date"), ("lockbox-window", r"lockbox|holdout window"),
          ("candidate-isolation", r"in-process|monkey|rebind|same process"), ("freeze-closure", r"freeze|transitive import"),
          ("dev-evaluation", r"dev-evaluation|--split dev"))  # critic ids are free text; group them by topic


def _klass(i, text=""):
    hay = f"{i} {text}".lower()
    return next((name for name, pat in TOPICS if re.search(pat, hay)), re.sub(r"\d+", "", i.lower()).strip("-_ "))


def _run_rows(d):
    c = core.cfg(d)
    exp, q = Path(c.get("exp_dir", "")), c["q"]
    if not exp.is_dir():
        return []
    out, rid = [], d.name
    rows = [r for r in director._trials(exp, q) if r.get("n")]
    direction = "max"
    if (exp / "loop.json").exists():
        direction = json.loads((exp / "loop.json").read_text()).get(q, {}).get("direction", "max")
    sign = 1 if direction == "max" else -1
    mem = {}
    if (exp / "memory.md").exists():
        for ln in (exp / "memory.md").read_text().splitlines():
            m = re.match(r"#(\d+) ", ln)
            if m:
                mem[m[1]] = ln.split(" -> ", 1)[-1]
    base = next((r for r in rows if r["status"] == "keep"), None)
    hsrc = {r["n"]: (re.match(r"holdout of #(\d+)", r["desc"]) or [0, ""])[1] for r in rows if r["status"] == "holdout"}
    for r in rows:
        hold = r["status"] == "holdout"
        ref = base["n"] if base else None
        b = next((x for x in rows if x["status"] == "holdout" and hsrc[x["n"]] == ref), None) if hold else base
        s, bs = _f(r["score"]), _f(b["score"]) if b else None
        out.append({"key": f"{rid}:{q}:{r['n']}", "kind": "trial", "run": rid, "q": q, "n": int(r["n"]), "utc": r["utc"],
                    "method": re.sub(r"^\[[^\]]*\] ", "", re.sub(r"^holdout of #\d+ \(dev [^)]*\): ", "", r["desc"])), "status": r["status"],
                    "score": s, "baseline": bs, "direction": direction, "holdout": hold,
                    "src": int(hsrc[r["n"]]) if hold and hsrc[r["n"]] else int(r["n"]),
                    "delta": None if s is None or bs is None else round(sign * (s - bs), 6),
                    "lesson": mem.get(hsrc[r["n"]] if hold else r["n"], "")})
    v = director._verdict(exp, q)
    if v:
        ln = [x for x in (exp / "DECISIONS.md").read_text().splitlines() if f" {q} **" in x][-1]
        out.append({"key": f"{rid}:{q}:verdict", "kind": "verdict", "run": rid, "q": q, "verdict": v,
                    "reason": ln.split("**", 2)[2].lstrip(": ").strip(),
                    "seed": json.loads((d / "pick.json").read_text()).get("seed", "") if (d / "pick.json").exists() else ""})  # KG combination it came from
    f = d / "critique.json"
    for x in json.loads(f.read_text()).get("findings", []) if f.exists() else []:
        out.append({"key": f"{rid}:critic:{x['id']}", "kind": "finding", "run": rid, "id": x["id"], "class": _klass(x["id"], x.get("claim") or x.get("evidence") or ""),
                    "severity": x.get("severity", ""), "claim": (x.get("claim") or x.get("evidence") or "")[:200]})
    f = d / "DISCRIMINATION.md"
    if f.exists():
        text = {h["id"]: h.get("mechanism", "") for h in json.loads((d / "hypotheses.json").read_text()).get("hypotheses", [])} \
            if (d / "hypotheses.json").exists() else {}
        for ln in f.read_text().splitlines():
            m = re.match(r"\|\s*(H\d+)\s*\|\s*(survives|refuted|\w+)\s*\|", ln)
            if m and m[1] not in {o["key"].split(":")[-1] for o in out if o["kind"] == "hypothesis"}:
                out.append({"key": f"{rid}:hyp:{m[1]}", "kind": "hypothesis", "run": rid, "id": m[1], "status": m[2],
                            "mechanism": text.get(m[1], "")[:200]})
    return out


def scan():
    """Upsert rows by key into methods/ledger.jsonl; returns (rows, new)."""
    old = {r["key"]: r for r in _rows()}
    new = 0
    for d in sorted((core.ROOT / "runs").iterdir()) if (core.ROOT / "runs").is_dir() else []:
        if not (d / "run.json").exists():
            continue
        for r in _run_rows(d):
            new += r["key"] not in old
            old[r["key"]] = {"schema": SCHEMA, **r}
    _ledger().parent.mkdir(exist_ok=True)
    _ledger().write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in old.values()))
    return len(old), new


def _c(s):
    return str(s).replace("|", "/").replace("\n", " ")


def _d(x):
    return "-" if x is None else f"{x:+.4g}"


TESTS = {"small": "조건부(국면·변동성 구간), 표본 제약 완화(기간·유니버스 확장)", "near-miss": "비선형(구간별·분위), 다른 지표",
         "dev-only": "확인 표본 1회", "partial": "사건 창"}
IVAL = re.compile(r"([+-]?\d[\d.]*)\s*%?p?\s*\[\s*(-?\d[\d.]*)\s*,\s*(-?\d[\d.]*)\s*\]")
PART = re.compile(r"일부 (기간|집단|구간)|some (period|subgroup|year)|sub-?period|subgroup", re.I)


def _excl0(text):
    """Last 'effect [a, b]' in text whose interval excludes 0, as 'effect [a, b]'; else None."""
    m = IVAL.findall(text)
    return f"{m[-1][0]} [{m[-1][1]}, {m[-1][2]}]" if m and (float(m[-1][1]) > 0 or float(m[-1][2]) < 0) else None


def _revisit(rows, dev):
    """Clues that are not plain failures: [(kind, ref, note)]; kind picks the suggested tests in TESTS."""
    out = [("dev-only", x[2:].split(":")[0], x.split(": ", 1)[-1]) for x in dev]
    for r in rows:
        if r["kind"] == "verdict":
            hit = _excl0(r["reason"])
            if r["verdict"] == "small":
                out.append(("small", f"{r['run']} {r['q']}", f"verdict small: {_c(r['reason'])[:100]}"))
            elif r["verdict"] == "inconclusive" and hit:
                out.append(("small", f"{r['run']} {r['q']}", f"inconclusive but interval excludes 0 ({hit}); relabelled small"))
        elif r["kind"] == "hypothesis" and r["status"] in ("partial", "mixed"):
            out.append(("partial", f"{r['run']} {r['id']}", f"{r['status']}: {_c(r['mechanism'])[:100]}"))
    for run, q in sorted({(r["run"], r["q"]) for r in rows if r["kind"] == "trial"}):
        try:
            md = _f(json.loads((Path(core.cfg(core.ROOT / "runs" / run)["exp_dir"]) / "loop.json").read_text())[q].get("min_delta"))
        except (OSError, KeyError, ValueError, TypeError):
            md = None
        best = None  # best kept signed score so far
        for r in sorted((x for x in rows if (x["kind"], x["run"], x.get("q")) == ("trial", run, q) and not x["holdout"] and x["score"] is not None), key=lambda x: x["n"]):
            sc = (1 if r["direction"] == "max" else -1) * r["score"]
            if r["status"] == "keep":
                best = sc if best is None else max(best, sc)
            elif md and r["status"] == "discard" and best is not None and 0 < sc - best >= md / 2:
                out.append(("near-miss", f"{run} {q} #{r['n']}", f"discarded {_d(sc - best)} over best kept >= half of min_delta {md:g}: {_c(r['method'])[:80]}"))
    for f in sorted((core.ROOT / "runs").glob("*/DISCRIMINATION.md")):
        out += [("partial", f.parent.name, _c(ln.strip("| "))[:120]) for ln in f.read_text().splitlines() if PART.search(ln)]
    listing = core.ROOT / "memory" / "global" / "experiments.json"
    for folder in map(Path, json.loads(listing.read_text()) if listing.exists() else []):
        f = folder / "RESULT.md"
        lines = f.read_text().splitlines() if f.exists() else []
        if not lines or not re.search(r"inconclusive|no_effect", lines[0]):
            continue
        for ln in lines[1:]:
            cells = [x.strip().strip("`") for x in ln.split("|")]
            hit = _excl0(ln)
            if hit and len(cells) > 2 and cells[-2] in ("inconclusive", "no_effect"):
                out.append(("small", folder.name, f"{cells[1]}: {hit} ({cells[-2]}); relabelled small"))
    return out


def review():
    rows = _rows()
    trials = [r for r in rows if r["kind"] == "trial"]
    verd = {(r["run"], r["q"]): r for r in rows if r["kind"] == "verdict"}
    out, works, dev, fails = ["# Methods review", "", f"Generated from methods/ledger.jsonl ({len(rows)} rows). Delta: positive = better than baseline.", ""], [], [], []
    for run, q in sorted({(r["run"], r["q"]) for r in trials}):
        ts = [r for r in trials if (r["run"], r["q"]) == (run, q)]
        hold = {r["src"]: r for r in ts if r["holdout"]}
        v = verd.get((run, q))
        out += [f"## {run} {q}" + (f" - {v['verdict']}: {_c(v['reason'])[:200]}" if v else ""), "",
                "| method | trials | best delta | holdout | status | lesson |", "|---|---|---|---|---|---|"]
        by = {}
        for r in ts:
            if not r["holdout"] and r["delta"] != 0:
                by.setdefault(r["method"], []).append(r)
        for m, rs in by.items():
            ds = [r["delta"] for r in rs if r["delta"] is not None]
            best = max(rs, key=lambda r: r["delta"] if r["delta"] is not None else -1e18)
            h = hold.get(best["n"])
            hd = h["delta"] if h else None
            out.append(f"| {_c(m)[:90]} | {','.join('#%d' % r['n'] for r in rs)} | {_d(max(ds) if ds else None)} | "
                       f"{_d(hd) if h else '-'} | {'/'.join(sorted({r['status'] for r in rs}))} | {_c(best['lesson'])[:120]} |")
            line = f"- {run} {q} #{best['n']}: {_c(m)[:90]} (dev {_d(best['delta'])}" + (f", holdout {_d(hd)})" if h else ")")
            if h and hd is not None and hd > 0:
                works.append(line)
            elif best["status"] == "keep" and best["delta"] is not None and best["delta"] > 0 and not h:
                dev.append(line)
            else:
                fails.append(line)
        out.append("")
    cls = {}
    for r in rows:
        if r["kind"] == "finding":
            cls.setdefault(r["class"], []).append(r)
    out += ["## Finding classes", "", "| class | runs | severities | status |", "|---|---|---|---|"]
    cand = 0
    for k, rs in sorted(cls.items(), key=lambda kv: -len({r["run"] for r in kv[1]})):
        runs = sorted({r["run"] for r in rs})
        st = "rule candidate" if len(runs) >= 3 else "watch" if len(runs) == 2 else "once"
        cand += st == "rule candidate"
        out.append(f"| {_c(k)} | {len(runs)}: {', '.join(runs)} | {', '.join(sorted({r['severity'] for r in rs}))} | {st} |")
    out += ["", "## Works / fails", "", "Works (holdout-confirmed, scope = that run's question):"] + (works or ["- none"])
    out += ["", "Dev only (kept, no holdout opened):"] + (dev or ["- none"]) + ["", "Fails (discarded, negative, or holdout lost):"] + (fails or ["- none"])
    rv = [{"kind": k, "ref": ref, "note": note, "tests": TESTS[k]} for k, ref, note in _revisit(rows, dev)]
    out += ["", "## 다시 볼 단서", ""] + [f"- [{x['kind']}] {x['ref']}: {x['note']} -> {x['tests']}" for x in rv] + ([] if rv else ["- none"])
    (core.ROOT / "METHODS.md").write_text("\n".join(out) + "\n")
    return {"summary": f"METHODS.md: {len(works)} works, {len(dev)} dev-only, {len(fails)} fails, {cand} rule candidates, {len(rv)} revisit",
            "works": works, "dev": dev, "fails": fails, "rule_candidates": cand, "revisit": rv}


def methods_scan(a):
    n, new = scan()
    print(f"methods ledger: {n} rows ({new} new)")


def methods_review(a):
    print(review()["summary"])


def selftest():
    import tempfile
    from unittest.mock import patch
    with tempfile.TemporaryDirectory() as t:
        root = Path(t)
        hdr = "n\tutc\tq\tprotocol\tprocedure\tcode\tscore\tstatus\tseconds\tdesc\n"

        def mk(rid, rows, direction="max", fids=(), exp=True):
            d, e = root / "runs" / rid, root / "exp" / rid
            d.mkdir(parents=True)
            (d / "run.json").write_text(json.dumps({"id": rid, "exp_dir": str(e), "q": "Q1"}))
            if fids:
                (d / "critique.json").write_text(json.dumps({"findings": [{"id": i, "severity": "major", "claim": "c"} for i in fids]}))
            if exp:
                e.mkdir(parents=True)
                (e / "loop.json").write_text(json.dumps({"Q1": {"direction": direction}}))
                (e / "results.tsv").write_text(hdr + "".join(f"{i}\tu\tQ1\tp\tx\tc\t{s}\t{st}\t0\t{ds}\n" for i, (s, st, ds) in enumerate(rows, 1)))
                (e / "memory.md").write_text("#2 lesson two\n")
                (e / "DECISIONS.md").write_text("- d Q1 **supported_within_scope**: ok\n")

        with patch.object(core, "ROOT", root):
            mk("a", [(1, "keep", "baseline"), (3, "keep", "up"), (4, "holdout", "holdout of #2 (dev 3): up"), (2, "holdout", "holdout of #1 (dev 1): baseline")],
               fids=["no-lock-1"])
            mk("b", [(10, "keep", "baseline"), (7, "keep", "down"), (12, "discard", "worse")], direction="min", fids=["no-lock-2"])
            mk("c", [(1, "keep", "baseline")], fids=["no-lock-3", "other-1"])
            mk("d", [(1, "keep", "baseline")], fids=["no-lock-4"], exp=False)
            n, new = scan()
            assert scan() == (n, 0), "rescan must be idempotent"
            rows = {r["key"]: r for r in _rows()}
            assert rows["a:Q1:2"]["delta"] == 2 and rows["a:Q1:2"]["lesson"] == "#2 lesson two", rows["a:Q1:2"]
            assert rows["a:Q1:3"]["holdout"] and rows["a:Q1:3"]["delta"] == 2 and rows["a:Q1:3"]["src"] == 2
            assert rows["b:Q1:2"]["delta"] == 3 and rows["b:Q1:3"]["delta"] == -2, "min direction: lower is better"
            assert rows["a:Q1:verdict"]["verdict"] == "supported_within_scope"
            assert not [k for k in rows if k.startswith("d:")], "missing exp_dir skipped"
            r = review()
            assert r["rule_candidates"] == 1 and len(r["works"]) == 1 and len(r["fails"]) == 1, r
            txt = (root / "METHODS.md").read_text()
            assert "| no-lock | 3: a, b, c | major | rule candidate |" in txt and "| other | 1: c" in txt, txt
            assert [x["kind"] for x in r["revisit"]] == ["dev-only"] and "## 다시 볼 단서" in txt, r["revisit"]
            mk("e", [(10, "keep", "baseline"), (13, "discard", "near"), (11, "discard", "too small"), (15, "keep", "best"), (17, "discard", "above baseline, not above best")])
            (root / "exp" / "e" / "loop.json").write_text(json.dumps({"Q1": {"direction": "max", "min_delta": 6}}))
            (root / "exp" / "e" / "DECISIONS.md").write_text("- d Q1 **inconclusive**: effect +4.2 [1.1, 7.3] below min\n")
            (root / "runs" / "e" / "DISCRIMINATION.md").write_text("| H1 | partial |\n| H2 | survives | only in some periods |\n")
            ext = root / "ext"
            ext.mkdir()
            (ext / "RESULT.md").write_text("종목별 평균은 inconclusive이며\n| 종목별 평균 | 21% [10, 35]% | 5% [4, 5]% | +16.67 [3.98, 32.06]%p | 0.0144 | inconclusive |\n"
                                           "| 시간 | 8% [4, 12]% | 5% | +3.10 [-0.63, 8.70]%p | 0.09 | inconclusive |\n")
            (root / "memory" / "global").mkdir(parents=True)
            (root / "memory" / "global" / "experiments.json").write_text(json.dumps([str(ext)]))
            scan()
            rv = review()["revisit"]
            kinds = [(x["kind"], x["ref"]) for x in rv]
            assert ("near-miss", "e Q1 #2") in kinds and ("near-miss", "e Q1 #3") not in kinds and ("near-miss", "e Q1 #5") not in kinds, kinds
            assert any(x["kind"] == "small" and x["ref"] == "e Q1" and "relabelled small" in x["note"] for x in rv), rv
            assert ("partial", "e") in kinds, kinds
            ex = [x for x in rv if x["ref"] == "ext"]
            assert len(ex) == 1 and "+16.67 [3.98, 32.06]" in ex[0]["note"] and ex[0]["tests"] == TESTS["small"], ex
    print(f"methods selftest: ok ({n} rows, idempotent, delta signs, recurrence, skip)")
