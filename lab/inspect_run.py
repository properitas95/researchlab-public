"""Small, offline run inspector; reads a run and its experiment directory only."""
import csv
import json
import sys
from lab import core, roles
from lab.util import esc as _e
from pathlib import Path


def _text(p):
    try:
        return p.read_text(encoding="utf-8", errors="replace") if p.exists() else ""
    except OSError:
        return ""


def _json(p, default=None):
    try:
        return json.loads(_text(p)) if p.exists() else default
    except (OSError, ValueError):
        return default


def _table(headers, rows):
    if not rows:
        return "<p class='muted'>없음</p>"
    h = "".join(f"<th>{_e(x)}</th>" for x in headers)
    body = "".join("<tr>" + "".join(f"<td>{_e(x)}</td>" for x in r) + "</tr>" for r in rows)
    return f"<table><thead><tr>{h}</tr></thead><tbody>{body}</tbody></table>"


def _section(title, body):
    return f"<section><h2>{_e(title)}</h2>{body}</section>"


def _chart(rows, direction="max"):
    vals = [(int(r.get("n", 0)), float(r["score"])) for r in rows
            if r.get("status") != "holdout" and r.get("score") not in (None, "")]
    if not vals:
        return "<p class='muted'>개발 점수 없음</p>"
    w, h, pad = 760, 210, 28
    lo, hi = min(0, min(v for _, v in vals)), max(0, max(v for _, v in vals))
    span = hi - lo or 1
    def xy(n, v):
        x = pad + (n - vals[0][0]) / max(1, vals[-1][0] - vals[0][0]) * (w - 2 * pad)
        y = h - pad - (v - lo) / span * (h - 2 * pad)
        return round(x, 1), round(y, 1)
    dots = "".join(f"<circle cx='{xy(n,v)[0]}' cy='{xy(n,v)[1]}' r='4'><title>#{n}: {v:g}</title></circle>" for n,v in vals)
    dev = " ".join(f"{xy(n,v)[0]},{xy(n,v)[1]}" for n,v in vals)
    keeps = {int(r["n"]) for r in rows if r.get("status") == "keep"}
    best, line = None, []
    for n, v in vals:
        if best is not None:
            line.append(xy(n, best))
        if n in keeps:
            best = v if best is None else (min(best, v) if direction == "min" else max(best, v))
        if best is not None:
            line.append(xy(n, best))
    path = " ".join(f"{x},{y}" for x,y in line)
    zero = xy(vals[0][0], 0)[1]
    return (f"<svg class='chart' viewBox='0 0 {w} {h}' role='img' aria-label='개발 점수와 누적 최고점'>"
            f"<line x1='{pad}' y1='{zero}' x2='{w-pad}' y2='{zero}' class='axis'/>"
            f"<polyline points='{dev}' class='dev'/><polyline points='{path}' class='best'/>{dots}</svg>"
            f"<p>Trial → · dev score ({lo:g} … {hi:g}); blue = running best ({direction})</p>")


def _tree(tree):
    if not tree:
        return "<p class='muted'>트리 기록 없음</p>"
    children = {}
    for x in tree:
        children.setdefault(str(x.get("parent")), []).append(x)
    def walk(x):
        n = str(x.get("n")); label = f"#{n} {x.get('kind','')} {x.get('status','')} score={x.get('score','')}"
        return f"<li>{_e(label)}" + (f"<ul>{''.join(walk(c) for c in children.get(n, []))}</ul>" if children.get(n) else "") + "</li>"
    return "<ul class='tree'>" + "".join(walk(x) for x in children.get("None", [])) + "</ul>"


