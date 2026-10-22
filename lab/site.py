"""Single-file offline research site: python3 lab/site.py -> site/index.html (stdlib only)."""
import glob
import html
import json
import math
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "site", "index.html")
ORDER = ["accepted", "submitted", "under_review", "revise", "internal_review", "draft", "idea"]
STAGE_KO = {"idea": "구상", "draft": "초고", "internal_review": "내부 검토", "submitted": "제출", "under_review": "심사 중",
            "revise": "수정 중", "accepted": "채택", "camera_ready": "최종본", "published": "출간", "rejected": "미채택", "withdrawn": "철회"}
TRACK = ["구상", "초고", "검토", "제출", "채택"]
POS = {"idea": 0, "draft": 1, "internal_review": 2, "submitted": 3, "under_review": 3, "revise": 3, "rejected": 3, "withdrawn": 3,
       "accepted": 4, "camera_ready": 4, "published": 4}
E = lambda s: html.escape(str(s if s is not None else ""), quote=True)


def load(path, default=None):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def text(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except Exception:
        return ""


def clip(s, n):
    s = re.sub(r"\s+", " ", str(s or "")).strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def sid(i):
    return str(i).encode().hex()


# ---------- section 2: working papers ----------
def track_html(stage):
    p = POS.get(stage, 0)
    cells = "".join('<i class="%s"></i>' % ("on" if k < p else "cur" if k == p else "") for k in range(5))
    return '<span class="track" role="img" aria-label="진행 단계 %s">%s</span>' % (E(TRACK[p]), cells)


def history_line(p):
    # the paper's finding, not the registry's bookkeeping note
    return clip(p.get("result") or "", 90)


def pivot_html(p):
    pv = p.get("pivot")
    if not pv:
        return ""
    steps = []
    for h in p.get("history", []):
        steps.append((h.get("venue", ""), STAGE_KO.get(h.get("stage"), h.get("stage"))))
    seq = "".join('<span class="%s"><b>%s</b> %s</span>' % ("bad" if s[1] == "미채택" else "", E(s[0]), E(s[1])) for s in steps)
    return '<div class="pivot" aria-label="피벗 경로"><span class="lab">피벗 경로</span>%s</div>' % seq


def row_html(p):
    pos = POS.get(p["stage"], 0)
    return ('<li><button class="prow" data-id="%s" aria-controls="gp-%s"><span class="pt">%s</span>'
            '<span class="pv">%s</span><span class="ps st%d">%s</span>%s<span class="ph">%s</span></button>%s</li>') % (
        E(p["id"]), sid(p["id"]), E(p["title"]), E(p.get("venue") or "미정"), pos, E(STAGE_KO.get(p["stage"], p["stage"])),
        track_html(p["stage"]), E(history_line(p)), pivot_html(p))


def layout(refs):
    """One ring keeps dense bibliographies and their hit targets separate."""
    cx, cy = 500, 400
    groups = {}
    for r in refs:
        groups.setdefault(r.get("group") or "기타", []).append(r)
    order = sorted(groups, key=lambda g: (-len(groups[g]), g))
    nodes, sectors = [], []
    for g in order:
        items = sorted(groups[g], key=lambda r: (-int(r.get("count") or 0), str(r.get("title"))))
        sectors.append((g, len(items)))
        for r in items:
            ang = 2 * math.pi * len(nodes) / len(refs) - math.pi / 2
            nodes.append((r, cx + 275 * math.cos(ang), cy + 275 * math.sin(ang)))
    return cx, cy, nodes, sectors, 275


def graph_svg(p, cp, shared_by_title, names):
    refs = cp.get("refs") or []
    if not refs:
        return '<p class="empty">추출된 참고문헌이 없다. 원고에 인용이 없다는 뜻은 아니다.</p>'
    cx, cy, nodes, sectors, rmax = layout(refs)
    o = ['<svg class="graph" viewBox="0 0 1000 800" role="group" aria-label="%s 인용 그래프">' % E(p["title"])]
    top = {id(r): k for k, (r, _, _) in enumerate(sorted(nodes, key=lambda t: -int(t[0].get("count") or 0))[:4])}
    for r, x, y in nodes:
        others = [i for i in shared_by_title.get(re.sub(r"\W+", "", str(r.get("title")).lower()), []) if i != p["id"]] if r.get("cited") else []
        rad = min(5, 275 * math.sin(math.pi / max(2, len(refs))) * .65)
        cls = "nd%s%s" % (" sh" if others else "", "" if r.get("cited", True) else " un")
        tip = "%s\n%s, %s, %s\n인용 %s회%s" % (r.get("title"), r.get("authors") or "저자 미상", r.get("year") or "연도 미상",
                                           r.get("venue") or "게재처 미상", r.get("count") or 0,
                                           ("\n함께 인용: " + ", ".join(names.get(i, i) for i in others)) if others else "")
        o.append('<line class="spoke%s" x1="%d" y1="%d" x2="%.1f" y2="%.1f"/>' % (" sh" if others else "", cx, cy, x, y))
        o.append('<g class="%s" tabindex="0" data-tip="%s" data-go="%s" transform="translate(%.1f %.1f)"><title>%s</title><circle r="%.1f"/></g>' % (
            cls, E(tip), E(others[0] if others else ""), x, y, E(tip), rad))
        if id(r) in top:
            k = top[id(r)]
            lx, ly = (40 if k % 2 == 0 else 960), (65 if k < 2 else 745)
            o.append('<text class="ref-label" x="%d" y="%d" text-anchor="%s" data-tip="%s">%s</text>' % (
                lx, ly, "start" if k % 2 == 0 else "end", E(tip), E(clip(r.get("title"), 24))))
    o.append('<circle class="ctr" cx="%d" cy="%d" r="34"/><text class="ctrt" x="%d" y="%d" text-anchor="middle">%s</text></svg>' % (
        cx, cy, cx, cy + 4, "원고"))
    labels = {"microstructure": "시장미시구조", "asset pricing": "자산가격", "ML/LLM": "기계학습/언어모델", "econometrics": "계량경제", "DeFi/crypto": "가상자산", "portfolio/risk": "포트폴리오/위험", "macro": "거시경제", "other": "기타"}
    o.append('<p class="meta">%s</p><p class="meta">채운 점: 공유 문헌 / 점선: 본문 미인용. 점에 커서를 놓으면 제목이 표시된다.</p>' % E(", ".join('%s %d건' % (labels.get(g, g), n) for g, n in sectors)))
    return "".join(o)


def reflist(cp, names, shared_by_title):
    refs = sorted(cp.get("refs") or [], key=lambda r: -int(r.get("count") or 0))
    if not refs:
        return ""
    rows = []
    for r in refs:
        oth = [i for i in shared_by_title.get(re.sub(r"\W+", "", str(r.get("title")).lower()), []) if i != cp.get("id")] if r.get("cited") else []
        rows.append('<tr data-c="%d" data-y="%s"><td class="num">%d</td><td class="num">%s</td><td>%s<span class="au">%s</span>%s</td></tr>' % (
            int(r.get("count") or 0), E(r.get("year") or 0), int(r.get("count") or 0), E(r.get("year") or ""),
            E(r.get("title")), E(clip(r.get("authors"), 60)),
            ('<span class="also">공유: %s</span>' % E(", ".join(names.get(i, i) for i in oth))) if oth else ""))
    return ('<table class="refs"><thead><tr><th><button data-sort="c">인용 횟수</button></th><th><button data-sort="y">연도</button></th>'
            '<th>참고문헌 %d건 (원고 인용 %s건)</th></tr></thead><tbody>%s</tbody></table>') % (
        len(refs), E(cp.get("n_cited", "")), "".join(rows))


def papers_section(papers, cit):
    cps = {c["id"]: c for c in (cit or {}).get("papers", [])}
    names = {p["id"]: p["title"] for p in papers}
    sbt = {}
    for s in (cit or {}).get("shared", []):
        sbt[re.sub(r"\W+", "", str(s.get("title")).lower())] = s.get("papers", [])
    ordered = sorted(papers, key=lambda p: (ORDER.index(p["stage"]) if p["stage"] in ORDER else 99))
    lis, last = [], None
    for p in ordered:
        if p["stage"] != last:
            last = p["stage"]
            lis.append('<li class="grp">%s <span>%d편</span></li>' % (E(STAGE_KO.get(last, last)), sum(1 for q in papers if q["stage"] == last)))
        lis.append(row_html(p))
    panels = []
    for p in ordered:
        cp = dict(cps.get(p["id"]) or {})
        cp["id"] = p["id"]
        body = graph_svg(p, cp, sbt, names) if p["id"] in cps else '<p class="empty">이 원고의 인용 데이터가 아직 등록되지 않았다.</p>'
        res = clip(p.get("result"), 170)
        if p.get("pivot"):
            res = "현재 상태는 위의 전환 이력을 따른다. 이전 결과 메모: " + res
        panels.append('<section class="gpanel" id="gp-%s" data-paper="%s" hidden><h3>%s</h3><p class="meta">%s, %s%s</p>%s%s%s</section>' % (
            sid(p["id"]), E(p["id"]), E(p["title"]), E(p.get("venue") or "미정"), E(STAGE_KO.get(p["stage"], p["stage"])),
            (", 참고문헌 %s건" % E(cp.get("n_bib"))) if cp.get("n_bib") is not None else "",
            ('<p class="res">%s</p>' % E(res)) if res else "", body, reflist(cp, names, sbt)))
    return '<div class="split"><ol class="plist">%s</ol><div class="gwrap">%s</div></div>' % ("".join(lis), "".join(panels))


# ---------- section 3: cross-paper ----------
def cross_section(cit, papers):
    sh = [s for s in (cit or {}).get("shared", []) if len(s.get("papers", [])) > 1]
    if not sh:
        return '<p class="empty">공유 참고문헌 데이터가 없어 논문 간 지도를 그리지 못했다. portfolio/citations.json 의 shared 항목이 채워지면 나타난다.</p>'
    ids = sorted({i for s in sh for i in s["papers"]}, key=lambda i: next((k for k, p in enumerate(sorted(papers, key=lambda q: ORDER.index(q["stage"]) if q["stage"] in ORDER else 99)) if p["id"] == i), 99))
    pair = {}
    for s in sh:
        for a in range(len(s["papers"])):
            for b in range(a + 1, len(s["papers"])):
                k = tuple(sorted((s["papers"][a], s["papers"][b])))
                pair[k] = pair.get(k, 0) + 1
    W, step = 1500, 1250 / max(1, len(ids) - 1) if len(ids) > 1 else 0
    xs = {i: 50 + k * step for k, i in enumerate(ids)}
    o = ['<svg class="arcs" viewBox="0 0 %d 440" role="group" aria-label="논문 간 공유 참고문헌 호">' % W, '<line class="axis" x1="30" x2="%d" y1="250" y2="250"/>' % (W - 30)]
    for (a, b), n in sorted(pair.items(), key=lambda t: t[1]):
        x1, x2 = xs[a], xs[b]
        r = abs(x2 - x1) / 2
        o.append('<path class="arc" tabindex="0" data-tip="%s" d="M%.1f 250 A%.1f %.1f 0 0 1 %.1f 250" style="stroke-width:%.1f"/>' % (
            E("%s 와 %s\n공유 참고문헌 %d건" % (a, b, n)), min(x1, x2), r, min(r, 230), max(x1, x2), 1.2 + n * 1.6))
    for i in ids:
        o.append('<circle class="pt" cx="%.1f" cy="250" r="4"/><text class="al" transform="translate(%.1f 266) rotate(38)">%s</text>' % (xs[i], xs[i], E(clip(i, 22))))
    o.append("</svg>")
    ths = "".join('<th class="v"><span>%s</span></th>' % E(clip(i, 16)) for i in ids)
    rows = []
    for s in sorted(sh, key=lambda s: (-len(s["papers"]), -(s.get("year") or 0))):
        cells = "".join('<td>%s</td>' % ('<b class="dot"></b>' if i in s["papers"] else "") for i in ids)
        rows.append("<tr><th scope='row' data-tip='%s'>%s <span class='num yr'>%s</span></th>%s</tr>" % (E(s.get("title")), E(clip(s.get("title"), 80)), E(s.get("year") or ""), cells))
    return ('<p class="lead">두 편 이상이 함께 인용한 문헌 %d건. 호가 굵을수록 두 논문이 공유하는 참고문헌이 많다.</p>%s'
            '<table class="matrix"><thead><tr><th>공유 문헌, 연도</th>%s</tr></thead><tbody>%s</tbody></table>') % (len(sh), "".join(o), ths, "".join(rows))


# ---------- section 4: engine ----------
def engine_section():
    boxes = [("사람", "후보 선택, 설계 동결", 0), ("연구 프로그램", "논지 관리", 0), ("단계 제어", "다음 단계 선택", 0),
             ("자료 처리\n연구\n검토", "역할별 실행", 1), ("실험 실행", "고정 평가기", 0), ("산출물", "장부 대조", 0)]
    o = ['<svg class="flow" viewBox="0 0 1500 190" role="img" aria-label="하네스 구조: 사람, 연구 프로그램, 지휘자, 세 역할, rloop, 산출물">']
    w, g = 205, 44
    for k, (t, s, ac) in enumerate(boxes):
        x = 10 + k * (w + g)
        if k:
            o.append('<line class="ln" x1="%d" y1="95" x2="%d" y2="95"/><path class="ah" d="M%d 89 L%d 95 L%d 101"/>' % (x - g, x - 2, x - 9, x - 2, x - 9))
        o.append('<rect class="bx%s" x="%d" y="30" width="%d" height="130"/>' % (" ac" if ac else "", x, w))
        ls = t.split("\n")
        for j, l in enumerate(ls):
            o.append('<text class="bt" x="%d" y="%d">%s</text>' % (x + 14, 58 + j * 22, E(l)))
        for j, l in enumerate(s.split("\n")):
            o.append('<text class="bs" x="%d" y="%d">%s</text>' % (x + 14, 138 + j * 18 - (len(s.split("\n")) - 1) * 18, E(l)))
    o.append("</svg>")
    return "".join(o)


def example_section(design):
    res = text(os.path.join(ROOT, "runs/g03/RESULT.md"))
    disc = text(os.path.join(ROOT, "runs/g03/DISCRIMINATION.md"))
    m = re.search(r"G02[^\n]*?([\d.]+)\s*대\s*([\d.]+)", design)
    g02 = ("확인 표본 MST 복원 %s 대 %s" % (m.group(1), m.group(2))) if m else "합성 환경에서 이득이 관측됨"
    v = re.search(r"분산비는\s*([\d.]+)", res)
    g03 = ("공매도 금지 최소분산 분산비 %.3f배" % float(v.group(1))) if v else "결과 수치 없음"
    rows = re.findall(r"^\|\s*(H\d)\s*\|\s*(\w+)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*(\w+)\s*\|", disc, re.M)
    hs = {}
    for h, st, pred, obs, t in rows:
        hs.setdefault(h, [st, []])[1].append((pred, obs, t))
    survivors = [h for h, (st, _) in hs.items() if st == "survives"]
    cols = [("합성 G02", g02 if m else "결과 수치 없음", "설계 기록에 수록된 합성 실험 결과."),
            ("실제 g03", g03 if v else "결과 수치 없음", "개발 표본 결과. 확인 표본 성능을 뜻하지 않는다."),
            ("판별 실험", (", ".join(survivors) + " 생존") if survivors else ("생존 가설 없음" if hs else "판별 결과 파일 없음"), "개발 표본에서 검정한 예측에 한정한 잠정 판정.")]
    o = ['<div class="ex">']
    for t, big, sub in cols:
        o.append('<div><h4>%s</h4><p class="big">%s</p><p>%s</p></div>' % (E(t), E(big), E(sub)))
    o.append("</div>")
    if hs:
        o.append('<table class="hyp"><thead><tr><th>가설</th><th>판정</th><th>사전 예측</th><th>관측</th><th>검정</th></tr></thead><tbody>')
        for h, (st, items) in hs.items():
            for k, (pred, obs, t) in enumerate(items):
                try:
                    obs = "%.3f" % float(obs)
                except ValueError:
                    pass
                o.append('<tr class="%s"><th>%s</th><td>%s</td><td><code>%s</code></td><td class="num">%s</td><td>%s</td></tr>' % (
                    "surv" if st == "survives" else "", E(h) if k == 0 else "", E({"survives": "생존", "refuted": "기각"}.get(st, st)) if k == 0 else "",
                    E(pred), E(obs), E({"pass": "통과", "fail": "실패"}.get(t, t))))
        o.append("</tbody></table>")
    prog = []
    for f in glob.glob(os.path.join(ROOT, "programs/*/program.json")):
        prog.append(load(f, {}))
    for pg in prog:
        o.append('<h4 class="sub">논지 보드, %s</h4><p class="lead">%s</p><ul class="board">' % (E(pg.get("name")), E(pg.get("question"))))
        for t in pg.get("theses", []):
            o.append('<li><span class="tid">%s</span><span class="tst %s">%s</span><span>%s</span></li>' % (
                E(t.get("id")), E(t.get("status")), E({"doubtful": "의심", "open": "미결", "supported": "지지"}.get(t.get("status"), t.get("status"))), E(clip(t.get("statement"), 110))))
        o.append("</ul>")
    return "".join(o)


# ---------- section 5: principles ----------
def principles_section(design):
    sec = design.split("## 1.")[-1].split("## 2.")[0]
    rows = []
    for ln in sec.splitlines():
        c = [x.strip().replace("`", "") for x in ln.strip().strip("|").split("|")]
        if ln.startswith("|") and len(c) >= 3 and not set(c[0]) <= set("-: ") and c[0] != "원칙":
            rows.append(c[:3])
    if not rows:
        return '<p class="empty">DESIGN.md 에서 원칙 표를 읽지 못했다.</p>'
    return '<table class="princ"><thead><tr><th>원칙</th><th>흔적</th><th>코드로 옮긴 곳</th></tr></thead><tbody>%s</tbody></table>' % "".join(
        "<tr><th scope='row'>%s</th><td>%s</td><td>%s</td></tr>" % (E(a), E(b), E(c)) for a, b, c in rows)


# ---------- page ----------
CSS = """
:root{color-scheme:light;--paper:#fff;--ink:#202020;--mute:#555;--rule:#d4d4d4;--acc:#006c60;--wash:#f3f5f3;--bad:#202020}
*{box-sizing:border-box}html{scroll-behavior:smooth;scroll-padding-top:20px}
body{margin:0;background:var(--paper);color:var(--ink);font:17px/1.55 -apple-system,"Apple SD Gothic Neo","Malgun Gothic","Noto Sans KR",sans-serif;font-variant-numeric:tabular-nums}
.serif,h1,h2,h3{font-family:"Iowan Old Style","Palatino Linotype","AppleMyungjo","Noto Serif KR","Batang",Georgia,serif}
a,button{color:inherit}button{font:inherit;background:none;border:0;padding:0;cursor:pointer;text-align:left}
:focus-visible{outline:2px solid var(--acc);outline-offset:2px}
.page{display:grid;grid-template-columns:190px minmax(0,1fr);max-width:1800px;margin:0 auto}
nav.idx{position:sticky;top:0;align-self:start;height:100vh;padding:44px 0 0 32px;font-size:15px}
nav.idx b{display:block;font-family:Georgia,serif;font-size:19px;margin-bottom:30px}
nav.idx a{display:block;padding:7px 0 7px 14px;border-left:1px solid var(--rule);color:var(--mute);text-decoration:none}
nav.idx a.on{border-left:3px solid var(--acc);padding-left:12px;color:var(--ink);font-weight:600}
main{padding:44px 48px 90px 40px;min-width:0}
.mast{border-bottom:2px solid var(--ink);padding-bottom:28px;margin-bottom:10px}
.mast .top{display:flex;justify-content:space-between;align-items:baseline;color:var(--mute);font-size:15px;border-bottom:1px solid var(--rule);padding-bottom:10px}
h1{font-size:64px;line-height:1.1;font-weight:400;letter-spacing:-.025em;margin:24px 0 14px}
.thesis{font-size:22px;line-height:1.4;max-width:40em;margin:0 0 24px}
.figs{display:grid;grid-template-columns:repeat(5,1fr);border-top:1px solid var(--rule)}
.figs div{padding:16px 18px 0 0;border-right:1px solid var(--rule);margin-right:18px}.figs div:last-child{border:0}
.figs b{display:block;font:400 44px/1.2 "Iowan Old Style",Georgia,serif;white-space:nowrap}.figs b.ac{color:var(--acc)}
.figs span{font-size:15px;color:var(--mute)}
section.s{padding-top:28px}
h2{font-size:44px;font-weight:400;margin:0 0 6px;letter-spacing:-.01em;border-top:1px solid var(--ink);padding-top:14px}
.lead{color:var(--mute);margin:4px 0 22px;max-width:46em}
.split{display:grid;grid-template-columns:minmax(0,5fr) minmax(0,6fr);gap:40px;align-items:start;margin-top:22px}
.plist{list-style:none;margin:0;padding:0}.plist .grp{font-variant:small-caps;letter-spacing:.06em;color:var(--acc);font-weight:700;padding:20px 0 6px;border-bottom:1px solid var(--ink);font-size:16px}
.grp span{color:var(--mute);font-weight:400;margin-left:6px}
.prow{display:grid;grid-template-columns:1fr auto;gap:2px 14px;width:100%;padding:10px 8px 10px 10px;border-bottom:1px solid var(--rule);border-left:3px solid transparent}
.prow:hover{background:var(--wash)}.prow[aria-current=true]{background:var(--wash);border-left-color:var(--acc)}
.pt{font-size:16.5px;line-height:1.35;font-weight:600;grid-column:1/2}.pv{color:var(--mute);font-size:14px;grid-column:1/2}
.ps{grid-column:2;grid-row:1;font-variant:small-caps;font-weight:700;font-size:15px;text-align:right}.ps.st4{color:var(--acc)}.ps.st0,.ps.st1{color:var(--mute)}
.track{grid-column:2;grid-row:2;display:flex;gap:3px;justify-content:flex-end;align-items:center}.track i{width:22px;height:4px;background:var(--rule)}.track i.on{background:var(--ink)}.track i.cur{background:var(--acc);height:9px}
.ph{grid-column:1/3;font-size:15px;color:var(--mute);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.pivot{margin:0 0 6px 13px;padding:8px 10px;border-left:2px solid var(--acc);font-size:14px;display:flex;flex-wrap:wrap;gap:4px 18px;background:var(--wash)}
.pivot .lab{color:var(--acc);font-weight:700;font-variant:small-caps}.pivot .bad b{text-decoration:line-through;text-decoration-color:var(--bad)}.pivot span:not(.lab):not(:last-child)::after{content:"  >";color:var(--mute);margin-left:14px}
.gwrap{position:sticky;top:16px;border-top:3px solid var(--ink);padding-top:8px;max-height:calc(100vh - 32px);overflow:auto}
.gpanel h3{font-size:25px;line-height:1.3;font-weight:400;margin:8px 0 4px}.meta{margin:0 0 4px;color:var(--mute);font-size:15px}.res{font-size:15px;margin:6px 0}
.graph,.arcs,.flow{width:100%;height:auto;display:block}
.graph .sep{stroke:var(--rule);stroke-width:1}.graph .spoke{stroke:var(--rule);stroke-width:.6;opacity:.7}.graph .spoke.sh{stroke:var(--acc);opacity:.55;stroke-width:1}
.graph .sec{font-size:15px;fill:var(--ink);font-weight:600}.graph .sec .n{fill:var(--mute);font-weight:400}
.nd circle{fill:var(--paper);stroke:var(--ink);stroke-width:1.4}.nd.sh circle{fill:var(--acc);stroke:var(--acc)}.nd.un circle{stroke-dasharray:2 2;stroke:var(--mute)}
.nd{cursor:pointer}.nd:hover circle,.nd:focus circle{stroke-width:3;stroke:var(--acc)}
.nd text{font-size:13px;fill:var(--ink);paint-order:stroke;stroke:var(--paper);stroke-width:4px}
.ref-label{font-size:22px;fill:var(--ink)}
.ctr{fill:var(--ink)}.ctrt{fill:var(--paper);font-size:11px;font-weight:700}
.refs{width:100%;border-collapse:collapse;font-size:14px;margin-top:10px}.refs th{font-size:13px;color:var(--mute);font-weight:600;border-bottom:1px solid var(--ink);padding:5px 6px}
.refs td{padding:6px;border-bottom:1px solid var(--rule);vertical-align:top}.refs .num{text-align:right;width:70px}.refs th button{font-weight:700;color:var(--acc)}
.au{display:block;color:var(--mute);font-size:12.5px}.also{display:block;color:var(--acc);font-size:12.5px;font-weight:600}
.empty{padding:22px;border:1px dashed var(--mute);color:var(--mute);margin:14px 0}
#tip{position:fixed;z-index:9;max-width:420px;background:var(--ink);color:var(--paper);padding:10px 12px;font-size:14px;line-height:1.4;white-space:pre-line;pointer-events:none;display:none}
.arcs .arc{fill:none;stroke:var(--acc);opacity:.45;cursor:pointer}.arcs .arc:hover,.arcs .arc:focus{opacity:1}.arcs .axis{stroke:var(--ink)}.arcs .pt{fill:var(--ink)}.arcs .al{font-size:14px;fill:var(--ink)}
.matrix,.hyp,.princ{width:100%;border-collapse:collapse;font-size:15px;margin-top:16px}
.matrix th,.matrix td{border-bottom:1px solid var(--rule);padding:5px 8px;text-align:center;font-weight:400}.matrix th:first-child,.matrix th[scope]{text-align:left}
.matrix th.v{height:120px;vertical-align:bottom;font-size:13px;color:var(--mute)}.matrix th.v span{display:inline-block;writing-mode:vertical-rl;transform:rotate(180deg);white-space:nowrap}
.yr{color:var(--mute);margin-left:6px}.dot{display:inline-block;width:11px;height:11px;background:var(--acc);border-radius:50%}
.flow .bx{fill:none;stroke:var(--ink);stroke-width:1.2}.flow .bx.ac{stroke:var(--acc);stroke-width:2.5}.flow .ln,.flow .ah{stroke:var(--ink);fill:none;stroke-width:1.2}
.flow .bt{font:600 17px/1 -apple-system,"Apple SD Gothic Neo",sans-serif;fill:var(--ink)}.flow .bs{font-size:14px;fill:var(--mute)}
.ex{display:grid;grid-template-columns:repeat(3,1fr);margin:30px 0 8px;border-top:1px solid var(--rule)}.ex div{padding:14px 26px 0 0}
.ex h4,h4.sub{margin:0;font-variant:small-caps;letter-spacing:.05em;color:var(--acc);font-size:16px}.ex .big{font:400 30px/1.25 "Iowan Old Style",Georgia,"AppleMyungjo",serif;margin:8px 0}.ex p{margin:0 0 8px}.ex div p:last-child{color:var(--mute);font-size:15px}
h4.sub{margin-top:34px}.hyp th,.hyp td,.princ th,.princ td{border-bottom:1px solid var(--rule);padding:7px 10px;text-align:left;vertical-align:top}.hyp thead th,.princ thead th{border-bottom:1px solid var(--ink);font-size:14px;color:var(--mute)}
.hyp tr.surv{background:var(--wash)}.hyp tr.surv th,.hyp tr.surv td:nth-child(2){color:var(--acc);font-weight:700}code{font:13.5px ui-monospace,Menlo,monospace}
.board{list-style:none;padding:0;margin:0}.board li{display:grid;grid-template-columns:44px 64px 1fr;gap:10px;padding:8px 0;border-bottom:1px solid var(--rule);font-size:15px}.tid{font-weight:700}.tst{font-weight:700;font-variant:small-caps}.tst.doubtful{color:var(--bad)}.tst.supported{color:var(--acc)}
.princ{font-size:14.5px;line-height:1.45}.princ th[scope]{width:20%;font-size:15.5px;font-weight:600}.princ td:nth-child(2){width:48%}.princ td:nth-child(3){color:var(--mute)}
footer{margin-top:60px;padding-top:12px;border-top:1px solid var(--ink);color:var(--mute);font-size:14px}
@media (max-width:1100px){.page{display:block}nav.idx{display:none}main{padding:24px 16px}h1{font-size:64px}.split,.ex{grid-template-columns:1fr}.gwrap{position:static;max-height:none}.figs{grid-template-columns:repeat(2,1fr)}}
@media print{nav.idx{display:none}}
"""

JS = """
(function(){var $=function(s,r){return(r||document).querySelector(s)},$$=function(s,r){return[].slice.call((r||document).querySelectorAll(s))};
var rows=$$('.prow'),panels=$$('.gpanel');
function pick(id,scroll){rows.forEach(function(r){r.setAttribute('aria-current',r.dataset.id===id)});
panels.forEach(function(p){p.hidden=p.dataset.paper!==id});try{history.replaceState(null,'','#'+encodeURIComponent(id))}catch(e){}}
rows.forEach(function(r){r.addEventListener('click',function(){pick(r.dataset.id)})});
var h='';try{h=decodeURIComponent(location.hash.slice(1))}catch(e){}var first=rows.filter(function(r){return r.dataset.id===h})[0]||rows.filter(function(r){return r.dataset.hasrefs})[0]||rows[0];
if(first)pick(first.dataset.id);
var tip=$('#tip');function show(e,t){tip.textContent=t;tip.style.display='block';var x=e.clientX||0,y=e.clientY||0;if(!e.clientX){var b=e.target.getBoundingClientRect();x=b.right;y=b.top}
tip.style.left=Math.min(x+14,innerWidth-tip.offsetWidth-10)+'px';tip.style.top=Math.min(y+14,innerHeight-tip.offsetHeight-10)+'px'}
document.addEventListener('mouseover',function(e){var t=e.target.closest('[data-tip]');if(t)show(e,t.dataset.tip)});
document.addEventListener('mousemove',function(e){var t=e.target.closest('[data-tip]');if(t)show(e,t.dataset.tip)});
document.addEventListener('mouseout',function(e){if(e.target.closest('[data-tip]'))tip.style.display='none'});
document.addEventListener('focusin',function(e){var t=e.target.closest('[data-tip]');if(t)show({target:t},t.dataset.tip)});
document.addEventListener('focusout',function(){tip.style.display='none'});
document.addEventListener('click',function(e){var n=e.target.closest('.nd');if(n&&n.dataset.go)pick(n.dataset.go)});
document.addEventListener('keydown',function(e){var n=e.target.closest&&e.target.closest('.nd');if(n&&(e.key==='Enter'||e.key===' ')&&n.dataset.go){e.preventDefault();pick(n.dataset.go)}});
$$('[data-sort]').forEach(function(b){var dir=-1;b.addEventListener('click',function(){var k=b.dataset.sort,tb=b.closest('table').tBodies[0];dir=-dir;
$$('tr',tb).sort(function(a,c){return dir*((+a.dataset[k]||0)-(+c.dataset[k]||0))}).forEach(function(r){tb.appendChild(r)})})});
var links=$$('nav.idx a'),secs=links.map(function(a){return $(a.getAttribute('href'))});
function spy(){var y=innerHeight*.3,cur=0;secs.forEach(function(s,i){if(s&&s.getBoundingClientRect().top<y)cur=i});links.forEach(function(a,i){a.classList.toggle('on',i===cur)})}
addEventListener('scroll',spy,{passive:true});spy();})();
"""


def build(out=None, cit_path=None):
    out = out or OUT
    reg = load(os.path.join(ROOT, "papers/registry.json"), {"papers": []})
    papers = reg.get("papers", [])
    cit = load(cit_path or os.path.join(ROOT, "portfolio/citations.json"))
    port = load(os.path.join(ROOT, "portfolio/portfolio.json"), {})
    design = text(os.path.join(ROOT, "DESIGN.md"))
    acc = sum(1 for p in papers if p["stage"] == "accepted")
    wip = sum(1 for p in papers if p["stage"] in ("idea", "draft", "internal_review"))
    runs = len(glob.glob(os.path.join(ROOT, "runs/*/run.json")))
    neg = (port.get("summary") or {}).get("negative_results_kept", "-")
    venues = len({re.sub(r"\s*\(.*", "", p.get("venue", "")).strip() for p in papers if p.get("venue")})
    figs = [("%d / %d편" % (acc, len(papers)), "채택 / 전체 원고", 1), ("%d편" % wip, "작성 중 원고", 0), ("%d건" % runs, "등록된 연구 실행", 0), ("%s건" % neg if neg != "-" else "미집계", "보존한 음성 결과", 0), ("%d개" % venues, "등록된 게재처 명칭", 0)]
    figs_h = "".join('<div><b class="%s">%s</b><span>%s</span></div>' % ("ac" if a else "", E(n), E(l)) for n, l, a in figs)
    sec = [("papers", "원고", papers_section(papers, cit), "원고 %d편. 원고를 선택하면 참고문헌과 본문 인용 여부를 표시한다." % len(papers)),
           ("cross", "논문 사이의 지도", cross_section(cit, papers), ""),
           ("engine", "연구 절차", engine_section() + example_section(design), "후보 선택과 설계 동결을 거쳐 실험한다. 실행 기록과 개발 표본의 판정을 함께 보관한다."),
           ("principles", "설계 원칙", principles_section(design), "설계 문서에 기록한 원칙과 근거.")]
    nav = "".join('<a href="#%s">%s</a>' % (i, E(t)) for i, t, _, _ in [("top", "개요", 0, 0)] + sec)
    body = "".join('<section class="s" id="%s"><h2>%s</h2>%s%s</section>' % (i, E(t), ('<p class="lead">%s</p>' % E(d)) if d else "", b) for i, t, b, d in sec)
    page = ('<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>Research Lab</title><style>%s</style></head><body><div class="page"><nav class="idx" aria-label="구역"><b>Research Lab</b>%s</nav>'
            '<main><header class="mast" id="top"><div class="top"><span>연구 현황판</span><span>기준일 %s</span></div><h1 class="serif">Research Lab</h1>'
            '<p class="thesis serif">원고 진행 현황과 인용 문헌, 실험 기록</p><div class="figs">%s</div></header>%s'
            '<footer>registry.json, citations.json, portfolio.json, programs, runs, DESIGN.md 에서 빌드 시점에 읽어 생성한 오프라인 단일 파일.</footer></main></div>'
            '<div id="tip" role="tooltip"></div><script>%s</script></body></html>') % (CSS, nav, E(reg.get("as_of", "")), figs_h, body, JS)
    if cit:
        have = {c["id"] for c in cit.get("papers", []) if c.get("refs")}
        for i in have:
            page = page.replace('<button class="prow" data-id="%s"' % E(i), '<button class="prow" data-hasrefs="1" data-id="%s"' % E(i))
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, "w", encoding="utf-8", errors="replace") as f:
        f.write(page)
    return out


