"""Read-only lineage page: per run, what was asked, what was picked, how far it got, why it stopped, what was learned.
Supporting evidence below: a research graph per run, then the stage timeline (critical path) and agent time by role."""
import datetime as dt
import json
import re
import tempfile
from pathlib import Path
from unittest import mock

from lab import core, director, methods
from lab.kanban import GROUPS
from lab.util import esc as _e, lines as _lines, load as _json

KST = dt.timezone(dt.timedelta(hours=9))
STAGE_COLORS = ("#d97757", "#6a9bcc", "#788c5d", "#9c8f6b", "#5e5d59")
SEV = ("blocker", "major", "minor", "known")
SEV_KO = dict(blocker="blocker", major="major", minor="minor", known="known defect")
# Plain-word themes for critic finding classes; first match wins, ids and claims stay one click away.
THEMES = (("holdout sealing", r"holdout|lockbox"), ("future information and data timing", r"look-?ahead|future|surviv|point-in-time|formation"),
          ("baseline definition", r"baseline|status-quo|anchor"), ("freeze scope and isolation", r"freez|frozen|isolat|interpreter|\bpin|stdout|sandbox|runtime|import|dependenc|deps"),
          ("dev-sample reuse", r"dev-"), ("score does not answer the question", r"metric|score|comparator|gate|criterion|verdict|falsif|question"),
          ("effect size and noise floor", r"delta|noise|effect|bootstrap|interval|power"), ("synthetic data design", r"dgp|synthetic|proxy|oracle|environment"),
          ("data basis and provenance", r"dividend|cache|provenance|source|splice|coverage|ridge"), ("hypothesis and scope wording", r"hypothes|overclaim|scope|title|pick|brief|wording"))
VERDICT_KO = dict(invalid="invalid", no_improvement="no improvement", inconclusive="inconclusive", supported="supported",
                  supported_within_scope="supported within scope", improved="improved", refuted="refuted")
NOVELTY_KO = dict(unclear="novelty unclear", novel="no prior work", known="prior work exists", dropped="dropped, prior work exists")


def analyze(nodes):
    """Longest estimate-weighted chain over {id, depends_on, estimate}."""
    by = {n["id"]: n for n in nodes}
    children = {k: [] for k in by}
    for k, n in by.items():
        for d in n.get("depends_on", []):
            if d not in by:
                raise ValueError("missing dependency")
            children[d].append(k)
    paths, visiting = {}, set()

    def visit(k):
        if k in visiting:
            raise ValueError("dependency cycle")
        if k in paths:
            return
        visiting.add(k)
        best = (0, [])
        for c in sorted(children[k]):
            visit(c)
            if not best[1] or paths[c][0] > best[0]:
                best = paths[c]
        paths[k] = (by[k]["estimate"] + best[0], [k] + best[1])
        visiting.remove(k)

    for k in sorted(by):
        visit(k)
    roots = (k for k in sorted(by) if not by[k].get("depends_on"))
    hours, path = max((paths[k] for k in roots), key=lambda p: p[0], default=(0, []))
    return dict(critical_path=path, hours=hours)


def _t(s):
    return dt.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)


def _depth(nodes):
    parent = {(n.get("n", n.get("node"))): n.get("parent") for n in nodes}
    def d(k):
        n = 0
        while parent.get(k) is not None and n <= len(parent):
            k, n = parent[k], n + 1
        return n
    return max((d(k) for k in parent), default=0)


def _trials(c):
    exp = Path(c["exp_dir"]) if c.get("exp_dir") else None
    if not exp:
        return {}
    nodes = _lines(exp / "tree.jsonl")
    if nodes:
        return dict(nodes=len(nodes), depth=_depth(nodes), edges=sum(n.get("parent") is not None for n in nodes))
    tsv = exp / "results.tsv"
    return dict(nodes=max(len(tsv.read_text().splitlines()) - 1, 0), depth=0, edges=0) if tsv.exists() else {}


def _stage(e):
    """campaign events name the stage in msg ("run plan", "plan completed")."""
    m = re.match(r"(?:run )?(\w+)(?: completed| failed)?$", e.get("msg", "")) if e.get("stage") == "campaign" else None
    return m[1] if m else e.get("stage")


def _plain(s):
    """Event text without absolute paths (they leak the machine and say nothing to a reader)."""
    return re.sub(r"(?:[A-Za-z]:)?[/\\]\S*[/\\]([^/\\\s]+)", r"\1", str(s or "")).strip()


def theme(text):
    hay = text.lower()
    return next((name for name, pat in THEMES if re.search(pat, hay)), "other defects")


def _stop_ko(raw):
    m = re.match(r"design unresolved after (\d+) replan rounds?", raw)
    if m:
        return f"Design still failed review after {m[1]} revision{'s' * (int(m[1]) != 1)}, so it stopped"
    m = re.match(r"stop: (\w+) failed (\d+) times", raw)
    if m:
        return f"Stopped because the {m[1]} stage failed {m[2]} times in a row"
    m = re.match(r"USER: (choose a candidate|fix the design|read .*freeze)", raw)
    if m:
        return "Stopped, waiting for a human to " + ("choose a candidate" if m[1].startswith("choose") else "fix the design" if m[1].startswith("fix") else "read the protocol and freeze it")
    if raw.startswith("USER:"):
        return "Stopped, waiting for a human decision"
    if raw.startswith("DONE"):
        return "Completed the last stage"
    return f"Stopped: {_plain(raw)}" if raw else "Stopped"


def _status(ev):
    """The run's current state: the last park / stop / USER / DONE event, unless real stage work was logged after it."""
    halts = [i for i, e in enumerate(ev) if e.get("stage") == "park" or (e.get("stage") == "campaign" and str(e.get("msg", "")).startswith(("stop:", "USER:", "DONE")))]
    i = halts[-1] if halts else -1
    if i < 0 or any(_stage(e) not in ("campaign", "park", "new") for e in ev[i + 1:]):  # "run replan" counts as work
        return dict(kind="running", raw="", utc=ev[-1].get("utc", "") if ev else "")
    e = ev[i]
    raw = (e.get("reason") or e.get("msg") or "parked") if e.get("stage") == "park" else e["msg"]
    kind = "park" if e.get("stage") == "park" else "done" if raw.startswith("DONE") else "wait" if raw.startswith("USER:") else "stop"
    return dict(kind=kind, raw=raw, utc=e.get("utc", ""))


