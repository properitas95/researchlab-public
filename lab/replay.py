"""Per-round state snapshots under <workspace>/replay/ plus an offline replay/index.html; LAB_REPLAY=0 turns recording off.
A snapshot keeps copies of pages, board cards and small text artifacts only, never data files."""
import json
import os
import re
import shutil
import urllib.request
from pathlib import Path

from lab import core, director
from lab.util import esc as _e, load as _json

CAP = 2 * 1024 * 1024  # bytes per copied file
RUN_FILES = ("brief.md", "field_papers.md", "candidates.json", "pick.json", "CRITIQUE.md", "PROTOCOL.md", "RESULT.md")
SEV = ("blocker", "major", "minor")


def _fetch_board():
    """Every project's tasks, read-only (one project per run plus LAB); (compact tasks, note)."""
    from lab import kanban
    def get(path):
        with urllib.request.urlopen(urllib.request.Request(kanban._url() + "/api/" + path, headers={"X-Agent": "researchlab"}), timeout=5) as r:
            return json.load(r)
    try:
        rows = [t | {"project": p["key"]} for p in get("projects") for t in get("tasks?project=" + p["key"])]
        return [{"key": t.get("key"), "project": t["project"], "title": t.get("title"), "type": t.get("type"), "status": t.get("status"), "labels": t.get("labels", []),
                 "parent": t.get("parent"), "criteria": [{"text": c.get("text"), "done": bool(c.get("done"))} for c in t.get("criteria") or []]}
                for t in rows], ""
    except Exception as e:  # board down / refusing: the snapshot is still useful
        return [], f"board unavailable: {e}"


def _copy(src, dst, skipped):
    if not src.is_file():
        return False
    if src.stat().st_size > CAP:
        skipped.append(f"{src.name} ({src.stat().st_size} bytes > {CAP})")
        return False
    shutil.copy2(src, dst)
    return True


def _state(d, stage, c, skipped, note):
    exp, q = Path(c.get("exp_dir", d / "exp")), c.get("q", "Q1")
    try:
        nxt = director.next_cmd(d)
    except Exception as e:
        nxt = f"unavailable: {e}"
    kg = _json(core.ROOT / "kg" / "kg.json").get("stats", {})
    crit = _json(d / "critique.json")
    sev = {s: sum(f.get("severity") == s for f in crit.get("findings", [])) for s in SEV}
    cands = _json(d / "candidates.json", [])
    calls = (d / "calls.jsonl")
    return {"run": d.name, "stage": stage, "utc": core.now(), "director": {"next_cmd": nxt}, "verdict": director._verdict(exp, q),
            "counts": {"papers": kg.get("papers", 0), "candidates": len(cands) if isinstance(cands, list) else 0, "findings": sev,
                       "calls": sum(1 for _ in calls.open(encoding="utf-8", errors="replace")) if calls.is_file() else 0},
            "skipped": skipped, "note": note}