def selftest():
    import tempfile
    from pathlib import Path
    from unittest.mock import patch
    from lab import core

    with tempfile.TemporaryDirectory() as t:
        d = Path(t)
        for sub in ("papers", "portfolio", "prompts", "runs/a", "runs/not-a-run"):
            (d / sub).mkdir(parents=True)
        (d / "prompts/site.md").write_text("{{fixture}}", encoding="utf-8", errors="replace")
        (d / "fake.py").write_text("import sys\nprint(sys.stdin.read())\n", encoding="utf-8", errors="replace")
        papers = [{"id": i, "stage": s, "title": '<unsafe "title">', "venue": "학회"}
                  for i, s in (("a/b", "accepted"), ("a_b", "draft"), ("empty", "idea"))]
        ref = {"title": '<unsafe "ref">', "count": 3, "cited": True, "group": "other", "year": 2024}
        cit = {"papers": [{"id": p["id"], "refs": [ref], "n_bib": 1, "n_cited": 1} for p in papers[:2]],
               "shared": [{"title": ref["title"], "year": 2024, "papers": [p["id"] for p in papers[:2]]}]}
        with patch.dict(os.environ, {"LAB_PROMPTS": str(d / "prompts")}):
            fixture = core.invoke("cmd:python3 fake.py", core.prompt("site", fixture=json.dumps(cit)), d, None, False)
        (d / "portfolio/citations.json").write_text(fixture, encoding="utf-8", errors="replace")
        (d / "papers/registry.json").write_text(json.dumps({"papers": papers}), encoding="utf-8", errors="replace")
        (d / "runs/a/run.json").write_text("{}", encoding="utf-8", errors="replace")
        with patch(__name__ + ".ROOT", t):
            page = Path(build(str(d / "index.html"))).read_text(encoding="utf-8", errors="replace")
            assert '1 / 3편' in page and '1건</b><span>등록된 연구 실행' in page
            assert page.count('class="gpanel"') == 3 and page.count('class="graph"') == 2
            assert 'gp-' + sid('a/b') in page and sid('a/b') != sid('a_b')
            assert '<unsafe' not in page and '&lt;unsafe' in page
            assert '이 원고의 인용 데이터가 아직 등록되지 않았다.' in page
            assert '참고문헌 1건 (원고 인용 1건)' in page
            assert '함께 인용:' in page and '공유 참고문헌 1건' in page
            (d / "portfolio/citations.json").unlink()
            assert 'class="graph"' not in Path(build(str(d / "index.html"))).read_text(encoding="utf-8", errors="replace")
        nodes = layout([dict(ref, title=str(i)) for i in range(114)])[2]
        assert min(math.hypot(x - xx, y - yy) for i, (_, x, y) in enumerate(nodes)
                   for _, xx, yy in nodes[i + 1:]) > 10
        assert '추출된 참고문헌이 없다' in graph_svg(papers[0], {"refs": []}, {}, {})
        uncited = graph_svg(papers[0], {"refs": [dict(ref, cited=False, count=0)]},
                            {re.sub(r"\W+", "", ref["title"].lower()): ["a_b"]}, {})
        assert 'class="nd un"' in uncited and '함께 인용:' not in uncited
    print("site selftest ok")


if __name__ == "__main__":
    build()