def story(d, c, ev, cs, ledger, defects):
    """Every fact the summary sentence and the graph use, read from the run folder and the workspace ledgers."""
    rid = d.name
    brief = (d / "brief.md").read_text(errors="ignore").splitlines() if (d / "brief.md").exists() else []
    question = next((ln.lstrip("# ").strip() for ln in brief if ln.strip()), "") or rid
    novelty = {k: v.get("verdict", "") for k, v in _json(d / "novelty.json").items() if isinstance(v, dict)}
    screened = set(_json(d / "screen" / "scores.json").get("scores", {}) or {}) | set(novelty)
    pick = _json(d / "pick.json")
    cands = _json(d / "candidates.json", [])
    cands = [dict(id=x.get("id", "?"), title=x.get("title", ""), hypothesis=x.get("hypothesis", ""), value=x.get("value"),
                  novelty=novelty.get(x.get("id"), ""), screened=x.get("id") in screened, picked=x.get("id") == pick.get("id"))
             for x in cands if isinstance(x, dict)] if isinstance(cands, list) else []
    auto = next((e for e in ev if e.get("msg") == "auto-pick by campaign"), None)
    rounds = []
    for e in ev:
        s, msg = e.get("stage"), e.get("msg", "")
        m = re.match(r"(\w+): (\d+) blockers?, (\d+) majors?", msg) if s == "critic" else None
        if s == "plan":
            rounds.append(dict(kind="plan", utc=e.get("utc", ""), text="initial design"))
        elif m:
            rounds.append(dict(kind="critic", utc=e.get("utc", ""), verdict=m[1], blockers=int(m[2]), majors=int(m[3]),
                               text=f"review b{m[2]} M{m[3]}", title=f"Review: {m[2]} blockers, {m[3]} majors"))
        elif s == "replan":
            rounds.append(dict(kind="replan", utc=e.get("utc", ""), text="design revised"))
    crit = _json(d / "critique.json")
    cls = {r.get("id"): r.get("class") for r in ledger if r.get("run") == rid and r.get("kind") == "finding"}
    findings = []
    for x in crit.get("findings", []) if isinstance(crit.get("findings"), list) else []:
        k = cls.get(x.get("id")) or methods._klass(x.get("id", ""), x.get("claim") or "")
        findings.append(dict(id=x.get("id", ""), severity=x.get("severity", "minor") if x.get("severity") in SEV else "minor",
                             cls=k, theme=theme(f"{x.get('id', '')} {k}"), claim=x.get("claim", ""), fix=x.get("fix", ""), defect=x.get("defect", "")))
    known = set(crit.get("known_defects") or []) | {f["defect"] for f in findings if f["defect"]}
    linked = [dict(id=x.get("id", ""), topic=x.get("topic", ""), status=x.get("status", ""), claim=x.get("claim", ""), runs=x.get("runs", []))
              for x in defects if rid in (x.get("runs") or []) or x.get("id") in known]
    others = {r.get("class") for r in ledger if r.get("kind") == "finding" and r.get("run") != rid}
    recurring = sorted({f["cls"] for f in findings} & others)
    status = _status(ev)
    exp = Path(c["exp_dir"]) if c.get("exp_dir") else None
    verdict = (director._verdict(exp, c.get("q", "Q1")) if exp and exp.is_dir() else None) or ""
    vj = _json(d / "verdict.json")
    lv = next((r for r in reversed(ledger) if r.get("run") == rid and r.get("kind") == "verdict"), {})
    hyps = [dict(id=r.get("id", ""), status=r.get("status", ""), mechanism=r.get("mechanism", ""))
            for r in ledger if r.get("run") == rid and r.get("kind") == "hypothesis"]
    tl = [r for r in ledger if r.get("run") == rid and r.get("kind") == "trial"]
    stamps = [e["utc"] for e in ev if e.get("utc")]
    return dict(question=question, candidates=cands, pick=dict(id=pick.get("id", ""), title=pick.get("title", ""), auto=bool(auto)) if pick else {},
                rounds=rounds, critique=dict(verdict=crit.get("verdict", ""), findings=findings) if crit else {}, defects=linked, recurring=recurring,
                status=status, stop=status if status["kind"] in ("park", "stop", "wait") else {},
                parks=sum(e.get("stage") == "park" for e in ev),
                design_parks=sum(e.get("stage") == "park" and str(e.get("reason") or e.get("msg", "")).startswith("design unresolved") for e in ev), verdict=verdict or vj.get("fine") or lv.get("verdict", ""),
                verdict_note=re.sub(r"^\[[^\]]*\]\s*", "", _plain(vj.get("message") or lv.get("reason") or "")).rstrip("."),
                tagged=bool(c.get("tagged")), tags=[x.get("id", "") for x in c.get("tags", []) if isinstance(x, dict)],
                hypotheses=hyps, trial_rows=len(tl), last=dict(stage=_stage(ev[-1]) if ev else "", utc=ev[-1].get("utc", "") if ev else ""),
                calls=len(cs), wall=(_t(max(stamps)) - _t(min(stamps))).total_seconds() if stamps else 0,
                helix=(core.ROOT / "helix" / "overview" / "concepts.jsonl").exists())


def build():
    root = core.ROOT
    progs = {p.parent.name: _json(p) for p in sorted((root / "programs").glob("*/program.json"))}
    ledger = _lines(root / "methods" / "ledger.jsonl")
    defects = _json(root / "memory" / "global" / "data_defects.json", [])
    defects = defects if isinstance(defects, list) else []
    runs = {}
    for f in sorted((root / "runs").glob("*/run.json")):
        d, c = f.parent, _json(f)
        ev, cs = _lines(d / "events.jsonl"), _lines(d / "calls.jsonl")
        spans = {}
        for gi, (g, stages) in enumerate(GROUPS):
            ts = [e["utc"] for e in ev if _stage(e) in stages.split() and e.get("utc")]
            if ts:
                spans[g] = dict(gi=gi, start=min(ts), end=max(ts))
        park = next((e for e in reversed(ev) if e.get("stage") == "park"), None)
        roles = {}
        for r in cs:
            x = roles.setdefault(r.get("role", "?"), dict(seconds=0.0, calls=0, ok=0))
            x["seconds"] += float(r.get("seconds") or 0)
            x["calls"] += 1
            x["ok"] += bool(r.get("ok"))
        exp = Path(c["exp_dir"]) if c.get("exp_dir") else None
        runs[d.name] = dict(id=d.name, program=c.get("program") or next((k for k, p in progs.items() if d.name in p.get("runs", [])), ""),
                            parent=None, spans=spans, parked=(park.get("reason") or park.get("msg") or "parked") if park else "",
                            verdict=(director._verdict(exp, c.get("q", "Q1")) if exp and exp.is_dir() else None) or "",
                            roles=roles, trials=_trials(c), first=min((e["utc"] for e in ev if e.get("utc")), default=""),
                            story=story(d, c, ev, cs, ledger, defects))
        runs[d.name]["graph"] = graph(runs[d.name])
    for k, p in progs.items():
        order = [r for r in p.get("runs", []) if r in runs]
        order += sorted((r for r in runs if runs[r]["program"] == k and r not in order), key=lambda r: runs[r]["first"])
        for a, b in zip(order, order[1:]):
            runs[b]["parent"] = a
        for h in p.get("history", []):  # failed runs carry no park event
            if h.get("action") == "failed" and h.get("run") in runs and not runs[h["run"]]["parked"]:
                runs[h["run"]]["parked"] = "failed"
    tasks = []
    for r in runs.values():
        prev = None
        for g, s in sorted(r["spans"].items(), key=lambda kv: kv[1]["gi"]):
            dep = [prev] if prev else [f"{r['parent']}:{max(runs[r['parent']]['spans'].items(), key=lambda kv: kv[1]['end'])[0]}"]\
                if r["parent"] and runs[r["parent"]]["spans"] else []
            s["hours"] = max((_t(s["end"]) - _t(s["start"])).total_seconds() / 3600, 0.01)
            prev = f"{r['id']}:{g}"
            tasks.append(dict(id=prev, depends_on=dep, estimate=s["hours"]))
    return dict(programs={k: dict(question=p.get("question", ""), runs=p.get("runs", [])) for k, p in progs.items()},
                runs=runs, pert=dict(tasks=tasks, **analyze(tasks)))