def record(run_dir, stage):
    """Snapshot after a completed stage; never raises into the caller."""
    if os.environ.get("LAB_REPLAY", "1") == "0" or not core.FOUND:
        return None
    try:
        d = Path(run_dir).resolve()
        base = core.ROOT / "replay"
        base.mkdir(exist_ok=True)
        n = 1 + max([int(m[1]) for p in base.iterdir() if (m := re.match(r"(\d+)-", p.name))] or [0])
        snap = base / f"{n:03d}-{d.name}-{re.sub(r'[^\w.-]', '_', stage)}"
        snap.mkdir()
        c, skipped = core.cfg(d), []
        for src, name in ((core.ROOT / "lineage.html", "lineage.html"), (core.ROOT / "kg" / "kg.html", "kg.html"), (core.ROOT / "kg" / "kg.json", "kg.json")):
            _copy(src, snap / name, skipped)
        tasks, note = _fetch_board()
        if not note:
            (snap / "board.json").write_text(json.dumps(tasks, ensure_ascii=False) + "\n", encoding="utf-8")
        exp = Path(c.get("exp_dir", d / "exp"))
        for name in RUN_FILES:  # the run folder first, then its experiment folder
            _copy(d / name, snap / name, skipped) or _copy(exp / name, snap / name, skipped)
        (snap / "state.json").write_text(json.dumps(_state(d, stage, c, skipped, note), indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        index()
        return snap
    except Exception as e:
        print(f"[replay] skipped: {e}")
        return None


CSS = """:root{--paper:#faf9f5;--ink:#141413;--soft:#5e5d59;--rule:#e8e6dc;--mid:#b0aea5;--accent:#d97757;--accent-ink:#a9502f;--wash:#f0eee6;
--head:Poppins,"Apple SD Gothic Neo","Noto Sans KR",Arial,sans-serif;--body:Lora,Georgia,serif;color-scheme:light}
@media (prefers-color-scheme:dark){:root{--paper:#141413;--ink:#faf9f5;--soft:#b0aea5;--rule:#2f2e2b;--mid:#6f6d66;--accent-ink:#e8987a;--wash:#1e1d1b;color-scheme:dark}}
*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);font:15px/1.5 var(--body)}
header{padding:20px 28px 12px;border-bottom:1px solid var(--rule)}h1{font:600 22px var(--head);margin:0 0 4px}.sub{color:var(--soft);font-size:13px;font-family:var(--head)}
.wrap{display:grid;grid-template-columns:300px 1fr;min-height:calc(100vh - 80px)}
nav{border-right:1px solid var(--rule);overflow-y:auto;max-height:calc(100vh - 80px)}
nav a{display:block;padding:9px 16px;border-bottom:1px solid var(--rule);color:inherit;text-decoration:none;cursor:pointer}
nav a:hover{background:var(--wash)}nav a.on{background:var(--wash);box-shadow:inset 3px 0 var(--accent)}
nav b{font:600 13px var(--head)}nav small{display:block;color:var(--soft);font-size:12px}
main{padding:18px 28px;min-width:0}.bar{display:flex;gap:8px;align-items:center;margin-bottom:12px}
button{font:600 13px var(--head);background:var(--wash);color:var(--ink);border:1px solid var(--rule);border-radius:6px;padding:5px 12px;cursor:pointer}
button:hover{border-color:var(--accent)}button.on{background:var(--accent);color:#fff;border-color:var(--accent)}
dl{display:grid;grid-template-columns:150px 1fr;gap:4px 12px;margin:0 0 18px}dt{color:var(--soft);font-family:var(--head);font-size:13px}dd{margin:0;overflow-wrap:anywhere}
.board{display:flex;gap:10px;overflow-x:auto;padding-bottom:8px}.col{min-width:190px;flex:1;background:var(--wash);border-radius:8px;padding:8px}
.col h4{font:600 12px var(--head);margin:0 0 8px;text-transform:uppercase;letter-spacing:.04em;color:var(--soft)}
.card{background:var(--paper);border:1px solid var(--rule);border-radius:6px;padding:6px 8px;margin-bottom:6px;font-size:13px}
.card i{font-style:normal;font-family:var(--head);font-size:11px;color:var(--soft)}.tag{display:inline-block;border:1px solid var(--rule);border-radius:4px;padding:0 5px;margin:2px 3px 0 0;font:11px var(--head);color:var(--soft)}
iframe{width:100%;height:calc(100vh - 220px);min-height:420px;border:1px solid var(--rule);border-radius:6px;background:#fff}
.proj{font:600 15px var(--head);margin:14px 0 6px}.note{color:var(--accent-ink);font-family:var(--head);font-size:13px}
@media (max-width:760px){.wrap{grid-template-columns:1fr}nav{max-height:200px;border-right:0}}"""

JS = r"""const S=DATA,$=s=>document.querySelector(s),esc=t=>String(t??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const COLS=['backlog','todo','in_progress','review','done'];let cur=0,tab='board';
function show(i){if(!S.length)return;cur=Math.max(0,Math.min(S.length-1,i));location.hash=cur+1;
document.querySelectorAll('nav a').forEach((a,k)=>a.classList.toggle('on',k===cur));
const s=S[cur],st=s.state,c=st.counts||{},f=c.findings||{};
let h=`<div class="bar"><button id="pv">&larr; prev</button><button id="nx">next &rarr;</button><span class="sub">round ${cur+1} of ${S.length}</span>`+
['board','lineage','kg'].map(t=>`<button data-t="${t}" class="${t===tab?'on':''}">${t==='kg'?'knowledge graph':t}</button>`).join('')+`</div>`;
h+=`<dl><dt>run / stage</dt><dd>${esc(st.run)} / ${esc(st.stage)}</dd><dt>time (UTC)</dt><dd>${esc(st.utc)}</dd><dt>next</dt><dd>${esc((st.director||{}).next_cmd)}</dd>`+
`<dt>verdict</dt><dd>${esc(st.verdict||'pending')}</dd><dt>counts</dt><dd>${c.papers} papers, ${c.candidates} candidates, findings ${f.blocker||0} blocker / ${f.major||0} major / ${f.minor||0} minor, ${c.calls} model calls</dd>`+
`<dt>files</dt><dd>${s.files.map(x=>`<a href="${esc(s.dir)}/${esc(x)}">${esc(x)}</a>`).join(' &middot; ')}</dd>`+
((st.skipped||[]).length?`<dt>skipped</dt><dd>${esc(st.skipped.join('; '))}</dd>`:'')+`</dl>`;
if(tab==='board'){if(s.board_note)h+=`<p class="note">${esc(s.board_note)}</p>`;else{
h+=[...new Set(s.board.map(x=>x.project||'LAB'))].map(pk=>{const B=s.board.filter(x=>(x.project||'LAB')===pk);return `<h3 class="proj">${esc(pk)} <span class="sub">${B.length} cards</span></h3><div class="board">`+COLS.map(k=>{const t=B.filter(x=>x.status===k);return `<div class="col"><h4>${k.replace('_',' ')} (${t.length})</h4>`+t.map(x=>{const n=x.criteria.length,d=x.criteria.filter(y=>y.done).length;
return `<div class="card"><i>${esc(x.key)} &middot; ${esc(x.type)}${n?` &middot; criteria ${d}/${n}`:''}</i><br>${esc(x.title)}<br>${(x.labels||[]).map(l=>`<span class="tag">${esc(l)}</span>`).join('')}</div>`}).join('')+`</div>`}).join('')+'</div>'}).join('')}}
else{const p=tab==='kg'?'kg.html':'lineage.html';h+=s.files.includes(p)?`<iframe src="${esc(s.dir)}/${p}"></iframe>`:`<p class="note">${p} was not present in this snapshot</p>`}
$('main').innerHTML=h;$('#pv').onclick=()=>show(cur-1);$('#nx').onclick=()=>show(cur+1);
document.querySelectorAll('[data-t]').forEach(b=>b.onclick=()=>{tab=b.dataset.t;show(cur)})}
$('nav').innerHTML=S.map((s,k)=>`<a data-k="${k}"><b>${String(k+1).padStart(3,'0')} &middot; ${esc(s.state.run)} &middot; ${esc(s.state.stage)}</b><small>${esc(s.state.utc)}</small><small>${esc(s.summary)}</small></a>`).join('');
document.querySelectorAll('nav a').forEach(a=>a.onclick=()=>show(+a.dataset.k));
document.addEventListener('keydown',e=>{if(e.key==='ArrowLeft')show(cur-1);if(e.key==='ArrowRight')show(cur+1)});
show((parseInt(location.hash.slice(1))||S.length)-1)"""


def index():
    """Rebuild replay/index.html from the snapshot folders (embedded data, so it opens from file://)."""
    base = core.ROOT / "replay"
    base.mkdir(exist_ok=True)
    snaps = []
    for p in sorted(x for x in base.iterdir() if x.is_dir() and re.match(r"\d+-", x.name)):
        st = _json(p / "state.json")
        if not st:
            continue
        c, f = st.get("counts", {}), st.get("counts", {}).get("findings", {})
        board = _json(p / "board.json", [])
        snaps.append({"dir": p.name, "state": st, "board": board if isinstance(board, list) else [],
                      "board_note": "" if (p / "board.json").is_file() else (st.get("note") or "board.json missing"),
                      "files": sorted(x.name for x in p.iterdir() if x.is_file()),
                      "summary": f"{st.get('verdict') or 'pending'} · {c.get('candidates', 0)} cand · {f.get('blocker', 0)}b/{f.get('major', 0)}M · {c.get('calls', 0)} calls"})
    data = json.dumps(snaps, ensure_ascii=False).replace("</", "<\\/")
    page = (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>Research replay</title><style>{CSS}</style></head><body><header><h1>Research replay</h1>'
            f'<div class="sub">{len(snaps)} snapshots, one per completed stage. Static copies; nothing here runs.</div></header>'
            f'<div class="wrap"><nav></nav><main>{"<p class=note>No snapshots yet.</p>" if not snaps else ""}</main></div>'
            f'<script>const DATA={data};{JS}</script></body></html>')
    (base / "index.html").write_text(page, encoding="utf-8")
    return base / "index.html"


def replay_cmd(a):
    p = index()
    print(p)
    if getattr(a, "open", False):
        import subprocess
        import sys
        subprocess.run(["open" if sys.platform == "darwin" else "xdg-open" if sys.platform != "win32" else "explorer", str(p)])


def selftest():
    import tempfile
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from types import SimpleNamespace
    from unittest.mock import patch
    tasks = [{"key": "LAB-1", "title": "T</script>", "type": "epic", "status": "review", "labels": ["run"], "criteria": [{"text": "x", "done": True}], "description": "dropped"}]

    class H(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_GET(self):
            assert self.headers["X-Agent"] == "researchlab"
            assert self.path in ("/api/projects", "/api/tasks?project=LAB", "/api/tasks?project=RUNA"), self.path
            body = json.dumps({"/api/projects": [{"key": "LAB"}, {"key": "RUNA"}], "/api/tasks?project=LAB": tasks,
                               "/api/tasks?project=RUNA": [{"key": "RUNA-1", "title": "Survey", "type": "spike", "status": "done", "labels": ["group:ideas"]}]}[self.path]).encode()
            self.send_response(200); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
    server = HTTPServer(("127.0.0.1", 0), H)
    thread = threading.Thread(target=server.serve_forever); thread.start()
    try:
        with tempfile.TemporaryDirectory() as t:
            root = Path(t).resolve()
            d = root / "runs" / "r1"
            (d / "exp").mkdir(parents=True)
            core.save_cfg(d, {"id": "r1", "exp_dir": str(d / "exp"), "q": "Q1"})
            (d / "brief.md").write_text("# B\n"); (d / "candidates.json").write_text(json.dumps([{"id": "C1"}, {"id": "C2"}]))
            (d / "critique.json").write_text(json.dumps({"findings": [{"severity": "major"}, {"severity": "major"}, {"severity": "minor"}]}))
            (d / "calls.jsonl").write_text("{}\n{}\n{}\n")
            (d / "exp" / "PROTOCOL.md").write_text("p")
            (d / "exp" / "big.bin").write_bytes(b"0" * 10)  # data files are never in RUN_FILES
            (d / "CRITIQUE.md").write_bytes(b"x" * (CAP + 1))  # over the cap: skipped, not copied
            (root / "lineage.html").write_text("<html>lineage</html>")
            with patch.object(core, "ROOT", root), patch.object(core, "FOUND", True), \
                    patch.dict(os.environ, {"LAB_KANBAN_URL": f"http://127.0.0.1:{server.server_port}", "LAB_REPLAY": "1"}):
                s1 = record(d, "survey")
                s2 = record(d, "plan")
                assert s1.name == "001-r1-survey" and s2.name == "002-r1-plan", (s1, s2)
                assert {p.name for p in s1.iterdir()} == {"lineage.html", "board.json", "state.json", "brief.md", "candidates.json", "PROTOCOL.md"}
                st = json.loads((s1 / "state.json").read_text())
                assert st["counts"] == {"papers": 0, "candidates": 2, "findings": {"blocker": 0, "major": 2, "minor": 1}, "calls": 3} and st["skipped"] and st["director"]["next_cmd"]
                board = json.loads((s1 / "board.json").read_text())
                assert board[0]["key"] == "LAB-1" and "description" not in board[0] and board[0]["criteria"] == [{"text": "x", "done": True}]
                assert [(t["project"], t["key"]) for t in board] == [("LAB", "LAB-1"), ("RUNA", "RUNA-1")]
                page = (root / "replay" / "index.html").read_text()
                assert "001-r1-survey" in page and "002-r1-plan" in page and "T</script>" not in page and "Research replay" in page
                server.shutdown(); thread.join(); server.server_close()  # board down: note, still a snapshot
                s3 = record(d, "critic")
                assert s3.name == "003-r1-critic" and not (s3 / "board.json").exists() and "board unavailable" in json.loads((s3 / "state.json").read_text())["note"]
                assert "board unavailable" in (root / "replay" / "index.html").read_text()
                with patch.dict(os.environ, {"LAB_REPLAY": "0"}):
                    assert record(d, "off") is None and len(list((root / "replay").glob("0*"))) == 3
                with patch("builtins.print") as pr:
                    replay_cmd(SimpleNamespace(open=False))
                    assert pr.call_args[0][0] == root / "replay" / "index.html"
    finally:
        if thread.is_alive():
            server.shutdown(); thread.join()
    print("replay selftest ok")