def inspect(a):
    rid = str(getattr(a, "run", ""))
    d = (Path(rid) if Path(rid).is_dir() else core.ROOT / "runs" / rid).resolve()
    if not (d / "run.json").exists():
        raise FileNotFoundError(f"no run.json in {d}")
    cfg = _json(d / "run.json", {}) or {}
    exp = Path(cfg.get("exp_dir", d / "exp"))
    out = Path(getattr(a, "output", None) or (d / "index.html"))
    brief = _text(d / "brief.md").splitlines()
    pick = _json(d / "pick.json", {}) or {}
    decisions = _text(exp / "DECISIONS.md")
    q = cfg.get("q", "Q1")
    verdict_line = next((x.strip() for x in reversed(decisions.splitlines()) if f"{q} **" in x), "미판정")
    verdict = verdict_line.split("**")[1] if "**" in verdict_line else verdict_line
    evtxt = _text(d / "events.jsonl")
    events = [e for x in evtxt.splitlines() if x.strip() and isinstance((e := _json_line_safe(x)), dict)]
    cands = _json(d / "candidates.json", []) or []
    cands = list(cands.values()) if isinstance(cands, dict) else cands
    nov = _json(d / "novelty.json", {}) or {}
    nov = ({str(x.get("id")): x for x in nov} if isinstance(nov, list) else nov)
    rows = []
    rf = exp / "results.tsv"
    if rf.exists():
        with rf.open(newline="", encoding="utf-8", errors="replace") as f:
            rows = list(csv.DictReader(f, delimiter="\t"))
    rows = [r for r in rows if not r.get("q") or r.get("q") == q]
    tree = []
    for line in _text(exp / "tree.jsonl").splitlines():
        try: tree.append(json.loads(line))
        except ValueError: pass
    cand_rows = [[x.get("id"), x.get("title"), x.get("value"), nov.get(str(x.get("id")), {}).get("verdict", ""), str(x.get("flags", "")) + (" predictable" if x.get("predictable") else "")] for x in cands]
    novelty_rows = [[k, v.get("verdict", ""), v.get("note", "")[:100]] for k,v in nov.items() if isinstance(v, dict)]
    result_rows = [[r.get("n"), r.get("status"), r.get("score"), r.get("desc", "")[:100]] for r in rows]
    event_rows = [[e.get("utc", ""), e.get("stage", ""), e.get("msg", "")] for e in events]
    links = []
    for label, p in (("RESULT", d / "RESULT.md"), ("REVIEW", d / "REVIEW.md"), ("paper.tex", d / "paper" / "paper.tex"), ("paper.pdf", d / "paper" / "paper.pdf")):
        if p.exists(): links.append(f"<a href='{_e(p.as_uri())}'>{_e(label)}</a>")
    panel = _json(d / "panel" / "scores.json", {}) or {}
    panel_cols = ("id", "persona", "soundness", "significance", "clarity", "overall", "confidence", "decision", "valid")
    panel_rows = [[x.get(k) for k in panel_cols] for x in panel.get("reviewers", [])]
    manifests = [(p.name, p) for base in (d, exp) for p in base.glob("*") if p.is_file() and (p.name in ("ablations.md", "ablations.tsv", "posthoc.md", "posthoc.tsv", "MANIFEST.md", "manifest.json") or "manifest" in p.name.lower())]
    manifests += [(p.name, p) for base in (d / "posthoc", exp / "posthoc") if base.exists() for p in base.rglob("*manifest*") if p.is_file()]
    extra = "".join(_section(name, f"<pre>{_e(_text(p))}</pre>") for name, p in manifests)
    history = _text(d / "panel" / "history.jsonl") or _text(d / "panel" / "history.json")
    extra += _section("패널 이력", f"<pre>{_e(history)}</pre>" + _table(("UTC", "내용"), [[e.get("utc"), e.get("msg")] for e in events if e.get("stage") == "panel"]))
    loop = _json(exp / "loop.json", {}) or {}
    direction = loop.get(q, {}).get("direction", "max")
    css = "body{font:14px system-ui,sans-serif;max-width:1100px;margin:2rem auto;padding:0 1rem;color:#18202a;background:#fff}section{margin:1.2rem 0;padding:1rem;border:1px solid #d8dee8;border-radius:8px}h1{margin-bottom:.2rem}h2{font-size:1.05rem}table{border-collapse:collapse;width:100%}th,td{border-bottom:1px solid #e3e7ed;padding:.35rem;text-align:left;vertical-align:top}th{background:#f2f5f8}.muted{color:#667085}.st-keep{color:#087f23;font-weight:700}.st-discard{color:#a15c00}.st-crash,.st-invalid{color:#b42318}.st-holdout{color:#6941c6}.tree ul{margin:.25rem 0}.chart{width:100%;height:210px;background:#fafbfc}.chart .axis{stroke:#9aa5b1}.chart .dev{fill:none;stroke:#8b95a5;stroke-width:1}.chart .best{fill:none;stroke:#1565c0;stroke-width:2}.chart circle{fill:#d84315}@media(prefers-color-scheme:dark){body{color:#e6edf3;background:#111827}section{border-color:#374151}th{background:#1f2937}td,th{border-color:#374151}.chart{background:#18212b}.chart .axis{stroke:#667085}}"
    title = brief[0] if brief else rid
    html_doc = f"<!doctype html><meta charset='utf-8'><meta name='color-scheme' content='light dark'><style>{css}</style><main><h1>{_e(title)}</h1><p><b>pick:</b> {_e(pick.get('id',''))} {_e(pick.get('title',''))} · <b>verdict:</b> {_e(verdict)}</p>"
    html_doc += _section("이벤트 타임라인", _table(("UTC","단계","내용"), event_rows))
    html_doc += _section("후보 · 가치 · 신규성", _table(("ID","후보","value","novelty","flag"), cand_rows) + _table(("ID","novelty","note"), novelty_rows))
    result_html = _table(("trial","status","score","description"), result_rows)
    for status in ("keep", "discard", "crash", "invalid", "holdout"):
        result_html = result_html.replace(f"<td>{status}</td>", f"<td class='st-{status}'>{status}</td>")
    html_doc += _section("개발 결과", _chart(rows, direction) + result_html)
    html_doc += _section("탐색 트리", _tree(tree))
    hold = [r for r in rows if r.get("status") == "holdout"]
    html_doc += _section("홀드아웃", _table(("trial","score","description"), [[r.get("n"),r.get("score"),r.get("desc","")] for r in hold]))
    html_doc += extra + _section("패널", _table(panel_cols, panel_rows) + f"<p>meta: {_e(panel.get('meta_decision',''))}; mean: {_e(panel.get('mean_overall',''))}</p>")
    html_doc += _section("자료 링크", " · ".join(links) or "<span class='muted'>없음</span>") + "</main>"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html_doc, encoding="utf-8", errors="replace")
    return out