# ---------- research graph: fixed columns, rows by index (deterministic, no layout library) ----------
COLS = dict(cand=(0, "Hypothesis candidates"), design=(250, "Design and review"), theme=(430, "Defect themes"), ledger=(654, "Data defects, method ledger"), out=(850, "Outcome"))
COL_W = dict(cand=210, design=140, theme=184, ledger=156, out=112)
ORDER = list(COLS)
LANE_W = 970
ROW = 34


def graph(r):
    """Nodes and edges for one run's lane. Each node: id, col, row, label, kind, sev (findings), body (full text for the inspector)."""
    s, nodes, edges = r["story"], [], []

    def node(nid, col, label, kind, body="", sev="", title=""):
        nodes.append(dict(id=nid, col=col, row=sum(n["col"] == col for n in nodes), label=label, kind=kind, sev=sev, body=body, title=title or label))
        return nid

    for x in sorted(s["candidates"], key=lambda x: (not x["picked"], -(x["value"] or 0) if isinstance(x["value"], (int, float)) else 1, x["id"])):
        mark = NOVELTY_KO.get(x["novelty"], x["novelty"]) if x["novelty"] else ("screened" if x["screened"] else "")
        node(f"c:{x['id']}", "cand", f"{x['id']} {x['title']}", "picked" if x["picked"] else "cand",
             body=x["hypothesis"], title=f"{x['id']} {x['title']}" + (f" ({mark})" if mark else "")
             + (f", score {x['value']:.1f}" if isinstance(x["value"], (int, float)) else ""))
    prev = next((n["id"] for n in nodes if n["kind"] == "picked"), None)
    for i, rd in enumerate(s["rounds"]):
        label = rd.get("title", rd["text"])
        cur = node(f"d:{i}", "design", rd["text"], rd["kind"], title=f"{label} ({_kst_s(rd['utc'])})" if rd["utc"] else label)
        if prev:
            edges.append((prev, cur))
        prev = cur
    last_critic = next((f"d:{i}" for i in reversed(range(len(s["rounds"]))) if s["rounds"][i]["kind"] == "critic"), prev)
    themes = {}
    for f in (s.get("critique") or {}).get("findings", []):
        themes.setdefault(f["theme"], []).append(f)
    for name, fs in sorted(themes.items(), key=lambda kv: (min(SEV.index(f["severity"]) for f in kv[1]), kv[0])):
        worst = min((f["severity"] for f in fs), key=SEV.index)
        body = "\n\n".join(f"[{SEV_KO[f['severity']]}] {f['id']}\n{f['claim']}" + (f"\nFix: {f['fix']}" if f["fix"] else "") for f in fs)
        tid = node(f"t:{name}", "theme", f"{name} {len(fs)}", "theme", body=body, sev=worst, title=f"{name}: {len(fs)} finding{'s' * (len(fs) != 1)}, worst: {SEV_KO[worst]}")
        if last_critic:
            edges.append((last_critic, tid))
    for x in s["defects"]:
        did = node(f"x:{x['id']}", "ledger", x["id"], "defect", body=x["claim"], title=f"{x['id']} ({x['status'] or 'no status'}, {len(x['runs'])} run{'s' * (len(x['runs']) != 1)})")
        topic = x["topic"] or x["id"].removeprefix("D-")
        for name, fs in themes.items():
            if any(f["defect"] == x["id"] or f["cls"] == topic for f in fs):
                edges.append((f"t:{name}", did))
    for k in s["recurring"]:
        mid = node(f"m:{k}", "ledger", k, "method", body="The same defect class was also recorded in the review of another run (methods/ledger.jsonl).", title=f"Method ledger: {k}")
        for name, fs in themes.items():
            if any(f["cls"] == k for f in fs):
                edges.append((f"t:{name}", mid))
    out = _outcome(r)
    oid = node("o", "out", out[0], "outcome", body=out[1], title=out[0])
    if prev:
        edges.append((prev, oid))
    return dict(nodes=nodes, edges=[dict(src=a, dst=b) for a, b in edges])


def _outcome(r):
    s = r["story"]
    n = r["trials"].get("nodes") or s["trial_rows"]
    if s["verdict"]:
        return (f"Verdict: {VERDICT_KO.get(s['verdict'], s['verdict'])}" + (" \u00b7 tagged" if s["tagged"] else ""),
                (s["verdict_note"] + "\n" if s["verdict_note"] else "") + (f"{n} trials" if n else "no trials recorded")
                + (f"\nFrozen with unresolved findings: {', '.join(s['tags'])}" if s["tagged"] else ""))
    if s["stop"]:
        reached = max((v["gi"] for v in r["spans"].values()), default=-1)
        k = s["stop"]["kind"]
        label = "Waiting for a human" if k == "wait" else f"Stopped in {GROUPS[reached][0]}" if reached >= 0 and k == "park" else "Stopped on failure" if k == "stop" else "Stopped"
        return label, _stop_ko(s["stop"]["raw"]) + (f" ({_kst_s(s['stop']['utc'])})" if s["stop"]["utc"] else "")
    return "In progress", f"Last record: {s['last']['stage']} ({_kst_s(s['last']['utc'])})" if s["last"]["utc"] else "no records"


