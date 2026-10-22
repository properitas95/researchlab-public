"""Workspace knowledge graph from the papers it read: kg/kg.json and an offline kg/kg.html (board link: Knowledge graph).
Nodes: papers, typed concepts, runs, candidates, critic findings, data defects, datasets. Every edge carries provenance.
Concepts come from papers/<id>/card.json; a card without typed concepts costs ONE cheap model call, cached in papers/<id>/concepts.json."""
import html
import itertools
import json
import os
import re
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

from lab import core
from lab.combine import NOISE
from lab.concepts import norm, ws

KINDS = ("method", "dataset", "metric", "task")  # typed concepts; claims are a fifth, untyped-by-model kind
MAP = dict(method="method", model="method", dataset="dataset", market="dataset", instrument="dataset", metric="metric",
           task="task", finding="claim", claim="claim")
LABEL = dict(paper="Paper", method="Method", dataset="Dataset", metric="Metric", task="Task", claim="Claim", run="Run",
             candidate="Candidate", finding="Critic finding", defect="Data defect", data="Local data")
CALLS = Counter()  # model calls made by the current build (read by the selftest and the CLI)


def _json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _items(v):
    return [v] if isinstance(v, (str, dict)) else v if isinstance(v, list) else []


def _card_concepts(card):
    """(type, name, quote) rows a card already names; [] when it has none."""
    rows = []
    for key, kind in (("concepts", None), ("methods", "method"), ("datasets", "dataset"), ("metrics", "metric"), ("tasks", "task")):
        for x in _items(card.get(key)):
            name, typ, quote = (x, kind, "") if isinstance(x, str) else (x.get("name") or x.get("text"), kind or x.get("type"), x.get("quote", "")) if isinstance(x, dict) else (None, None, "")
            typ = MAP.get(str(typ).lower())
            if isinstance(name, str) and name.strip() and typ:
                rows.append((typ, ws(name), ws(quote) if isinstance(quote, str) else ""))
    return rows