def _json_line_safe(s):
    try: return json.loads(s)
    except ValueError: return None


def selftest():
    import tempfile
    from types import SimpleNamespace
    with tempfile.TemporaryDirectory() as t:
        d = Path(t) / "runs" / "x"; e = d / "exp"; e.mkdir(parents=True)
        (d / "run.json").write_text(json.dumps({"exp_dir": str(e)}))
        (d / "fake.py").write_text("print('<script>alert(1)</script>')\n")
        instr = core.prompt("propose", **dict.fromkeys(("protocol", "status", "recent", "mutable", "code", "memory"), ""))
        (d / "brief.md").write_text(roles.call("experiment", instr, cwd=d, run=d, write=False, provider="cmd:python3 fake.py", model=""))
        (d / "events.jsonl").write_text('{"stage":"test","msg":"<event>"}\n')
        (d / "candidates.json").write_text('[{"id":"C1","title":"<cand>","value":1,"predictable":true}]')
        (d / "novelty.json").write_text('{"C1":{"verdict":"unclear","note":"<novel>"}}')
        (d / "pick.json").write_text('{"id":"C1","title":"<pick>"}')
        (e / "loop.json").write_text('{"Q1":{"direction":"min"}}')
        (e / "results.tsv").write_text("n\tq\tscore\tstatus\tdesc\n1\tQ1\t0.1\tkeep\tbase\n2\tQ1\t-0.2\tdiscard\tbetter-looking\n3\tQ1\t0.3\tholdout\tsecret\n")
        (e / "tree.jsonl").write_text('{"n":1,"parent":null,"kind":"draft","score":0.1,"status":"keep"}\n{"n":2,"parent":1,"kind":"improve","score":-0.2,"status":"discard"}\n')
        (e / "ablations.tsv").write_text("name\tscore\nbase\t0.1\n")
        (d / "posthoc").mkdir(); (d / "posthoc" / "manifest.json").write_text('{"m":"<manifest>"}')
        (d / "panel").mkdir(); (d / "panel" / "scores.json").write_text('{"meta_decision":"accept","mean_overall":6,"reviewers":[]}')
        (d / "panel" / "history.jsonl").write_text('{"round":1,"mean_overall":6}\n')
        p = inspect(SimpleNamespace(run=str(d), output=str(Path(t) / "out.html")))
        s = p.read_text()
        for needle in ("&lt;script&gt;", "&lt;cand&gt;", "&lt;event&gt;", "novelty", "holdout", "tree", "ablations.tsv", "manifest.json", "패널 이력", "meta: accept"):
            assert needle in s, needle
        assert "<script>alert" not in s and "st-discard" in s and "polyline" in s
        assert "#3: 0.3" not in s  # holdout is isolated from the dev chart
        chart = _chart([{"n": "1", "score": "2", "status": "keep"}, {"n": "2", "score": "1", "status": "keep"}], "min")
        assert "28.0,28.0 732.0,28.0 732.0,105.0" in chart
    print("inspect selftest ok")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "selftest": selftest()
    else:
        from types import SimpleNamespace
        print(inspect(SimpleNamespace(run=sys.argv[1], output=sys.argv[2] if len(sys.argv) > 2 else None)))