def _kst_s(s):
    try:
        t = _t(s).astimezone(KST)
    except (TypeError, ValueError):
        return s or ""
    return f"{t:%b} {t.day}, {t:%H:%M} KST"


def _dur(sec):
    m = round(sec / 60)
    return f"{m // 60} h {m % 60} min" if m >= 60 else f"{m} min"


def _cut(text, n):
    return text if len(text) <= n else text[:n - 1] + "…"


def _fit(text, px):
    """Cut a label to an estimated pixel width (Hangul ~14px, Latin ~8px at 14px type)."""
    w = lambda ch: 14 if ord(ch) > 0x1100 else 8
    if sum(map(w, text)) <= px:
        return text
    out, used = "", 14
    for ch in text:
        if used + w(ch) > px:
            break
        out, used = out + ch, used + w(ch)
    return out.rstrip() + "…"


def _lane_svg(r):
    g = r["graph"]
    if not g["nodes"]:
        return ""
    by = {n["id"]: n for n in g["nodes"]}
    rows = max(n["row"] for n in g["nodes"]) + 1
    H = 40 + rows * ROW
    pos = lambda n: (COLS[n["col"]][0], 40 + n["row"] * ROW)
    parts = [f'<text class="colhead" x="{x}" y="16">{_e(t)}</text>' for x, t in COLS.values()]
    for e in g["edges"]:
        a, b = by[e["src"]], by[e["dst"]]
        (ax, ay), (bx, by_) = pos(a), pos(b)
        if a["col"] == b["col"]:  # design chain runs downward between the marks
            parts.append(f'<path class="edge" d="M{ax + 7} {ay + 17} V{by_ + 7}"/>')
            continue
        x1, y1, x2, y2 = ax + COL_W[a["col"]] + 4, ay + 12, bx - 4, by_ + 12
        hot = a["kind"] == "picked" or (a["kind"] in ("plan", "critic", "replan") and b["kind"] == "outcome")
        if ORDER.index(b["col"]) - ORDER.index(a["col"]) == 1:  # elbow: the vertical run sits in the column gap
            d = f"M{x1} {y1} H{(x1 + x2) / 2} V{y2} H{x2}"
        else:  # skip columns along the free band under the headers
            d = f"M{x1} {y1} H{x1 + 10} V30 H{x2 - 10} V{y2} H{x2}"
        parts.append(f'<path class="edge{" hot" if hot else ""}" d="{d}"/>')
    for n in g["nodes"]:
        x, y = pos(n)
        w, kind = COL_W[n["col"]], n["kind"]
        cls = f'node {kind}' + (f' sev-{n["sev"]}' if n["sev"] else "")
        mark = {"picked": f'<circle cx="{x + 7}" cy="{y + 12}" r="5" class="m-pick"/>',
                "cand": f'<circle cx="{x + 7}" cy="{y + 12}" r="4" class="m-cand"/>',
                "theme": f'<rect x="{x + 1}" y="{y + 5}" width="12" height="12" rx="2" class="m-sev"/>',
                "defect": f'<path d="M{x + 7} {y + 4} l7 14 h-14z" class="m-defect"/>',
                "method": f'<path d="M{x + 1} {y + 12} h12" class="m-method"/>',
                "outcome": f'<circle cx="{x + 7}" cy="{y + 12}" r="6" class="m-out"/>'}.get(kind, f'<circle cx="{x + 7}" cy="{y + 12}" r="3.5" class="m-design"/>')
        parts.append(f'<g class="{cls}" tabindex="0" role="button" data-title="{_e(n["title"])}" data-body="{_e(n["body"])}">'
                     f'<rect class="hit" x="{x + (16 if n["col"] == "design" else -2)}" y="{y}" width="{w + (-14 if n["col"] == "design" else 4)}" height="{ROW - 6}" rx="5"/>{mark}'
                     f'<text x="{x + 22}" y="{y + 17}">{_e(_fit(n["label"], w - 24))}</text><title>{_e(n["title"])}</title></g>')
    return f'<svg viewBox="-8 0 {LANE_W} {H}" width="{LANE_W}" height="{H}" role="img" aria-label="{_e(r["id"])} research graph">{"".join(parts)}</svg>'


def _sentence(r):
    s = r["story"]
    reached = max((v["gi"] for v in r["spans"].values()), default=-1)
    where = f"the run reached the {GROUPS[reached][0]} stage" if reached >= 0 else "no stage has been logged yet"
    n = len(s["candidates"])
    if s["pick"]:
        t = s["pick"]["title"] or s["pick"]["id"]
        lead = f"Of {n} candidate{'s' * (n != 1)}, {s['pick']['id']} \u201c{t}\u201d was picked{' automatically by the campaign' if s['pick']['auto'] else ''}, and {where}."
    elif n:
        lead = f"{n} candidate{'s were' if n != 1 else ' was'} proposed but none is picked yet, and {where}."
    else:
        lead = f"There are no hypothesis candidates yet, and {where}."
    fs = (s.get("critique") or {}).get("findings", [])
    heavy = [f for f in fs if f["severity"] in ("blocker", "major")]
    st = s["status"]
    earlier = s["parks"] - (st["kind"] == "park")
    why = f"It paused {earlier} time{'s' * (earlier != 1)} ({'mostly in design' if s['design_parks'] >= earlier else 'along the way'}) and resumed. " if earlier and reached > 1 else ""
    if st["kind"] == "running":
        why += "No stop is recorded; " + (f"the last record is {s['last']['stage']} ({_kst_s(s['last']['utc'])})." if s["last"]["utc"] else "the log ends here.")
    else:
        why += _stop_ko(st["raw"]) + "."
        if heavy and st["kind"] in ("park", "wait") and reached == 1:
            names = list(dict.fromkeys(f["theme"] for f in heavy))
            why += f" {len(heavy)} blocker or major defect{'s' * (len(heavy) != 1)} remain; themes: {', '.join(names[:3])}{' and others' if len(names) > 3 else ''}."
    if s["verdict"]:
        learned = f"Verdict: {VERDICT_KO.get(s['verdict'], s['verdict'])} ({s['verdict']})." + (f" {s['verdict_note']}" if s["verdict_note"] else "")
        if s["tagged"]:
            learned += f" Tagged: frozen with {len(s['tags'])} unresolved finding{'s' * (len(s['tags']) != 1)} ({', '.join(s['tags'])})."
    elif fs:
        c = {k: sum(f["severity"] == k for f in fs) for k in SEV}
        learned = f"Design review found {len(fs)} defect{'s' * (len(fs) != 1)} (" + ", ".join(f"{v} {SEV_KO[k]}" for k, v in c.items() if v) + ")."
    else:
        learned = "No verdict or review file yet."
    hyp = [h for h in s["hypotheses"] if h["status"]]
    if hyp:
        learned += " Hypotheses: " + ", ".join(f"{h['id']} {'survives' if h['status'] == 'survives' else 'refuted' if h['status'] == 'refuted' else h['status']}" for h in hyp) + "."
    return lead + " " + why, learned