def _model(pdir, pid, title, text, root):
    """One haiku call; quotes must occur in the paper text. Result is cached so a rebuild is free."""
    provider = os.environ.get("LAB_KG_PROVIDER", "claude")
    model = os.environ.get("LAB_KG_MODEL", "haiku") if provider == "claude" else None
    reply = core.invoke(provider, core.prompt("kg_concepts", id=pid, title=title, text=text[:14000]), root, model, False, timeout=300)
    CALLS["model"] += 1
    got = core.last_json(reply)
    got = got.get("concepts") if isinstance(got, dict) else got
    src, rows, dropped = ws(text), [], 0
    for c in got if isinstance(got, list) else []:
        typ = MAP.get(str(c.get("type") if isinstance(c, dict) else "").lower())
        q, n = (ws(c.get("quote") or ""), ws(str(c.get("name") or ""))) if isinstance(c, dict) else ("", "")
        if typ in KINDS and n and q and q in src:
            rows.append(dict(type=typ, name=n, quote=q))
        else:
            dropped += 1
    (pdir / "concepts.json").write_text(json.dumps(dict(paper=pid, model=model or provider, concepts=rows, dropped_quotes=dropped),
                                                    ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return [(c["type"], c["name"], c["quote"]) for c in rows]


def build(root=None):
    root = Path(root or core.ROOT)
    CALLS.clear()
    nodes, edges, seen = {}, {}, set()
    rel = lambda p: str(Path(p).relative_to(root)).replace("\\", "/")

    def node(nid, typ, label, **props):
        n = nodes.setdefault(nid, dict(id=nid, type=typ, label=label, props={}))
        for k, v in props.items():
            if isinstance(v, list):
                cur = n["props"].setdefault(k, [])
                cur += [x for x in v if x not in cur]
            else:
                n["props"].setdefault(k, v)
        return n

    def edge(s, t, kind, **prov):
        e = edges.setdefault((s, t, kind), dict(s=s, t=t, rel=kind, w=0, prov=[]))
        e["w"] += 1
        if prov and prov not in e["prov"] and len(e["prov"]) < 8:
            e["prov"].append(prov)

    # papers and their concepts
    members = defaultdict(set)  # paper -> concept ids, for co-occurrence
    stance = {}  # paper id -> (relation, why, claim, file) from card.json, relative to the run question
    failed = []
    for pdir in sorted(p for p in (root / "papers").glob("*") if p.is_dir() and ((p / "meta.json").is_file() or (p / "card.json").is_file())):
        pid = pdir.name
        meta, card = _json(pdir / "meta.json", {}), _json(pdir / "card.json", {})
        meta, card = meta if isinstance(meta, dict) else {}, card if isinstance(card, dict) else {}
        title = ws(str(meta.get("title") or card.get("title") or pid))
        pid = str(meta.get("id") or pid)
        pnode = f"paper:{pid}"
        node(pnode, "paper", title, id=pid, authors=meta.get("authors") or card.get("authors") or "", year=meta.get("year") or "",
             url=meta.get("url") or "", files=[rel(p) for p in (pdir / "meta.json", pdir / "card.json", pdir / "paper.md") if p.is_file()])
        if card.get("relation") in ("supports", "refutes", "neutral"):
            cl = card.get("claim")
            stance[pid] = (card["relation"], ws(str(card.get("why") or "")), ws(cl if isinstance(cl, str) else str(cl.get("text") or "") if isinstance(cl, dict) else ""), f"papers/{pdir.name}/card.json")
        rows, where = _card_concepts(card), "card.json"
        if not rows:
            cached = _json(pdir / "concepts.json")
            if isinstance(cached, dict) and isinstance(cached.get("concepts"), list):
                rows, where = [(c["type"], c["name"], c["quote"]) for c in cached["concepts"] if isinstance(c, dict) and c.get("type") in KINDS], "concepts.json"
            elif (pdir / "paper.md").is_file():
                try:
                    rows, where = _model(pdir, pid, title, (pdir / "paper.md").read_text(encoding="utf-8", errors="replace"), root), "concepts.json"
                except Exception as e:  # one failed paper must not stop the graph; the next build retries it
                    failed.append(f"{pid}: {str(e)[:120]}")
        for i, (typ, name, quote) in enumerate(rows):
            if NOISE.search(name) or not norm(name):
                continue
            cid = f"{typ}:{norm(name)}"
            node(cid, typ, name, names=[name], papers=[pid])
            edge(pnode, cid, "mentions", paper=pid, quote=quote, quote_id=f"{pid}#c{i}", file=f"papers/{pdir.name}/{where}")
            members[pid].add(cid)
        for i, c in enumerate(_items(card.get("claims"))[:12]):
            text, quote = (c, "") if isinstance(c, str) else (c.get("text"), c.get("quote", "")) if isinstance(c, dict) else (None, "")
            if isinstance(text, str) and norm(text):
                cid = f"claim:{norm(text)[:90]}"
                node(cid, "claim", ws(text)[:140], text=ws(text), papers=[pid])
                edge(pnode, cid, "claims", paper=pid, quote=ws(quote), quote_id=f"{pid}#k{i}", file=f"papers/{pdir.name}/card.json")
    paper_ids = {n["props"]["id"]: nid for nid, n in nodes.items() if n["type"] == "paper"}

    # concept co-occurrence: weight = papers in common
    pair = defaultdict(set)
    for pid, cs in members.items():
        for a, b in itertools.combinations(sorted(cs)[:40], 2):
            pair[(a, b)].add(pid)
    for (a, b), ps in pair.items():
        edges[(a, b, "co-occurs")] = dict(s=a, t=b, rel="co-occurs", w=len(ps), prov=[dict(papers=sorted(ps))])

    # workspace data
    for m in sorted((root / "data").glob("*/*")) if (root / "data").is_dir() else []:
        if m.name.lower() == "manifest.json":
            info = _json(m, {})
            did = f"data:{m.parent.name}"
            node(did, "data", m.parent.name, file=rel(m), manifest=info if isinstance(info, dict) else {})
            for nid, n in list(nodes.items()):
                if n["type"] == "dataset" and nid != did and norm(n["label"]) == norm(m.parent.name):
                    edge(did, nid, "backs", file=rel(m))

    # defects, runs, candidates, findings
    defects = _json(root / "memory" / "global" / "data_defects.json", [])
    for r in defects if isinstance(defects, list) else []:
        node(f"defect:{r['id']}", "defect", r["id"], status=r.get("status", ""), claim=r.get("claim", ""), runs=r.get("runs", []), file="memory/global/data_defects.json")
    runs = sorted(p for p in (root / "runs").glob("*") if (p / "run.json").is_file()) if (root / "runs").is_dir() else []
    cnames = [(nid, n["label"].lower()) for nid, n in nodes.items() if n["type"] in ("method", "dataset") and len(n["label"]) >= 3]
    for d in runs:
        rid = f"run:{d.name}"
        brief = d / "brief.md"
        head = next((ln.lstrip("# ").strip() for ln in brief.read_text(encoding="utf-8", errors="replace").splitlines() if ln.startswith("#")), "") if brief.is_file() else ""
        node(rid, "run", f"{d.name}: {head}" if head else d.name, heading=head, file=rel(d / "run.json"))
        cands, pick = _json(d / "candidates.json", []), _json(d / "pick.json", {})
        picked = pick.get("id") if isinstance(pick, dict) else None
        nov = _json(d / "novelty.json", {})
        nov = nov if isinstance(nov, dict) else {}
        for c in cands if isinstance(cands, list) else []:
            if not isinstance(c, dict) or not c.get("id"):
                continue
            cid = f"cand:{d.name}:{c['id']}"
            text = " ".join(str(c.get(k, "")) for k in ("title", "hypothesis", "test"))
            node(cid, "candidate", f"{c['id']} {c.get('title', '')}".strip(), picked=c["id"] == picked, hypothesis=c.get("hypothesis", ""), run=d.name,
                 value=c.get("value"), file=rel(d / "candidates.json"))
            edge(rid, cid, "proposes", file=rel(d / "candidates.json"), picked=c["id"] == picked)
            for pid, pn in paper_ids.items():
                if re.search(r"(?<![\w.])" + re.escape(pid) + r"(?![\w])", text):
                    edge(cid, pn, "cites", file=rel(d / "candidates.json"), paper=pid, quote=pid)
            low = text.lower()
            for pid in c.get("papers") if isinstance(c.get("papers"), list) else []:
                if str(pid) in paper_ids:
                    edge(cid, paper_ids[str(pid)], "cites", file=rel(d / "candidates.json"), paper=str(pid), quote=str(pid))
            nv = nov.get(c["id"]) if isinstance(nov.get(c["id"]), dict) else {}
            if nv.get("verdict") == "prior_work":  # the papers novelty search named as already doing this
                for cl in nv.get("closest") if isinstance(nv.get("closest"), list) else []:
                    pid = str(cl.get("id_or_url", "")).strip() if isinstance(cl, dict) else ""
                    if pid in paper_ids:
                        edge(paper_ids[pid], cid, "prior-work", file=rel(d / "novelty.json"), paper=pid, why=ws(str(cl.get("why", ""))))
            for nid, name in cnames:
                if name in low:
                    edge(cid, nid, "uses", file=rel(d / "candidates.json"), quote=name)
        if d == runs[-1]:  # cards carry one stance, relative to the question being researched: attach it to the latest run
            for pid, (rl, why, claim, f) in sorted(stance.items()):
                if pid in paper_ids:
                    edge(paper_ids[pid], rid, rl, file=f, paper=pid, why=why, quote=claim)
        try:
            from lab import director
            ex = _json(d / "run.json", {}).get("exp_dir")
            v = (director._verdict(Path(ex), _json(d / "run.json", {}).get("q", "Q1")) or "") if ex else ""
        except Exception:
            v = ""
        res = "supported" if v.startswith("supported") else "refuted" if v in ("no_improvement", "refuted") else ""
        if res and picked and f"cand:{d.name}:{picked}" in nodes:
            edge(rid, f"cand:{d.name}:{picked}", res, file=rel(d / "run.json"), verdict=v)
        crit = _json(d / "critique.json", {})
        for f in crit.get("findings", []) if isinstance(crit, dict) else []:
            if isinstance(f, dict) and f.get("severity") in ("blocker", "major", "known") and f.get("id"):
                fid = f"finding:{d.name}:{f['id']}"
                node(fid, "finding", f"{f['id']} ({f['severity']})", severity=f["severity"], run=d.name, evidence=ws(str(f.get("evidence") or f.get("claim") or ""))[:400],
                     klass=f.get("class", ""), file=rel(d / "critique.json"))
                edge(rid, fid, "found", file=rel(d / "critique.json"))
                if f.get("defect") and f"defect:{f['defect']}" in nodes:
                    edge(fid, f"defect:{f['defect']}", "instance-of", file=rel(d / "critique.json"), defect=f["defect"])

    ns, es = list(nodes.values()), sorted(edges.values(), key=lambda e: (e["s"], e["t"], e["rel"]))
    count = Counter(n["type"] for n in ns)
    stats = dict(papers=count["paper"], concepts=sum(count[k] for k in KINDS + ("claim",)), edges=len(es), nodes=len(ns),
                 by_type=dict(count), by_rel=dict(Counter(e["rel"] for e in es)), model_calls=CALLS["model"], failed=failed)
    kg = dict(built_utc=core.now(), stats=stats, nodes=ns, edges=es)
    out = root / "kg"
    out.mkdir(parents=True, exist_ok=True)
    (out / "kg.json").write_text(json.dumps(kg, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    (out / "kg.html").write_text(_page(kg), encoding="utf-8")
    return kg


def _page(kg):
    s = kg["stats"]
    line = f"{s['papers']} papers, {s['concepts']} concepts, {s['edges']} links"
    blob = json.dumps(dict(nodes=kg["nodes"], edges=kg["edges"], label=LABEL), ensure_ascii=False).replace("</", "<\\/")
    return (PAGE.replace("{{LINE}}", html.escape(line)).replace("{{BUILT}}", html.escape(kg["built_utc"]))
            .replace("{{DATA}}", blob))


PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Knowledge graph</title>
<style>
:root{--bg:#f6f3ee;--panel:#fbf9f5;--ink:#26231f;--mute:#6d675e;--line:#d9d3c8;--edge:#b8b0a2;--acc:#2f6b66;--acc-bg:#dfeae8;--c-paper:#2f6b66;--c-method:#b0703a;--c-dataset:#4f7d9c;--c-metric:#8b6aa1;--c-task:#6c8f4d;--c-claim:#a79f90;--c-run:#4a453e;--c-candidate:#c4573f;--c-finding:#b3383a;--c-defect:#7d4545;--c-data:#5f7c7a;--sup:#3f8a52;--ref:#b8433d;--neu:#8c857a;--pri:#b0703a}
@media (prefers-color-scheme:dark){:root{--bg:#1d1b18;--panel:#25221e;--ink:#ebe6dd;--mute:#a49d91;--line:#3b362f;--edge:#5a5348;--acc:#6fb3ab;--acc-bg:#25403d;--c-paper:#6fb3ab;--c-method:#d99a5f;--c-dataset:#7fb0cf;--c-metric:#b79ccb;--c-task:#9bc07b;--c-claim:#7d766a;--c-run:#c9c2b5;--c-candidate:#e08a74;--c-finding:#e0777a;--c-defect:#b98585;--c-data:#8fb0ae;--sup:#6fc283;--ref:#e5827b;--neu:#9a9488;--pri:#d99a5f}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.55 system-ui,-apple-system,"Apple SD Gothic Neo","Malgun Gothic","Noto Sans KR",sans-serif;height:100vh;display:flex;flex-direction:column}
header{padding:14px 20px;border-bottom:1px solid var(--line);display:flex;flex-wrap:wrap;gap:8px 20px;align-items:baseline}
h1{font-size:20px;margin:0}.sum{font-size:16px;font-weight:600;color:var(--acc)}.mute{color:var(--mute);font-size:14px}
.bar{padding:10px 20px;border-bottom:1px solid var(--line);display:flex;flex-wrap:wrap;gap:8px;align-items:center}
.chip{font:inherit;font-size:14px;border:1px solid var(--line);background:var(--panel);color:var(--ink);border-radius:999px;padding:3px 12px;cursor:pointer}
.chip[aria-pressed=true]{background:var(--acc-bg);border-color:var(--acc);color:var(--ink)}
.chip svg{vertical-align:-2px;margin-right:4px}
.sep{width:1px;align-self:stretch;background:var(--line);margin:0 4px}
.rng{display:inline-flex;align-items:center;gap:6px;font-size:14px;color:var(--mute)}.rng input{accent-color:var(--acc);width:90px}.rng[hidden]{display:none}
.rng.off{opacity:.45}
input[type=search]{font:inherit;font-size:14px;padding:5px 12px;border:1px solid var(--line);border-radius:6px;background:var(--panel);color:var(--ink);min-width:220px;margin-left:auto}
main{flex:1;display:flex;min-height:0}
#stage{flex:1;position:relative;min-width:0}
svg#g{width:100%;height:100%;display:block;cursor:grab;touch-action:none}
svg text{font-size:calc(14px / var(--k,1));fill:var(--ink);paint-order:stroke;stroke:var(--bg);stroke-width:calc(3.5px / var(--k,1));pointer-events:none}
.n.paper text{font-weight:600}
.n{cursor:pointer}.n .sh{stroke:var(--bg);stroke-width:calc(1.2px / var(--k,1))}
.n.paper .sh{fill:var(--c-paper)}.n.method .sh{fill:var(--c-method)}.n.dataset .sh{fill:var(--c-dataset)}.n.metric .sh{fill:var(--c-metric)}.n.task .sh{fill:var(--c-task)}.n.claim .sh{fill:var(--c-claim)}.n.run .sh{fill:var(--c-run)}.n.candidate .sh{fill:var(--c-candidate)}.n.finding .sh{fill:var(--c-finding)}.n.defect .sh{fill:var(--c-defect)}.n.data .sh{fill:var(--c-data)}
.n.sel .sh{stroke:var(--ink);stroke-width:calc(3px / var(--k,1))}
.n.dim,.e.dim{opacity:.12}.e{stroke:var(--edge);fill:none;opacity:.7}.e.st{opacity:.9}.e.st-neutral{stroke-dasharray:5 4;opacity:.6}.e.st-supports,.e.st-supported{stroke:var(--sup)}.e.st-refutes,.e.st-refuted{stroke:var(--ref)}.e.st-neutral{stroke:var(--neu)}.e.st-prior-work{stroke:var(--pri)}
.leg{display:inline-flex;gap:10px;align-items:center;font-size:13px;color:var(--mute)}.leg span{display:inline-flex;align-items:center;gap:4px}.leg.off{opacity:.45}
.e.co{stroke-dasharray:3 3;opacity:.45}.e.hot{stroke:var(--acc);opacity:1}
aside{width:340px;max-width:34vw;border-left:1px solid var(--line);background:var(--panel);overflow:auto;padding:16px 18px}
aside h2{font-size:17px;margin:0 0 4px}aside h3{font-size:14px;margin:16px 0 4px;color:var(--mute);font-weight:600}
aside ul{margin:0;padding-left:18px}aside li{margin:4px 0}
blockquote{margin:4px 0;padding:2px 10px;border-left:3px solid var(--acc);color:var(--ink);font-size:14px}
code{font-size:13px;word-break:break-all}a.lk{color:var(--acc);cursor:pointer;text-decoration:underline}
nav#rank{width:260px;max-width:26vw;border-right:1px solid var(--line);background:var(--panel);overflow:auto;padding:14px 14px}
nav#rank h2{font-size:15px;margin:0 0 2px}nav#rank p{margin:0 0 8px;font-size:13px;color:var(--mute);line-height:1.4}
nav#rank ol{list-style:none;margin:0;padding:0}
nav#rank button{font:inherit;font-size:14px;line-height:1.3;display:grid;grid-template-columns:10px 1fr auto;gap:8px;align-items:baseline;width:100%;text-align:left;background:none;border:0;border-radius:6px;padding:5px 6px;color:var(--ink);cursor:pointer}
nav#rank button:hover{background:var(--acc-bg)}nav#rank button[aria-current=true]{background:var(--acc-bg);box-shadow:inset 2px 0 var(--acc)}
nav#rank i{width:9px;height:9px;border-radius:50%;display:inline-block}nav#rank b{font-weight:600;color:var(--mute);font-size:13px;font-variant-numeric:tabular-nums}
.empty{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;text-align:center;padding:32px;color:var(--mute);font-size:16px}
@media (max-width:760px){main{flex-direction:column}nav#rank{width:auto;max-width:none;border-right:0;border-bottom:1px solid var(--line);max-height:28vh}aside{width:auto;max-width:none;border-left:0;border-top:1px solid var(--line);max-height:45vh}}
</style></head><body>
<header><h1>Knowledge graph</h1><span class="sum" id="sum">{{LINE}}</span><span class="mute">Built {{BUILT}} · click a node to see its evidence</span></header>
<div class="bar" id="bar"></div>
<main><nav id="rank" aria-label="Concepts shared by several papers"></nav><div id="stage"><svg id="g" role="img" aria-label="Knowledge graph"></svg><div class="empty" id="empty" hidden></div></div>
<aside id="side"><h2>Select a node</h2><p class="mute">Shows where concepts from papers, run candidates and critic findings came from, with sources. Scroll to zoom, drag to pan.</p></aside></main>
<script type="application/json" id="data">{{DATA}}</script>
<script>
const D=JSON.parse(document.getElementById('data').textContent),L=D.label,NS=D.nodes,ES=D.edges;
const byId=Object.fromEntries(NS.map(n=>[n.id,n]));
const SH={paper:'c',method:'c',dataset:'s',metric:'t',task:'d',claim:'p',run:'s',candidate:'d',finding:'t',defect:'x',data:'s'};
const RING={paper:0,method:1,dataset:1,metric:1,task:1,claim:2,data:2,run:3,candidate:4,finding:5,defect:5};
const on=new Set(Object.keys(L).filter(t=>t!=='claim')),q={s:''},co={on:false,min:2,st:true},CK=new Set(['method','dataset','metric','task']);let sel=null,hov=null,K=1,tx=0,ty=0,pos={},vis=[],ED=[];
const $=id=>document.getElementById(id),svg=$('g'),NSV='http://www.w3.org/2000/svg';
const el=(t,a={},p)=>{const e=document.createElementNS(NSV,t);for(const k in a)e.setAttribute(k,a[k]);if(p)p.appendChild(e);return e};
const esc=s=>String(s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
function shape(t,r){return {c:`M${-r} 0a${r} ${r} 0 1 0 ${2*r} 0a${r} ${r} 0 1 0 ${-2*r} 0`,s:`M${-r} ${-r}h${2*r}v${2*r}h${-2*r}z`,t:`M0 ${-r*1.2}L${r*1.1} ${r*.9}L${-r*1.1} ${r*.9}z`,d:`M0 ${-r*1.3}L${r*1.3} 0L0 ${r*1.3}L${-r*1.3} 0z`,p:`M${-r*.6} 0a${r*.6} ${r*.6} 0 1 0 ${r*1.2} 0a${r*.6} ${r*.6} 0 1 0 ${-r*1.2} 0`,x:`M${-r} ${-r*.35}h${r*.7}v${-r*.65}h${r*.6}v${r*.65}h${r*.7}v${r*.7}h${-r*.7}v${r*.65}h${-r*.6}v${-r*.65}h${-r*.7}z`}[SH[t]]}
const deg={};ES.forEach(e=>{if(e.rel==='co-occurs')return;deg[e.s]=(deg[e.s]||0)+1;deg[e.t]=(deg[e.t]||0)+1});
const ST={supports:'sup',supported:'sup',refutes:'ref',refuted:'ref',neutral:'neu','prior-work':'pri'},hasSt=ES.some(e=>ST[e.rel]);
const maxW=Math.max(1,...ES.filter(e=>e.rel==='co-occurs').map(e=>e.w)),hasCo=ES.some(e=>e.rel==='co-occurs');
const rad=n=>n.type==='paper'?12+Math.min(8,1.6*Math.sqrt(deg[n.id]||0)):5+Math.min(7,2*Math.sqrt(deg[n.id]||0));
function mulberry(a){return()=>{a|=0;a=a+0x6D2B79F5|0;let t=Math.imul(a^a>>>15,1|a);t=t+Math.imul(t^t>>>7,61|t)^t;return((t^t>>>14)>>>0)/4294967296}}
// Deterministic layout: papers fixed on a circle (by id), the run at the centre, everything else relaxed by springs and repulsion,
// so single-paper concepts gather around their paper and shared concepts settle between papers.
function layout(){
  vis=NS.filter(n=>on.has(n.type));const ids=new Set(vis.map(n=>n.id)),es=ED=ES.filter(e=>ids.has(e.s)&&ids.has(e.t)&&(e.rel==='co-occurs'?(co.on&&e.w>=co.min):ST[e.rel]?co.st:true));
  const rnd=mulberry(7),P={},ps=vis.filter(n=>n.type==='paper').sort((a,b)=>a.id<b.id?-1:1),Rp=Math.max(150,24*ps.length);
  const ringR={run:0,candidate:Rp+110,finding:Rp+110,defect:Rp+160,data:Rp+160};
  ps.forEach((n,i)=>{const a=i/ps.length*6.2832-1.5708;P[n.id]={x:Math.cos(a)*Rp,y:Math.sin(a)*Rp,vx:0,vy:0,fix:1}});
  const first={};es.forEach(e=>{if(P[e.s]&&!P[e.t]&&!first[e.t])first[e.t]=e.s;if(P[e.t]&&!P[e.s]&&!first[e.s])first[e.s]=e.t});
  vis.forEach(n=>{if(P[n.id])return;const f=P[first[n.id]],j=()=>(rnd()-.5)*60;P[n.id]=f?{x:f.x*.8+j(),y:f.y*.8+j(),vx:0,vy:0}:{x:j()*3,y:j()*3,vx:0,vy:0}});
  const N=vis.length,it=N>600?60:N>250?150:260;
  for(let k=0;k<it;k++){const c=1-k/it;
    for(let i=0;i<N;i++){const a=P[vis[i].id];for(let j=i+1;j<N;j++){const b=P[vis[j].id];let dx=a.x-b.x,dy=a.y-b.y,d=dx*dx+dy*dy+.01;if(d>40000)continue;const f=2200/d;dx*=f;dy*=f;a.vx+=dx;a.vy+=dy;b.vx-=dx;b.vy-=dy}}
    es.forEach(e=>{const a=P[e.s],b=P[e.t],dx=b.x-a.x,dy=b.y-a.y,d=Math.sqrt(dx*dx+dy*dy)+.01,f=(d-(e.rel==='co-occurs'?110:60))*.02*Math.min(3,1+Math.log(e.w));a.vx+=dx/d*f;a.vy+=dy/d*f;b.vx-=dx/d*f;b.vy-=dy/d*f});
    vis.forEach(n=>{const p=P[n.id];if(p.fix)return;const r=Math.hypot(p.x,p.y)+.01,tr=ringR[n.type];if(tr!==undefined){p.vx-=p.x/r*(r-tr)*.02;p.vy-=p.y/r*(r-tr)*.02}
      p.x+=Math.max(-15,Math.min(15,p.vx))*c*.7;p.y+=Math.max(-15,Math.min(15,p.vy))*c*.7;p.vx*=.5;p.vy*=.5})}
  pos=P;return es}
let gE,gN;
function draw(){
  const es=layout();svg.innerHTML='';const root=el('g',{id:'root'},svg);gE=[];gN={};
  const emp=$('empty');
  if(!NS.some(n=>n.type==='paper')){emp.hidden=false;emp.innerHTML='<div><b>No papers read yet.</b><br>Concepts and sources accumulate here once papers are read (lab.py read).<br>Runs, candidates and critic findings can be viewed with the filters below.</div>'}
  else if(!vis.length){emp.hidden=false;emp.textContent='No nodes of the selected types.'}else emp.hidden=true;
  const eg=el('g',{},root),ng=el('g',{},root);
  const defs=el('defs',{},svg);['sup','ref','neu','pri'].forEach(k=>{const m=el('marker',{id:'ar-'+k,viewBox:'0 0 10 10',refX:9,refY:5,markerWidth:5,markerHeight:5,orient:'auto'},defs);el('path',{d:'M0 0L10 5L0 10z',fill:`var(--${k})`},m)});
  es.forEach(e=>{let a=pos[e.s],b=pos[e.t],st=ST[e.rel];const A={x:a.x,y:a.y},B={x:b.x,y:b.y};
    if(st){const dx=b.x-a.x,dy=b.y-a.y,d=Math.hypot(dx,dy)||1,rt=rad(byId[e.t])+2;B.x=b.x-dx/d*rt;B.y=b.y-dy/d*rt}
    const p=el('line',{class:'e'+(e.rel==='co-occurs'?' co':st?' st st-'+e.rel:''),x1:A.x,y1:A.y,x2:B.x,y2:B.y},eg);if(st)p.setAttribute('marker-end',`url(#ar-${st})`);
    p.style.strokeWidth=((st?1.8:Math.min(3,.7+Math.log(e.w+1)*.5))/K)+'px';gE.push([e,p])});
  vis.forEach(n=>{const p=pos[n.id],g=el('g',{class:'n '+n.type,transform:`translate(${p.x},${p.y})`},ng),r=rad(n);
    el('path',{class:'sh',d:shape(n.type,r)},g);const t=el('text',{},g);t.textContent=n.label.length>28?n.label.slice(0,27)+'\u2026':n.label;t.style.display='none';
    g.onclick=ev=>{ev.stopPropagation();pick(n.id)};g.onmouseenter=()=>{hov=n.id;labels()};g.onmouseleave=()=>{hov=null;labels()};gN[n.id]=[n,g,t,r]});
  apply();view()}
const hit=n=>{const s=q.s.trim().toLowerCase();return !s||(n.label+' '+JSON.stringify(n.props)).toLowerCase().includes(s)};
let NB=new Set();
function apply(){
  const s=q.s.trim();NB=new Set(sel?[sel]:[]);if(sel)ED.forEach(e=>{if(e.s===sel)NB.add(e.t);if(e.t===sel)NB.add(e.s)});
  for(const id in gN){const[n,g]=gN[id];g.classList.toggle('dim',!(hit(n)&&(!sel||NB.has(id))));g.classList.toggle('sel',id===sel)}
  gE.forEach(([e,p])=>{const h=sel&&(e.s===sel||e.t===sel);p.classList.toggle('hot',!!h);p.classList.toggle('dim',!!((sel&&!h)||(s&&!(hit(byId[e.s])&&hit(byId[e.t])))))});
  labels();mark()}
// Labels: papers, runs, candidates, findings and hub concepts (degree >= 3), plus selection, neighbours, hover and search hits.
// Greedy collision pass in screen space, ordered by priority then degree then id (deterministic); a rejected label is simply not shown.
function labels(){
  const cs=[],found=q.s.trim();
  for(const id in gN){const[n]=gN[id],d=deg[id]||0;let pr=-1;
    if(id===sel)pr=0;else if(id===hov)pr=1;else if(sel){if(NB.has(id))pr=2}
    else if(n.type==='paper')pr=3;else if(n.type==='run'||n.type==='candidate'||n.type==='finding')pr=4;else if(d>=3)pr=5;else if(found&&hit(n))pr=6;
    if(pr>=0)cs.push([pr,-d,id])}
  cs.sort((a,b)=>a[0]-b[0]||a[1]-b[1]||(a[2]<b[2]?-1:a[2]>b[2]?1:0));
  const on_=new Set(),box=[],W=s=>{let w=6;for(const c of s)w+=c.charCodeAt(0)>0x2e7f?14:7.4;return w};
  const free=(x0,y0,x1,y1)=>box.every(b=>x1<=b[0]||x0>=b[2]||y1<=b[1]||y0>=b[3]);
  const side=svg.getBoundingClientRect();
  for(const[pr,,id]of cs){const[n,,t,r]=gN[id],p=pos[id],sx=p.x*K+tx,sy=p.y*K+ty,w=W(t.textContent),rr=r*K+4;
    if(sx<-w||sx>side.width+w||sy<-10||sy>side.height+10)continue;
    let place=null;for(const dir of[1,-1]){const x0=dir>0?sx+rr:sx-rr-w,y0=sy-9;if(pr<=1||free(x0,y0,x0+w,y0+18)){place=[dir,x0,y0];break}}
    if(!place)continue;box.push([place[1],place[2],place[1]+w,place[2]+18]);on_.add(id);
    t.setAttribute('x',place[0]*(r+4/K));t.setAttribute('y',5/K);t.setAttribute('text-anchor',place[0]>0?'start':'end')}
  for(const id in gN)gN[id][2].style.display=on_.has(id)?'':'none'}
// "Concepts shared by several papers": top 15 by number of papers.
function rank(){
  const r=NS.filter(n=>CK.has(n.type)&&on.has(n.type)).map(n=>[n,(n.props.papers||[]).length]).filter(x=>x[1]>=2)
    .sort((a,b)=>b[1]-a[1]||(a[0].label.toLowerCase()<b[0].label.toLowerCase()?-1:1)).slice(0,15),a=$('rank');
  a.innerHTML='<h2>Shared by several papers</h2><p>Concepts read in the most papers. Click to select.</p>'+(r.length?'<ol>'+r.map(([n,c])=>`<li><button type="button" data-id="${esc(n.id)}" title="${esc(L[n.type]+': '+n.label)}"><i style="background:var(--c-${n.type})"></i><span>${esc(n.label.length>40?n.label.slice(0,39)+'\u2026':n.label)}</span><b>${c}</b></button></li>`).join('')+'</ol>':'<p>No concept appears in more than one paper yet.</p>');
  a.querySelectorAll('button').forEach(b=>b.onclick=()=>{const id=b.dataset.id,p=pos[id];pick(id);if(p){const r=svg.getBoundingClientRect();tx=r.width/2-p.x*K;ty=r.height/2-p.y*K;view()}})}
function mark(){document.querySelectorAll('#rank button').forEach(b=>b.toggleAttribute('aria-current',b.dataset.id===sel)); document.querySelectorAll('#rank button').forEach(b=>{if(b.dataset.id!==sel)b.removeAttribute('aria-current');else b.setAttribute('aria-current','true')})}
function view(){const r=svg.querySelector('#root');if(!r)return;svg.style.setProperty('--k',K);r.setAttribute('transform',`translate(${tx},${ty}) scale(${K})`);labels()}
function fit(){const b=svg.getBoundingClientRect();let x0=1e9,x1=-1e9,y0=1e9,y1=-1e9;vis.forEach(n=>{const p=pos[n.id];x0=Math.min(x0,p.x);x1=Math.max(x1,p.x);y0=Math.min(y0,p.y);y1=Math.max(y1,p.y)});
  if(!vis.length){K=1;tx=b.width/2;ty=b.height/2;return}K=Math.min(1.3,Math.min(b.width/(x1-x0+160),b.height/(y1-y0+160)));tx=b.width/2-(x0+x1)/2*K;ty=b.height/2-(y0+y1)/2*K}
svg.addEventListener('wheel',e=>{e.preventDefault();const b=svg.getBoundingClientRect(),mx=e.clientX-b.left,my=e.clientY-b.top,k=Math.max(.2,Math.min(6,K*(e.deltaY<0?1.15:1/1.15)));tx=mx-(mx-tx)*k/K;ty=my-(my-ty)*k/K;K=k;view()},{passive:false});
let drag=null;svg.onpointerdown=e=>{drag={x:e.clientX,y:e.clientY,tx,ty};svg.setPointerCapture(e.pointerId)};svg.onpointermove=e=>{if(drag){tx=drag.tx+e.clientX-drag.x;ty=drag.ty+e.clientY-drag.y;view()}};svg.onpointerup=()=>{drag=null};
svg.onclick=()=>{if(sel){sel=null;apply();side(null)}};
function pick(id){sel=id;apply();side(id)}
function side(id){const a=$('side');if(!id){a.innerHTML='<h2>Select a node</h2>';return}
  const n=byId[id],p=n.props,out=[];out.push(`<h2>${esc(n.label)}</h2><div class="mute">${esc(L[n.type])} · <code>${esc(n.id)}</code></div>`);
  const kv=(k,v)=>v!==undefined&&v!==''&&v!==null&&!(Array.isArray(v)&&!v.length)&&out.push(`<h3>${k}</h3><div>${esc(Array.isArray(v)?v.join(', '):typeof v==='object'?JSON.stringify(v):v)}</div>`);
  kv('Authors',p.authors);kv('Year',p.year);kv('URL',p.url);kv('Hypothesis',p.hypothesis);kv('Picked',p.picked===true?'Yes':p.picked===false?'No':'');kv('Status',p.status);kv('Claim',p.claim||p.text);kv('Evidence',p.evidence);kv('Other names',p.names&&p.names.length>1?p.names:'');kv('Appears in papers',p.papers);kv('Files',p.files||p.file);
  const rel=ES.filter(e=>e.s===id||e.t===id).sort((a,b)=>b.w-a.w),grp={};rel.forEach(e=>{const o=e.s===id?e.t:e.s;(grp[(e.s===id?'→ ':'← ')+e.rel]=grp[(e.s===id?'→ ':'← ')+e.rel]||[]).push([o,e])});
  const ord=k=>/support/.test(k)?0:/refute/.test(k)?1:/prior-work/.test(k)?2:/neutral/.test(k)?3:9;
  for(const k of Object.keys(grp).sort((a,b)=>ord(a)-ord(b))){out.push(`<h3>${esc(k)} (${grp[k].length})</h3><ul>`);grp[k].slice(0,40).forEach(([o,e])=>{const m=byId[o];out.push(`<li><a class="lk" data-id="${esc(o)}">${esc(m?m.label:o)}</a> <span class="mute">${esc(L[m?m.type:'']||'')}${e.rel==='co-occurs'?' · shared papers '+e.w:''}</span>`+e.prov.slice(0,3).map(v=>(v.why?`<div>${esc(v.why)}</div>`:'')+(v.quote&&v.rel!=='x'?`<blockquote>${esc(v.quote)}</blockquote>`:'')+(v.file||v.paper?`<div class="mute"><code>${esc(v.file||'')}</code> ${v.quote_id?esc(v.quote_id):''}</div>`:'')).join('')+'</li>')});if(grp[k].length>40)out.push(`<li class="mute">… ${grp[k].length-40} more</li>`);out.push('</ul>')}
  if(!rel.length)out.push('<p class="mute">No links.</p>');a.innerHTML=out.join('');a.querySelectorAll('a.lk').forEach(x=>x.onclick=()=>{const t=x.dataset.id;if(byId[t]&&!on.has(byId[t].type)){on.add(byId[t].type);bar();draw()}pick(t)})}
function bar(){const b=$('bar'),cnt={};NS.forEach(n=>cnt[n.type]=(cnt[n.type]||0)+1);b.innerHTML='';
  Object.keys(L).forEach(t=>{const c=document.createElement('button');c.className='chip';c.type='button';c.setAttribute('aria-pressed',on.has(t));
    c.innerHTML=`<svg width="14" height="14" viewBox="-9 -9 18 18" aria-hidden="true"><path d="${shape(t,6)}" fill="var(--c-${t})"/></svg>${L[t]} ${cnt[t]||0}`;
    c.onclick=()=>{on.has(t)?on.delete(t):on.add(t);if(sel&&!on.has(byId[sel].type))sel=null;bar();draw();fit();view();side(sel);rank()};b.appendChild(c)});
  if(hasSt){const sp=document.createElement('span');sp.className='sep';b.appendChild(sp);
    const c=document.createElement('button');c.className='chip';c.type='button';c.textContent='Stance';c.title='Arrows from papers to the question: supports / refutes / neutral; prior work and verdicts';c.setAttribute('aria-pressed',co.st);
    c.onclick=()=>{co.st=!co.st;bar();draw();fit();view()};b.appendChild(c);
    const lg=document.createElement('span');lg.className='leg'+(co.st?'':' off');
    lg.innerHTML=[['sup','supports','-'],['ref','refutes','-'],['neu','neutral','4 3'],['pri','prior work','-']].map(([k,t,d])=>`<span><svg width="22" height="8" aria-hidden="true"><line x1="0" y1="4" x2="16" y2="4" stroke="var(--${k})" stroke-width="2" ${d==='-'?'':`stroke-dasharray="${d}"`}/><path d="M22 4L15 0.5V7.5z" fill="var(--${k})"/></svg>${t}</span>`).join('');b.appendChild(lg)}
  if(hasCo){const sp=document.createElement('span');sp.className='sep';b.appendChild(sp);
    const c=document.createElement('button');c.className='chip';c.type='button';c.textContent='Co-occurrence';c.title='Dashed links between concepts that share papers';c.setAttribute('aria-pressed',co.on);
    const r=document.createElement('label');r.className='rng'+(co.on?'':' off');r.innerHTML=`min shared papers <input type="range" min="1" max="${Math.max(2,maxW)}" step="1" value="${co.min}" ${co.on?'':'disabled'} aria-label="Minimum shared papers"><b>${co.min}</b>`;
    const inp=r.querySelector('input'),out=r.querySelector('b');inp.oninput=()=>{co.min=+inp.value;out.textContent=co.min;draw();fit();view()};
    c.onclick=()=>{co.on=!co.on;bar();draw();fit();view()};b.appendChild(c);b.appendChild(r)}
  const s=document.createElement('input');s.type='search';s.placeholder='Search (name, quote, path)';s.setAttribute('aria-label','Search');s.value=q.s;
  s.oninput=()=>{q.s=s.value;apply()};s.onkeydown=e=>{if(e.key==='Enter'){const h=vis.find(n=>(n.label+' '+JSON.stringify(n.props)).toLowerCase().includes(q.s.trim().toLowerCase()));if(h&&q.s.trim())pick(h.id)}};b.appendChild(s)}
bar();draw();fit();view();rank();
{const m=/^#(run|node)=(.+)$/.exec(location.hash),t=m&&(m[1]==='run'?'run:':'')+decodeURIComponent(m[2]);if(t&&byId[t])pick(t)}  // card links: #run=<id> or #node=<node id>
let rw=0;new ResizeObserver(()=>{const w=Math.round(svg.getBoundingClientRect().width);if(Math.abs(w-rw)>20){rw=w;fit();view()}}).observe(svg);
</script></body></html>
"""


def kg(a=None):
    k = build()
    s = k["stats"]
    print(f"{s['papers']} papers, {s['concepts']} concepts, {s['edges']} links (nodes {s['nodes']}, model calls {s['model_calls']})")
    print("nodes:", ", ".join(f"{t} {n}" for t, n in sorted(s["by_type"].items())), "\nedges:", ", ".join(f"{t} {n}" for t, n in sorted(s["by_rel"].items())))
    for f in s["failed"]:
        print("failed:", f)
    print(core.ROOT / "kg" / "kg.html")
    return k


def selftest():
    from unittest.mock import patch
    with tempfile.TemporaryDirectory() as t:
        root = Path(t)
        fake = root / "fake.py"
        fake.write_text("import sys, json\nsys.stdin.read()\nopen('calls.txt','a').write('x')\n"
                        "print(json.dumps({'concepts':[{'type':'method','name':'Ledoit-Wolf shrinkage','quote':'We apply Ledoit-Wolf shrinkage to returns.'},"
                        "{'type':'dataset','name':'KOSPI200 daily','quote':'invented sentence'},{'type':'metric','name':'GMV variance','quote':'We report GMV variance.'}]}))\n")
        for pid, title, card in (("2601.00001", "Rank risk", dict(methods=[dict(name="rank correlation", quote="Rank correlation helps.")], datasets=["KOSPI200 daily"],
                                                                  metrics=["GMV variance"], relation="neutral", why="Adjacent only", claims=[dict(text="Rank correlation lowers GMV variance", quote="lowers variance")])),
                                 ("2601.00002", "Shrinkage", dict(relation="supports", why="Closest related work", claim="Shrinkage lowers risk", concepts=[dict(type="method", name="Ledoit-Wolf shrinkage", quote="shrinkage"), dict(type="dataset", name="KOSPI200 daily", quote="KOSPI")])),
                                 ("2601.00003", "No concepts", dict(claims=[]))):
            d = root / "papers" / pid
            d.mkdir(parents=True)
            (d / "meta.json").write_text(json.dumps(dict(id=pid, title=title, authors=["A"], year=2026, url="https://x/" + pid)))
            (d / "card.json").write_text(json.dumps(card))
            (d / "paper.md").write_text("Intro. We apply Ledoit-Wolf shrinkage to returns. We report GMV variance. Done.")
        run = root / "runs" / "r1"
        run.mkdir(parents=True)
        (root / "exp").mkdir()
        (root / "exp/DECISIONS.md").write_text("- Q1 **supported_within_scope** x\n".replace("- Q1", "- 2026 Q1"))
        (run / "run.json").write_text(json.dumps(dict(exp_dir=str(root / "exp"), q="Q1")))
        (run / "novelty.json").write_text(json.dumps(dict(C2=dict(verdict="prior_work", closest=[dict(id_or_url="2601.00001", why="Same idea")]))))
        (run / "brief.md").write_text("# Does shrinkage help?\n")
        (run / "candidates.json").write_text(json.dumps([dict(id="C1", title="Shrink", hypothesis="Using Ledoit-Wolf shrinkage as in 2601.00002 lowers risk", test="x"), dict(id="C2", title="Other", papers=["2601.00003"])]))
        (run / "pick.json").write_text('{"id":"C1"}')
        (run / "critique.json").write_text(json.dumps(dict(findings=[dict(id="look-ahead", severity="known", evidence="e", defect="D-look-ahead"), dict(id="nit", severity="minor")])))
        (root / "memory/global").mkdir(parents=True)
        (root / "memory/global/data_defects.json").write_text(json.dumps([dict(id="D-look-ahead", topic="look-ahead", status="open", runs=["r1"], claim="c")]))
        (root / "data/panel").mkdir(parents=True)
        (root / "data/panel/MANIFEST.json").write_text('{"rows": 3}')
        env = dict(LAB_KG_PROVIDER="cmd:python3 " + str(fake))
        with patch.dict(os.environ, env), patch.object(core, "ROOT", root):
            import contextlib
            import io
            cwd = os.getcwd()
            try:
                os.chdir(root)  # the fake model call logs to ./calls.txt (core.invoke runs it in the workspace)
                k = build()
                s = k["stats"]
                assert s["model_calls"] == 1 and (root / "calls.txt").read_text() == "x", s
                assert s["by_type"] == dict(paper=3, method=2, dataset=1, metric=1, claim=1, run=1, candidate=2, finding=1, defect=1, data=1), s["by_type"]
                assert s["papers"] == 3 and s["concepts"] == 5, s  # invented quote dropped: model gave no dataset for paper 3
                E = {(e["s"], e["t"], e["rel"]): e for e in k["edges"]}
                m = E[("paper:2601.00002", "method:ledoit wolf shrinkage", "mentions")]
                assert m["prov"][0]["quote"] == "shrinkage" and m["prov"][0]["file"] == "papers/2601.00002/card.json"
                assert E[("paper:2601.00003", "method:ledoit wolf shrinkage", "mentions")]["prov"][0]["file"] == "papers/2601.00003/concepts.json"
                assert E[("dataset:kospi200 daily", "metric:gmv variance", "co-occurs")]["w"] == 1
                assert E[("method:ledoit wolf shrinkage", "metric:gmv variance", "co-occurs")]["w"] == 1
                assert E[("dataset:kospi200 daily", "method:rank correlation", "co-occurs")]["prov"][0]["papers"] == ["2601.00001"]
                assert ("cand:r1:C1", "paper:2601.00002", "cites") in E and ("cand:r1:C2", "paper:2601.00002", "cites") not in E
                assert ("cand:r1:C1", "method:ledoit wolf shrinkage", "uses") in E and ("run:r1", "cand:r1:C1", "proposes") in E
                assert E[("paper:2601.00002", "run:r1", "supports")]["prov"][0]["why"] == "Closest related work"
                assert ("paper:2601.00001", "run:r1", "neutral") in E and not any(e["rel"] == "refutes" for e in k["edges"])
                assert E[("paper:2601.00001", "cand:r1:C2", "prior-work")]["prov"][0]["why"] == "Same idea"
                assert ("cand:r1:C2", "paper:2601.00003", "cites") in E and ("run:r1", "cand:r1:C1", "supported") in E
                assert nodes_picked(k) == {"C1": True, "C2": False}
                assert ("run:r1", "finding:r1:look-ahead", "found") in E and ("finding:r1:look-ahead", "defect:D-look-ahead", "instance-of") in E
                assert not any(n["id"] == "finding:r1:nit" for n in k["nodes"])
                assert (root / "papers/2601.00003/concepts.json").is_file() and json.loads((root / "papers/2601.00003/concepts.json").read_text())["dropped_quotes"] == 1
                page = (root / "kg/kg.html").read_text()
                assert "3 papers, 5 concepts, %d links" % s["edges"] in page and "http" not in page.replace("https://x/", "").replace("http://www.w3.org/2000/svg", ""), "page"
                assert json.loads((root / "kg/kg.json").read_text())["stats"]["edges"] == s["edges"]
                k2 = build()  # cache reused: no second model call, same graph
                assert k2["stats"]["model_calls"] == 0 and (root / "calls.txt").read_text() == "x" and k2["edges"] == k["edges"]
                from lab import combine
                cs = combine.concepts(root)
                assert {c["name"] for c in cs["method"]} >= {"rank correlation", "Ledoit-Wolf shrinkage"} and any(c["paper"] == "2601.00002" for c in cs["method"])
                assert any(c["name"] == "GMV variance" for c in cs["metric"]) and any(c["name"] == "KOSPI200 daily" for c in cs["dataset"])
                with patch.dict(os.environ, dict(LAB_KG_PROVIDER="cmd:exit 1")):  # a failing model call must not break the build
                    (root / "papers/2601.00003/concepts.json").unlink()
                    k3 = build()
                    assert k3["stats"]["failed"] and k3["stats"]["papers"] == 3
                (root / "papers").rename(root / "p0")
                empty = build()
                assert empty["stats"]["papers"] == 0 and "0 papers, 0 concepts" in (root / "kg/kg.html").read_text()
            finally:
                os.chdir(cwd)
    print("kg selftest ok (nodes/edges by type, provenance, concept cache, combine reads kg, empty workspace)")


def nodes_picked(k):
    return {n["label"].split()[0]: n["props"]["picked"] for n in k["nodes"] if n["type"] == "candidate"}