def _track(r):
    s = r["story"]
    reached = max((v["gi"] for v in r["spans"].values()), default=-1)
    stopped = bool(s["stop"])
    cells = []
    for i, (g, _) in enumerate(GROUPS):
        state = "done" if i < reached or (i == reached and s["status"]["kind"] == "done") else "at" if i == reached else "todo"
        if i == reached and stopped:
            state = "halt"
        cells.append(f'<li class="{state}"><span>{_e(g)}</span></li>')
    return f'<ol class="track" aria-label="stage progress">{"".join(cells)}</ol>'


def _run_block(r):
    s = r["story"]
    lead, learned = _sentence(r)
    calls = f'{s["calls"]}' if s["calls"] else "no records"
    facts = [("Model calls", calls), ("Elapsed time", _dur(s["wall"]) if s["wall"] else "no records"),
             ("Started", _kst_s(r["first"]) if r["first"] else "no records")] + ([("Program", r["program"])] if r["program"] else [])
    return (f'<article class="run" id="{_e(r["id"])}"><p class="rid">{_e(r["id"])}</p><h2>{_e(s["question"])}</h2>'
            f'<p class="lead">{_e(lead)}</p><p class="learned"><span>Learned</span> {_e(learned)}</p>{_track(r)}'
            f'<dl>{"".join(f"<div><dt>{_e(k)}</dt><dd>{_e(v)}</dd></div>" for k, v in facts)}</dl></article>')


def _evidence(r):
    s = r["story"]
    svg = _lane_svg(r)
    notes = []
    if not s["candidates"]:
        notes.append("No hypothesis candidates: candidates.json does not exist yet.")
    if not s["rounds"]:
        notes.append("No design record: the plan and critic stages have not run yet.")
    if not s.get("critique"):
        notes.append("No review result: critique.json is missing.")
    if not s["defects"]:
        notes.append("No linked data defects: no entry in memory/global/data_defects.json points to this run.")
    if s.get("critique") and not s["recurring"]:
        notes.append("No defect class in the method ledger overlaps with another run.")
    if not s["helix"]:
        notes.append("No concept graph: the helix sync output (helix/overview/concepts.jsonl) is missing.")
    fs = (s.get("critique") or {}).get("findings", [])
    items = "".join(f'<li class="sev-{f["severity"]}"><b>{_e(SEV_KO[f["severity"]])}</b> <span class="fid">{_e(f["theme"])}, {_e(f["id"])}</span>'
                    f'<q lang="en">{_e(f["claim"])}</q></li>' for f in sorted(fs, key=lambda f: SEV.index(f["severity"])))
    return (f'<details class="lane" {"open" if r.get("_open") else ""}><summary><span class="rid">{_e(r["id"])}</span> {_e(_cut(s["question"], 60))}</summary>'
            + (f'<div class="scroll">{svg}</div><aside class="inspect" aria-live="polite"><p class="hint">Click a node to see its full text here.</p></aside>' if svg else "")
            + (f'<ul class="empty">{"".join(f"<li>{_e(x)}</li>" for x in notes)}</ul>' if notes else "")
            + (f'<h4>Review findings, full text</h4><ul class="findings">{items}</ul>' if items else "") + '</details>')


def _gantt(data, runs):
    crit = set(data["pert"]["critical_path"])
    bars = sorted(((r["id"], g, s) for r in runs for g, s in r["spans"].items()), key=lambda b: (b[2]["start"], b[2]["gi"]))
    if not bars:
        return '<p class="emptyline">No run has stage records (events.jsonl).</p>'
    t0 = min(_t(s["start"]) for *_, s in bars)
    span = max(max(_t(s["end"]) for *_, s in bars) - t0, dt.timedelta(minutes=1)).total_seconds()
    X = lambda t: 220 + 690 * (t - t0).total_seconds() / span
    g = []
    for i in range(6):
        t = t0 + dt.timedelta(seconds=span * i / 5)
        g.append(f'<line class="tick" x1="{X(t):.1f}" x2="{X(t):.1f}" y1="26" y2="{36 + 28 * len(bars)}"/>'
                 f'<text class="axis" x="{X(t):.1f}" y="18" text-anchor="middle">{_e(_kst_s(t.strftime("%Y-%m-%dT%H:%M:%SZ")))}</text>')
    for i, (rid, grp, s) in enumerate(bars):
        x1, x2, y = X(_t(s["start"])), X(_t(s["end"])), 34 + i * 28
        g.append(f'<text x="0" y="{y + 15}">{_e(_cut(rid, 18))} <tspan class="axis">{_e(grp)}</tspan></text>'
                 f'<rect x="{x1:.1f}" y="{y + 2}" width="{max(x2 - x1, 4):.1f}" height="16" rx="3" fill="{STAGE_COLORS[s["gi"] % 5]}"'
                 + (' class="crit"' if f"{rid}:{grp}" in crit else "") + f'><title>{_e(rid)} {_e(grp)} {s["hours"] * 60:.0f} min</title></rect>'
                 f'<text class="axis" x="{max(x2, x1 + 4) + 6:.1f}" y="{y + 15}">{s["hours"] * 60:.0f} min</text>')
    legend = "".join(f'<span><i style="background:{STAGE_COLORS[i]}"></i>{_e(n)}</span>' for i, (n, _) in enumerate(GROUPS))
    return (f'<p class="legend">{legend}<span><i class="critkey"></i>critical path {data["pert"]["hours"] * 60:.0f} min</span></p>'
            f'<div class="scroll"><svg viewBox="0 0 {LANE_W} {50 + 28 * len(bars)}" width="{LANE_W}" height="{50 + 28 * len(bars)}" role="img" aria-label="stage Gantt chart, Korea time">{"".join(g)}</svg></div>')


def _roles(runs):
    names = sorted({k for r in runs for k in r["roles"]})
    if not names:
        return '<p class="emptyline">No per-role time records (calls.jsonl).</p>'
    h = lambda sec: f"{sec / 60:.0f} min" if sec < 3600 else f"{sec / 3600:.1f} h"
    body = "".join(f'<tr><th scope="row">{_e(r["id"])}</th>' + "".join(f'<td>{h(r["roles"][n]["seconds"])}<small>{r["roles"][n]["calls"]} calls</small></td>' if n in r["roles"] else "<td>–</td>" for n in names)
                   + f'<td>{h(sum(x["seconds"] for x in r["roles"].values()))}</td></tr>' for r in runs)
    total = "".join(f'<td>{h(sum(r["roles"].get(n, {}).get("seconds", 0) for r in runs))}</td>' for n in names)
    return (f'<div class="scroll"><table><thead><tr><th>Run</th>{"".join(f"<th>{_e(n)}</th>" for n in names)}<th>Total</th></tr></thead><tbody>{body}'
            f'<tr class="sum"><th scope="row">Total</th>{total}<td>{h(sum(x["seconds"] for r in runs for x in r["roles"].values()))}</td></tr></tbody></table></div>')


CSS = """
:root{--paper:#faf9f5;--ink:#141413;--soft:#5e5d59;--rule:#e8e6dc;--mid:#b0aea5;--accent:#d97757;--accent-ink:#a9502f;--blue:#6a9bcc;--green:#788c5d;--wash:#f0eee6;
--head:Poppins,"Apple SD Gothic Neo","Noto Sans KR","Malgun Gothic",Arial,sans-serif;--body:Lora,Georgia,"Apple SD Gothic Neo","Noto Sans KR","Malgun Gothic",serif;color-scheme:light}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--paper:#141413;--ink:#faf9f5;--soft:#b0aea5;--rule:#2f2e2b;--mid:#6f6d66;--accent-ink:#e8987a;--wash:#1e1d1b;color-scheme:dark}}
:root[data-theme="dark"]{--paper:#141413;--ink:#faf9f5;--soft:#b0aea5;--rule:#2f2e2b;--mid:#6f6d66;--accent-ink:#e8987a;--wash:#1e1d1b;color-scheme:dark}
*{box-sizing:border-box}html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--paper);color:var(--ink);font:16px/1.7 var(--body);word-break:keep-all;overflow-wrap:anywhere}
main{max-width:1040px;margin:0 auto;padding:56px 24px 96px}
header{margin-bottom:56px}header h1{font:600 34px/1.2 var(--head);letter-spacing:-.01em;margin:0 0 12px}
header p{margin:0;color:var(--soft);font-size:15px;max-width:62ch}
h2,h3,h4{font-family:var(--head);font-weight:600}
section{margin-top:72px}section>h3{font-size:21px;margin:0 0 6px}section>p.sub{margin:0 0 24px;color:var(--soft);font-size:15px;max-width:66ch}
.run{padding:28px 0 32px 24px;border-left:2px solid var(--rule);margin:0 0 8px}
.run:target{border-left-color:var(--accent)}
.rid{font:500 13px/1 var(--head);color:var(--soft);margin:0 0 10px;letter-spacing:.02em}
.run h2{font-size:23px;line-height:1.4;margin:0 0 14px;max-width:40ch}
.lead{font-size:18px;line-height:1.75;margin:0 0 10px;max-width:62ch}
.learned{font-size:16px;color:var(--soft);margin:0 0 22px;max-width:66ch}.learned span{font-family:var(--head);font-weight:600;color:var(--ink);margin-right:6px}
.track{display:flex;list-style:none;margin:0 0 18px;padding:0;max-width:560px;gap:4px}
.track li{flex:1;font:500 13px/1 var(--head);color:var(--mid)}.track li::before{content:"";display:block;height:4px;border-radius:2px;background:var(--rule);margin-bottom:8px}
.track li.done,.track li.at{color:var(--ink)}.track li.done::before{background:var(--ink)}.track li.at::before{background:var(--accent)}
.track li.halt{color:var(--accent-ink)}.track li.halt::before{background:repeating-linear-gradient(90deg,var(--accent) 0 6px,transparent 6px 9px)}
dl{display:flex;flex-wrap:wrap;gap:8px 32px;margin:0;font-size:14px}dl div{display:flex;gap:8px}dt{color:var(--soft)}dd{margin:0;font-variant-numeric:tabular-nums}
details.lane{border-top:1px solid var(--rule);padding:4px 0}details.lane:last-of-type{border-bottom:1px solid var(--rule)}
summary{cursor:pointer;padding:14px 0;font:500 16px/1.4 var(--head);list-style:none;display:flex;gap:12px;align-items:baseline}
summary::-webkit-details-marker{display:none}summary::before{content:"+";font-weight:400;color:var(--soft);width:14px;flex:none}details[open]>summary::before{content:"\\2212"}
summary .rid{margin:0;flex:none}
summary:focus-visible,.node:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.scroll{overflow-x:auto;-webkit-overflow-scrolling:touch}svg{display:block;font-family:var(--head)}
svg text{font-size:14px;fill:var(--ink)}svg .colhead{font-size:13px;fill:var(--soft);font-weight:500}svg .axis{font-size:13px;fill:var(--soft)}
.edge{fill:none;stroke:var(--mid);stroke-width:1.2}.edge.hot{stroke:var(--accent);stroke-width:1.8}
.node{cursor:pointer;outline:none}.node .hit{fill:var(--paper)}.node:hover .hit,.node.sel .hit{fill:var(--wash)}
.m-pick{fill:var(--accent)}.node.picked text{font-weight:600}.m-cand{fill:none;stroke:var(--mid);stroke-width:1.5}.node.cand text{fill:var(--soft)}
.m-design{fill:var(--ink)}.node.replan .m-design{fill:none;stroke:var(--ink)}
.m-sev{fill:var(--mid)}.sev-blocker .m-sev{fill:var(--accent-ink);stroke:var(--ink);stroke-width:1.5}.sev-major .m-sev{fill:var(--accent)}.sev-minor .m-sev{fill:none;stroke:var(--soft);stroke-width:1.5}.sev-known .m-sev{fill:var(--blue)}
.m-defect{fill:none;stroke:var(--accent-ink);stroke-width:1.5}.m-method{stroke:var(--blue);stroke-width:3}.m-out{fill:none;stroke:var(--ink);stroke-width:2}
.inspect{margin:8px 0 12px;padding:14px 18px;background:var(--wash);border-radius:8px;font-size:15px;max-width:76ch}.inspect .hint{margin:0;color:var(--soft)}
.inspect h5{font:600 15px/1.4 var(--head);margin:0 0 8px}.inspect pre{margin:0;white-space:pre-wrap;font:inherit;line-height:1.65}
ul.empty{margin:4px 0 16px;padding-left:18px;color:var(--soft);font-size:14px}
h4{font-size:15px;margin:20px 0 8px}ul.findings{list-style:none;padding:0;margin:0 0 20px;font-size:14px}
.findings li{padding:10px 0 10px 14px;border-left:3px solid var(--mid);margin-bottom:6px}.findings b{font-family:var(--head);font-weight:600;margin-right:8px}
.findings .sev-blocker{border-color:var(--accent-ink)}.findings .sev-major{border-color:var(--accent)}.findings .sev-known{border-color:var(--blue)}
.findings .fid{color:var(--soft)}.findings q{display:block;quotes:none;font-family:Lora,Georgia,serif;line-height:1.6;margin-top:4px;max-width:78ch}
.legend{display:flex;flex-wrap:wrap;gap:6px 18px;font:13px/1.4 var(--head);color:var(--soft);margin:0 0 12px}.legend i{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:6px;vertical-align:-1px}
.legend .critkey,rect.crit{outline:none;stroke:var(--ink);stroke-width:2}.legend .critkey{border:2px solid var(--ink)}
line.tick{stroke:var(--rule)}
table{border-collapse:collapse;font:14px/1.4 var(--head);min-width:520px}th,td{padding:9px 14px 9px 0;text-align:left;border-bottom:1px solid var(--rule);font-weight:400;vertical-align:top}
thead th{color:var(--soft);font-weight:500}tbody th{font-weight:500}td small{display:block;color:var(--soft);font-size:12px}tr.sum th,tr.sum td{font-weight:600;border-bottom:0}
.emptyline{color:var(--soft);font-size:15px}
details.fold>summary{font-size:18px}
footer{margin-top:72px;color:var(--soft);font-size:13px;font-family:var(--head)}
@media (max-width:640px){main{padding:32px 16px 64px}header{margin-bottom:36px}header h1{font-size:27px}.run{padding:22px 0 24px 16px}.run h2{font-size:20px}.lead{font-size:17px}section{margin-top:52px}}
@media print{body{background:#fff;color:#000}main{max-width:none;padding:0}details>summary::before{display:none}.inspect{display:none}.scroll{overflow:visible}svg{max-width:100%;height:auto}.run{break-inside:avoid}}
@media (prefers-reduced-motion:reduce){*{scroll-behavior:auto}}
"""

JS = """document.querySelectorAll('details.lane').forEach(function(d){var a=d.querySelector('.inspect');if(!a)return;
function show(n){d.querySelectorAll('.node.sel').forEach(function(x){x.classList.remove('sel')});n.classList.add('sel');
a.innerHTML='';var h=document.createElement('h5');h.textContent=n.dataset.title;a.appendChild(h);
if(n.dataset.body){var p=document.createElement('pre');p.textContent=n.dataset.body;a.appendChild(p)}}
d.querySelectorAll('.node').forEach(function(n){n.addEventListener('click',function(){show(n)});
n.addEventListener('keydown',function(e){if(e.key==='Enter'||e.key===' '){e.preventDefault();show(n)}})})});
var h=location.hash&&document.getElementById(decodeURIComponent(location.hash.slice(1)));
if(h){document.querySelectorAll('details.lane').forEach(function(d){if(d.querySelector('summary .rid').textContent===h.id)d.open=true})}
window.addEventListener('beforeprint',function(){document.querySelectorAll('details').forEach(function(d){d.open=true})});"""


def render(data):
    runs = sorted(data["runs"].values(), key=lambda r: (r["first"] or "~", r["id"]), reverse=True)  # newest first
    for i, r in enumerate(runs):
        r["_open"] = len(runs) <= 5 or i == 0
    judged = sum(bool(r["story"]["verdict"]) for r in runs)
    parked = sum(bool(r["story"]["stop"]) and not r["story"]["verdict"] for r in runs)
    stamp = dt.datetime.now(KST)
    counts = ", ".join(f"{v} {k}" for k, v in (("reached a verdict", judged), ("stopped before a verdict", parked), ("continuing with no stop recorded", len(runs) - judged - parked)) if v)
    intro = (f"{len(runs)} run{'s' * (len(runs) != 1)}: {counts}."
             " For each run: what was asked, which hypothesis was picked, and where and why it stopped, with the supporting evidence below.") if runs else \
        "No runs yet. Create one with lab.py new and it will appear here."
    doc = ('<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
           f'<title>Research lineage</title><style>{CSS}</style></head><body><main>'
           f'<header><h1>Research lineage</h1><p>{_e(intro)}</p></header>'
           + "".join(_run_block(r) for r in runs)
           + '<section><h3>Evidence graph</h3><p class="sub">Left to right: hypothesis candidates, design and review rounds, themes of the defects the review found, linked data defects and the method ledger, then the outcome. '
             'The orange circle is the picked hypothesis. Defect squares: solid dark orange is a blocker, orange is a major, an empty square is a minor, blue is a known defect. Click a node to see its full text.</p>'
           + "".join(_evidence(r) for r in runs) + '</section>'
           + '<section><details class="fold"><summary>Schedule and agent time</summary>'
             '<p class="sub">Start and end of each stage group (Korea time). The bold outline is the longest path along the program lineage.</p>'
           + _gantt(data, runs) + '<h4>Model time by role</h4>' + _roles(runs) + '</details></section>'
           + f'<footer>Built from events.jsonl, calls.jsonl, candidates.json, pick.json, novelty.json and critique.json in runs/*/, plus methods/ledger.jsonl and '
             f'memory/global/data_defects.json. {stamp:%Y-%m-%d %H:%M} KST</footer>'
           f'</main><script>{JS}</script></body></html>')
    for r in runs:
        r.pop("_open", None)
    (core.ROOT / "lineage.html").write_text(doc)


def lineage(a):
    data = build()
    (core.ROOT / "lineage.json").write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n")
    render(data)
    secs = sum(x["seconds"] for r in data["runs"].values() for x in r["roles"].values())
    print(f'lineage: {len(data["runs"])} runs · critical path {data["pert"]["hours"]:.2f}h ({len(data["pert"]["critical_path"])} stages) · agent {secs/3600:.2f}h → lineage.html')


def selftest():
    with tempfile.TemporaryDirectory() as tmp, mock.patch.object(core, "ROOT", Path(tmp)):
        root = Path(tmp)
        (root / "programs/p").mkdir(parents=True)
        (root / "programs/p/program.json").write_text(json.dumps(dict(question="q", runs=["a", "b", "c"], history=[])))
        spec = {"a": [("survey", "2026-01-01T00:00:00Z"), ("plan", "2026-01-01T01:00:00Z"), ("plan", "2026-01-01T02:00:00Z")],
                "b": [("survey", "2026-01-02T00:00:00Z"), ("explore", "2026-01-02T05:00:00Z"), ("park", "2026-01-02T05:00:00Z")],
                "c": [("survey", "2026-01-03T00:00:00Z"), ("park", "2026-01-03T00:30:00Z")]}
        for rid, evs in spec.items():
            d = root / "runs" / rid
            d.mkdir(parents=True)
            (d / "run.json").write_text(json.dumps(dict(id=rid, program="p")))
            (d / "events.jsonl").write_text("".join(json.dumps(dict(utc=u, stage=s, msg="m", reason="<b>x</b>")) + "\n" for s, u in evs))
        (root / "runs/a/calls.jsonl").write_text('{"role":"researcher","seconds":3600,"ok":true}\n{"role":"researcher","seconds":1800,"ok":false}\n{"role":"reviewer","seconds":600,"ok":true}\n')
        # run c: a full design story (candidates, pick, novelty, critic rounds, findings of every severity, a linked defect, a recurring class)
        c = root / "runs/c"
        (c / "brief.md").write_text("# 질문 <i>c</i>\n\n본문\n")
        (c / "candidates.json").write_text(json.dumps([dict(id="C1", title="첫 가설", value=3.0, hypothesis="h1"), dict(id="C2", title="둘째 가설", value=None, hypothesis="h2"),
                                                       dict(id="C3", title="셋째", value=1.0, hypothesis="h3")]))
        (c / "pick.json").write_text(json.dumps(dict(id="C2", title="둘째 가설")))
        (c / "novelty.json").write_text(json.dumps(dict(C1=dict(verdict="unclear"), C2=dict(verdict="novel"))))
        (c / "critique.json").write_text(json.dumps(dict(verdict="fix_first", known_defects=["D-look-ahead"], findings=[
            dict(id="holdout-exposure", severity="blocker", claim="Holdout seeds are readable.", fix="seal"),
            dict(id="score-vs-hypothesis-metric", severity="major", claim="Score is not the hypothesis."),
            dict(id="interpreter-version-not-pinned", severity="minor", claim="Python not pinned."),
            dict(id="look-ahead", severity="known", claim="known look-ahead", defect="D-look-ahead")])))
        (c / "events.jsonl").write_text("".join(json.dumps(x) + "\n" for x in [
            dict(utc="2026-01-03T00:00:00Z", stage="survey", msg="field"), dict(utc="2026-01-03T00:05:00Z", stage="campaign", msg="auto-pick by campaign"),
            dict(utc="2026-01-03T00:10:00Z", stage="plan", msg="drafted /x/y/PROTOCOL.md"), dict(utc="2026-01-03T00:12:00Z", stage="critic", msg="fix_first: 1 blockers, 1 majors"),
            dict(utc="2026-01-03T00:15:00Z", stage="replan", msg="design revised"), dict(utc="2026-01-03T00:18:00Z", stage="critic", msg="fix_first: 0 blockers, 1 majors"),
            dict(utc="2026-01-03T00:30:00Z", stage="park", msg="design unresolved after 1 replan rounds", reason="design unresolved after 1 replan rounds <b>x</b>")]))
        (root / "memory/global").mkdir(parents=True)
        (root / "memory/global/data_defects.json").write_text(json.dumps([dict(id="D-look-ahead", topic="look-ahead", status="open", runs=["zz"], claim="future rows")]))
        (root / "methods").mkdir()
        (root / "methods/ledger.jsonl").write_text(json.dumps(dict(kind="finding", run="zz", id="q", **{"class": "score-vs-hypothesis-metric"})) + "\n")
        data = build()
        r = data["runs"]
        assert (r["a"]["parent"], r["b"]["parent"], r["c"]["parent"]) == (None, "a", "b"), r
        assert r["a"]["spans"][GROUPS[1][0]]["hours"] == 1 and r["b"]["spans"][GROUPS[2][0]]["start"] == "2026-01-02T05:00:00Z"
        assert r["a"]["roles"]["researcher"] == dict(seconds=5400, calls=2, ok=1) and r["c"]["parked"].startswith("design unresolved")
        assert data["pert"]["critical_path"] == [f"{k}:{GROUPS[g][0]}" for k, g in (("a", 0), ("a", 1), ("b", 0), ("b", 2), ("c", 0), ("c", 1))], data["pert"]
        short = analyze([dict(id="x", estimate=1), dict(id="y", depends_on=["x"], estimate=1), dict(id="z", depends_on=["x"], estimate=5)])
        assert short["critical_path"] == ["x", "z"] and short["hours"] == 6
        g, kinds = r["c"]["graph"], {}
        for n in g["nodes"]:
            kinds[n["kind"]] = kinds.get(n["kind"], 0) + 1
        assert kinds == dict(picked=1, cand=2, plan=1, critic=2, replan=1, theme=4, defect=1, method=1, outcome=1), kinds
        # picked->plan, plan->critic->replan->critic, last critic->4 themes, look-ahead theme->defect, metric theme->method, last critic->outcome
        assert len(g["edges"]) == 1 + 3 + 4 + 1 + 1 + 1, g["edges"]
        assert {n["sev"] for n in g["nodes"] if n["kind"] == "theme"} == set(SEV)
        assert g["nodes"][0]["id"] == "c:C2" and dict(src="t:future information and data timing", dst="x:D-look-ahead") in g["edges"]
        assert r["c"]["story"]["pick"]["auto"] and r["c"]["story"]["question"] == "질문 <i>c</i>"
        assert [(n["kind"], n["label"]) for n in r["b"]["graph"]["nodes"]] == [("outcome", f"Stopped in {GROUPS[2][0]}")] and not r["b"]["graph"]["edges"]
        assert [n["kind"] for n in r["a"]["graph"]["nodes"]] == ["plan", "plan", "outcome"] and r["a"]["graph"]["nodes"][-1]["label"] == "In progress"
        lineage(None)
        page = (root / "lineage.html").read_text()
        assert "&lt;b&gt;x&lt;/b&gt;" in page and "<b>x</b>" not in page and "<i>c</i>" not in page and (root / "lineage.json").exists()
        assert "/x/y/" not in page and 'id="c"' in page and f"was picked automatically by the campaign, and the run reached the {GROUPS[1][0]} stage" in page
        assert "No hypothesis candidates: candidates.json does not exist yet." in page and "No concept graph" in page and "No review result" in page
        live = [dict(stage="plan", msg="drafted"), dict(stage="campaign", msg="USER: fix the design first (x)"), dict(stage="campaign", msg="run replan")]
        assert _status(live)["kind"] == "running" and _status(live[:2])["kind"] == "wait"
        assert _status(live[:2] + [dict(stage="campaign", msg="DONE (invalid). Pivot")])["kind"] == "done"
        assert "Design still failed review after 1 revision, so it stopped" in page and page.count('<details class="lane"') == 3
    print(f"lineage selftest: ok ({len(r)} runs, critical {len(data['pert']['critical_path'])} tasks, graph {len(g['nodes'])} nodes/{len(g['edges'])} edges)")


if __name__ == "__main__":
    selftest()
