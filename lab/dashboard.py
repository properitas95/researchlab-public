"""Read-only lab overview. Generate one offline HTML file; never evaluate a trial."""
import csv
import datetime as dt
import json
import math
import os
import re
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen

from lab import core, director
from lab.util import esc as _e, lines as _lines, load as _json, table as _table, text as _text

SECTIONS = ('개요', '성과', '논문 상태', '지식 그래프', '시스템 구조', '연구 프로그램', '실행', '실험', '판정과 검토', '비용·자원', '리서치 칸반', '설계 사상')
STAGES = list(zip('survey ideate screen novelty pick plan critic freeze explore analyze ablate final posthoc hypotheses discriminate write review paper panel inspect'.split(),
                  'field.md candidates.json screen novelty.json pick.json PROTOCOL.md critique.json loop.json results.tsv DECISIONS.md ablations.md final_evaluation.json posthoc/manifest.json hypotheses.json discrimination.json RESULT.md REVIEW.md paper/paper.pdf panel/scores.json index.html'.split()))
CSS = '''
:root{color-scheme:light dark;--bg:#f3f6fa;--card:#fff;--ink:#17283e;--muted:#4c6078;--line:#c8d4e2;--blue:#155bc0;--green:#087752;--amber:#925400;--red:#b22d43;--tint:#e8f0fe}
@media(prefers-color-scheme:dark){:root{--bg:#0d1725;--card:#172539;--ink:#edf4ff;--muted:#b0c3da;--line:#435671;--blue:#83b9ff;--green:#6ee0b7;--amber:#ffce80;--red:#ff94a5;--tint:#243b59}}
*{box-sizing:border-box}html{scroll-behavior:smooth;scroll-padding-top:90px}body{margin:0;background:var(--bg);color:var(--ink);font:17px/1.6 system-ui,-apple-system,sans-serif}main{max-width:1400px;margin:auto;padding:32px}header{padding:24px 0}h1{font-size:42px;margin:0;letter-spacing:-1.5px}h2{font-size:28px;margin:0 0 20px}h3{margin:0 0 12px;font-size:21px}p{margin:10px 0}a{color:var(--blue);text-underline-offset:4px}nav{position:sticky;top:0;background:var(--card);border-bottom:1px solid var(--line);z-index:2;display:flex;gap:22px;padding:14px 24px;overflow:auto}nav a{white-space:nowrap;text-decoration:none;font-weight:650}section{margin:28px 0 44px;scroll-margin-top:85px}.card,.stat{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:24px;margin-bottom:16px}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:16px}.stat{margin:0}.stat strong{display:block;font-size:32px;color:var(--blue)}.muted,small{color:var(--muted)}small{font-size:16px}.chip{white-space:nowrap;display:inline-block;padding:2px 10px;border-radius:7px;border:1px solid currentColor;font-size:16px}.supported,.leaning,.done,.survives{color:var(--green)}.contested,.doubtful,.parked{color:var(--amber)}.refuted{color:var(--red)}.open,.todo{color:var(--muted)}.current{color:var(--blue);background:var(--tint);font-weight:bold}.pipeline{display:flex;flex-wrap:wrap;gap:6px;margin:16px 0}.pipeline span{border-top:5px solid currentColor;padding:5px;font-size:16px}.scroll{overflow:auto}table{width:100%;border-collapse:collapse}th,td{padding:12px;text-align:left;vertical-align:top;border-bottom:1px solid var(--line);overflow-wrap:anywhere}th{color:var(--muted);font-size:16px}details{margin-top:18px}summary{cursor:pointer;font-weight:650}li{margin:8px 0}svg{display:block;width:100%;height:auto}svg text{fill:var(--ink);font:17px system-ui,sans-serif}svg .box{fill:var(--card);stroke:var(--line);stroke-width:2}svg .accent{fill:var(--tint);stroke:var(--blue);stroke-width:2}svg .wire{fill:none;stroke:var(--muted);stroke-width:2}svg .dev{fill:none;stroke:var(--muted);stroke-width:2}svg .best{fill:none;stroke:var(--blue);stroke-width:3}svg circle{fill:var(--green)}svg .hold{fill:var(--amber)}.architecture{min-width:960px}.chart{min-width:620px}.eyebrow{color:var(--blue);font-weight:750;letter-spacing:2px}.history{border-left:3px solid var(--line);padding-left:24px;list-style:none}footer{padding:24px 0;color:var(--muted)}@media(max-width:700px){main{padding:16px}h1{font-size:32px}.card{padding:16px}}@media print{nav{position:static}section,.card{break-inside:avoid}}
'''


def _dict(p):
    value = _json(p)
    return value if isinstance(value, dict) else {}


def _num(value):
    try:
        n = float(value)
        return n if math.isfinite(n) else 0
    except (ValueError, TypeError): return 0


def _chip(value):
    cls = value if value in 'supported leaning contested doubtful refuted open parked survives'.split() else 'open'
    return f'<span class="chip {cls}">{_e(value)}</span>'


def _link(p, out, label):
    return f'<a href="{_e(quote(Path(os.path.relpath(p, out.parent)).as_posix(), safe="/"))}">{_e(label)}</a>' if p.exists() else ''


def _architecture(root):
    cfg = _dict(root / 'harness.json')
    stages = cfg.get('stages', {})
    roles = [('data_engineer', '데이터 엔지니어', 'snapshot'), ('researcher', '연구자', 'survey · plan · explore · write'), ('reviewer', '검토자', 'screen · critic · review · panel')]
    parts = ['<svg class="architecture" viewBox="0 0 1160 620" role="img" aria-label="연구실 시스템 구조">']
    def box(x, y, w, h, title, lines=(), accent=False):
        parts.append(f'<rect class="{"accent" if accent else "box"}" x="{x}" y="{y}" width="{w}" height="{h}" rx="14"/><text x="{x+20}" y="{y+32}" style="font-weight:750;font-size:21px">{_e(title)}</text>')
        for i, line in enumerate(lines): parts.append(f'<text x="{x+20}" y="{y+62+i*25}">{_e(line)}</text>')
    def arrow(x, y, xx, yy):
        parts.append(f'<path class="wire" d="M{x} {y} L{xx} {yy}"/><text x="{xx-7}" y="{yy+6}">›</text>')
    box(10, 10, 270, 100, '사람 관문', ['프로그램 시작 · 아침 검토'], True)
    box(330, 10, 330, 100, '연구 프로그램', ['논지 보드 · 증거 · 다음 질문'], True)
    box(710, 10, 440, 100, '지휘자', ['파일 상태 → 규칙 → 다음 단계'], True)
    arrow(280, 60, 325, 60); arrow(660, 60, 705, 60)
    for i, (key, label, fallback) in enumerate(roles):
        x = 10 + i*385
        names = [str(s) for s, role in stages.items() if role == key] if isinstance(stages, dict) else []
        names = names or fallback.split(' · ')
        lines, line = [], ''
        for name in names:
            if len(line + name) > 31: lines.append(line); line = ''
            line += (' · ' if line else '') + name
        lines.append(line)
        box(x, 160, 370, 80+25*max(3, len(lines)), label, lines)
        parts.append(f'<path class="wire" d="M930 110 V135 H{x+185} V160 M{x+185} {240+25*max(3,len(lines))} V385 H338 V400"/>')
    box(10, 400, 655, 90, 'rloop', ['고정 평가기 · 개발 표본 탐색 · 확인 표본 1회'], True)
    box(710, 400, 440, 90, '산출물', ['결과 · 검토 · 논문 · 실행 뷰어'])
    arrow(665, 445, 705, 445)
    box(10, 525, 655, 85, '메모리 3계층', ['실행 → 역할 → 공용 · 교훈과 증거 누적'])
    box(710, 525, 440, 85, '데이터 스냅샷', ['dev / holdout 분리 · 고정 입력'])
    return ''.join(parts) + '</svg>'


def _chart(rows, direction):
    points = []
    for r in rows:
        try: n, v = float(r.get('n', '')), float(r.get('score', ''))
        except (ValueError, TypeError): continue
        if math.isfinite(n) and math.isfinite(v): points.append((n, v, r.get('status', '')))
    if not points: return '<p class="muted">점수 기록 없음</p>'
    lo, hi = min(v for _, v, _ in points), max(v for _, v, _ in points)
    xmin, xmax = min(n for n, _, _ in points), max(n for n, _, _ in points)
    def xy(n, v): return (80+(n-xmin)/max(1, xmax-xmin)*840, 230-(v-lo)/(hi-lo or 1)*180)
    dev, best, dots, current = [], [], [], None
    for n, v, status in sorted(points):
        x, y = xy(n, v)
        dots.append(f'<circle class="{"hold" if status == "holdout" else "point"}" cx="{x:.1f}" cy="{y:.1f}" r="5"><title>{_e(f"#{n:g} {status}: {v:g}")}</title></circle>')
        if status == 'holdout': continue
        dev.append(f'{x:.1f},{y:.1f}')
        if current is not None: best.append(f'{x:.1f},{xy(n,current)[1]:.1f}')
        if status == 'keep': current = v if current is None else (min(current, v) if direction == 'min' else max(current, v))
        if current is not None: best.append(f'{x:.1f},{xy(n,current)[1]:.1f}')
    return f'<svg class="chart" viewBox="0 0 960 280" role="img" aria-label="개발 점수와 채택 최고점"><path class="wire" d="M80 35 V240 H930"/><text x="5" y="55">{hi:.3g}</text><text x="5" y="230">{lo:.3g}</text><text x="80" y="267">시행 #{xmin:g} → #{xmax:g}</text><polyline class="dev" points="{" ".join(dev)}"/><polyline class="best" points="{" ".join(best)}"/>{"".join(dots)}</svg><p class="muted">회색: 개발 점수 · 파랑: 채택 최고점 · 주황: 기존 확인 표본 기록 (개발 곡선에서 제외)</p>'


def _tree(rows):
    if not rows: return '<p class="muted">탐색 트리 기록 없음</p>'
    depth, layers, pos = {}, defaultdict(list), {}
    for r in rows:
        key = str(r.get('n')); level = depth.get(str(r.get('parent')), -1)+1
        depth[key] = level; layers[level].append(key)
    for level, keys in layers.items():
        for i, key in enumerate(keys): pos[key] = (960*(i+1)/(len(keys)+1), 40+level*90)
    parts = []
    for r in rows:
        x, y = pos[str(r.get('n'))]; parent = pos.get(str(r.get('parent')))
        if parent: parts.append(f'<path class="wire" d="M{parent[0]} {parent[1]} L{x} {y}"/>')
    for r in rows:
        x, y = pos[str(r.get('n'))]
        parts.append(f'<circle cx="{x}" cy="{y}" r="9"><title>{_e(r)}</title></circle><text x="{x-30}" y="{y+30}">#{_e(r.get("n"))}</text>')
    return f'<svg class="chart" viewBox="0 0 960 {100+90*max(layers)}" role="img" aria-label="탐색 노드와 부모 연결">{"".join(parts)}</svg>'


def _kanban():
    url = os.environ.get('LAB_KANBAN_URL', 'http://127.0.0.1:8790').rstrip('/')
    link = f'<p>리서치 칸반: <a href="{_e(url)}">{_e(url)}</a></p>' if re.match(r'^https?://', url) else f'<p>리서치 칸반: {_e(url)} (잘못된 URL)</p>'
    if not re.match(r'^https?://', url): return link
    def get(path):
        with urlopen(Request(url + '/api/' + path, headers={'X-Agent': 'lab-dashboard'}), timeout=2) as r: return json.load(r)
    try:
        projects = get('projects')
        project = next((p for p in (projects.get('projects', []) if isinstance(projects, dict) else projects) if p.get('key') == 'LAB'), None)
        if not project: return link + '<p class="muted">LAB 프로젝트 없음</p>'
        board = get('projects/' + quote(str(project['key']), safe='') + '/board')
        columns = board.get('columns', [])
        tasks = board.get('tasks', [])
        return link + ('<div class="grid">' + ''.join('<article class="card"><h3>' + _e(c.get('name', c.get('title', ''))) + '</h3><p>' + str(len(items)) + '개 작업</p><ul>' + ''.join('<li>'+_e(t.get('title', ''))+'</li>' for t in items) + '</ul></article>' for c in columns for items in [c.get('tasks', [t for t in tasks if t.get('column_id') == c.get('id')])]) + '</div>' if columns else '<p>LAB 보드 열 기록 없음</p>')
    except (OSError, ValueError, TypeError, KeyError): return link + '<p class="muted">칸반 서버 꺼짐</p>'


TYPES = dict(zip('paper report seminar tool competition dataset finding harness'.split(), '#2563eb #0891b2 #c2410c #7c3aed #be185d #15803d #a16207 #475569'.split()))
STATUS = dict(zip('accepted submitted in_review draft delivered done active parked negative'.split(), '#166534 #1d4ed8 #7e22ce #64748b #0e7490 #047857 #b45309 #92400e #be123c'.split()))
RELS = {'derived_from': ('#52657d', ''), 'uses_data': ('#52657d', '8 5'), 'uses_tool': ('#52657d', '2 5'), 'supports': ('#15803d', ''), 'refutes': ('#be123c', ''), 'feeds': ('#8793a3', ''), 'reviewed_by': ('#7e22ce', '')}
CSS += '''
main{max-width:1600px}button{font:inherit;color:inherit;cursor:pointer;background:transparent;border:0}button:focus-visible,a:focus-visible{outline:3px solid var(--blue)}.timeline{display:grid;grid-template-columns:repeat(5,1fr);gap:12px;margin:24px 0}.month{border-top:3px solid var(--line);padding-top:10px}.dot{display:inline-block;width:13px;height:13px;border-radius:50%;margin:5px}.portfolio th:nth-child(4),.portfolio td:nth-child(4){min-width:130px}.portfolio td:first-child{min-width:240px}.result{max-width:430px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.graph-layout{display:grid;grid-template-columns:minmax(0,1fr) 300px;gap:20px}.graph-scroll{overflow:auto}.kg{min-width:1080px;max-width:1080px}.kg text{font-size:14px;paint-order:stroke;stroke:var(--card);stroke-width:5px;stroke-linejoin:round;pointer-events:none}.kg .node{cursor:pointer}.kg .label{visibility:hidden}.kg .always,.kg .node:hover .label,.kg .node:focus .label,.kg .highlight .label{visibility:visible}.kg .dim{opacity:.12}.kg .highlight circle{stroke-width:6}.filters{display:flex;gap:18px;flex-wrap:wrap;margin-bottom:16px}.filters label{cursor:pointer}.legend{display:flex;flex-wrap:wrap;gap:16px;font-size:14px}.legend span{white-space:nowrap}.legend i{display:inline-block;width:24px;border-top:3px solid;vertical-align:middle;margin-right:6px}.node-detail{position:sticky;top:90px;align-self:start;overflow-wrap:anywhere}.node-detail h3{font-size:20px}.design pre{overflow:auto}.design code{font-size:15px;background:var(--tint);padding:2px 4px}.design h2{margin-top:28px}@media(max-width:900px){.graph-layout{grid-template-columns:1fr}.node-detail{position:static}}
'''
SCRIPT = '''<script>
document.querySelectorAll('.portfolio th button').forEach((b,i)=>b.onclick=()=>{const t=b.closest('table'), dir=b.dataset.dir==='asc'?-1:1;b.dataset.dir=dir===1?'asc':'desc';t.querySelectorAll('tbody').forEach(body=>[...body.rows].sort((a,c)=>dir*a.cells[i].textContent.localeCompare(c.cells[i].textContent,'ko',{numeric:true})).forEach(r=>body.append(r)));});
const graph=document.querySelector('.kg');
if(graph){const nodes=[...graph.querySelectorAll('.node')], edges=[...graph.querySelectorAll('.edge')], panel=document.querySelector('.node-detail');
function highlight(id){const near=new Set([id]);edges.forEach(e=>{if(e.dataset.from===id)near.add(e.dataset.to);if(e.dataset.to===id)near.add(e.dataset.from)});nodes.forEach(n=>{n.classList.toggle('dim',!!id&&!near.has(n.dataset.id));n.classList.toggle('highlight',!!id&&near.has(n.dataset.id))});edges.forEach(e=>e.classList.toggle('dim',!!id&&e.dataset.from!==id&&e.dataset.to!==id));}
nodes.forEach(n=>{n.onmouseenter=()=>highlight(n.dataset.id);n.onmouseleave=()=>highlight(null);n.onfocus=n.onmouseenter;n.onblur=n.onmouseleave;n.onclick=()=>{panel.replaceChildren(document.getElementById(n.dataset.detail).content.cloneNode(true))};n.onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();n.onclick()}}});
document.querySelectorAll('.filters input').forEach(c=>c.onchange=()=>{const types=new Set([...document.querySelectorAll('.filters input:checked')].map(x=>x.value)),visible=new Set();nodes.forEach(n=>{const show=types.has(n.dataset.type);n.style.display=show?'':'none';if(show)visible.add(n.dataset.id)});edges.forEach(e=>e.style.display=visible.has(e.dataset.from)&&visible.has(e.dataset.to)?'':'none');highlight(null)});
}
</script>'''


def _portfolio_links(node, root):
    evidence = node.get('evidence', [])
    paths = [('열기', node.get('path'))] + [(f'근거 {i+1}', p) for i, p in enumerate(evidence if isinstance(evidence, list) else [])]
    return ' · '.join(f'<a href="{_e((root / p).absolute().as_uri())}">{label}</a>' for label, p in paths if isinstance(p, str) and p)


def _status(node):
    status = str(node.get('status', 'draft'))
    return f'<span class="chip" style="color:{STATUS.get(status, "#64748b")}">{_e(status)}</span>'


def _layout(nodes, edges):
    """Deterministic cluster layout: one cell per type (4 per row), busiest nodes first, two columns per cell
    labels to the right (the force layout piled nodes on the walls)."""
    order = ['paper', 'report', 'seminar', 'competition', 'tool', 'harness', 'dataset', 'finding']
    kinds = sorted({n['type'] for n in nodes}, key=lambda k: (order.index(k) if k in order else 99, k))
    degree = {n['id']: 0 for n in nodes}
    for e in edges:
        for k in (e['from'], e['to']):
            if k in degree:
                degree[k] += 1
    groups = {k: sorted((n for n in nodes if n['type'] == k), key=lambda n: (-degree[n['id']], n['id'])) for k in kinds}
    cell_w, step_y, top = 270, 34, 40
    rows = [kinds[i:i + 4] for i in range(0, len(kinds), 4)]
    pos, y0 = {}, top
    for row in rows:
        tallest = max(len(groups[k]) for k in row)
        for c, k in enumerate(row):
            for i, n in enumerate(groups[k]):
                pos[n['id']] = [c * cell_w + 22, y0 + i * step_y]
        y0 += tallest * step_y + 60
    return pos, max(800, y0)


def _graph(nodes, edges, root):
    if not nodes: return '<p class="muted">포트폴리오 기록 없음</p>'
    pos, height = _layout(nodes, edges); degree = Counter(k for e in edges for k in (e['from'],e['to']))
    parts = [f'<svg class="kg" viewBox="0 0 1080 {height}" role="group" aria-label="포트폴리오 지식 그래프">']; templates = []; boxes = []
    for e in edges:
        u,v = e['from'],e['to']; color,dash = RELS.get(e.get('rel'), RELS['feeds'])
        parts.append(f'<path class="edge" data-from="{_e(u)}" data-to="{_e(v)}" d="M{pos[u][0]:.1f} {pos[u][1]:.1f} L{pos[v][0]:.1f} {pos[v][1]:.1f}" fill="none" stroke="{color}" stroke-width="2" stroke-dasharray="{dash}"><title>{_e(u)} → {_e(v)}: {_e(e.get("rel"))}</title></path>')
    for i,n in enumerate(sorted(nodes, key=lambda n: -degree[n['id']])):
        key=n['id']; x,y=pos[key]; title=str(n.get('title',key)); label=title[:14]+('…' if len(title)>14 else '')
        lx, ly = x+15, y+5  # one column per type cell: the label sits to the right and never collides
        parts.append(f'<g class="node" tabindex="0" role="button" aria-label="{_e(title)}" data-id="{_e(key)}" data-type="{_e(n["type"])}" data-detail="detail-{i}"><title>{_e(title)}</title><circle cx="{x:.1f}" cy="{y:.1f}" r="9" style="fill:{TYPES.get(n["type"], "#475569")};stroke:{STATUS.get(str(n.get("status")), "#64748b")};stroke-width:4"/><text class="label always" x="{lx:.1f}" y="{ly:.1f}">{_e(label)}</text></g>')
        templates.append(f'<template id="detail-{i}"><h3>{_e(title)}</h3><p>{_status(n)}</p><p>{_e(n.get("venue", ""))} · {_e(n.get("date", ""))}</p><p>{_e(n.get("result", ""))}</p><p>{_portfolio_links(n,root)}</p></template>')
    filters=''.join(f'<label><input type="checkbox" checked value="{_e(k)}"> <span style="color:{TYPES.get(k,"#475569")}">●</span> {_e(k)}</label>' for k in sorted({n['type'] for n in nodes}))
    legend=''.join(f'<span><i style="border-color:{c};border-top-style:{"dashed" if r=="uses_data" else "dotted" if r=="uses_tool" else "solid"}"></i>{r}</span>' for r,(c,_) in RELS.items())
    return f'<div class="card"><div class="filters">{filters}</div><div class="legend">{legend}</div><p class="muted">{len(nodes)}개 노드 · {len(edges)}개 연결 · 내부 색: 유형 · 테두리: 상태 · 노드 선택: 상세 정보</p><div class="graph-layout"><div class="graph-scroll">{"".join(parts)}</svg></div><aside class="node-detail card" aria-live="polite"><h3>연구의 연결</h3><p>노드를 선택하면 결과와 근거를 볼 수 있습니다.</p></aside></div>{"".join(templates)}</div>'


def _portfolio(root):
    data=_dict(root/'portfolio/portfolio.json'); raw=data.get('nodes'); nodes=[]; ids=set()
    for n in raw if isinstance(raw,list) else []:
        if isinstance(n,dict) and isinstance(n.get('id'),str) and n['id'] not in ids:
            nodes.append(n | {'type':str(n.get('type','other'))}); ids.add(n['id'])
    raw=data.get('edges'); edges=[e for e in raw if isinstance(e,dict) and isinstance(e.get('from'),str) and isinstance(e.get('to'),str) and e['from'] in ids and e['to'] in ids] if isinstance(raw,list) else []
    summary=data.get('summary') if isinstance(data.get('summary'),dict) else {}; counts=Counter(n['type'] for n in nodes)
    stats=[(label,summary.get(key, counts[typ]+(counts['harness'] if typ=='tool' else 0))) for key,typ,label in [('papers','paper','논문'),('reports','report','보고서'),('seminars','seminar','세미나'),('tools','tool','도구'),('competitions','competition','대회'),('datasets','dataset','데이터셋'),('findings','finding','발견')]]
    stats += [('보존한 음성 결과',summary.get('negative_results_kept',sum(n.get('status')=='negative' for n in nodes))),('외부 심사',summary.get('external_refereeing_counted_only','미기록'))]
    content='<div class="grid">'+''.join(f'<div class="stat">{label}<strong>{_e(v)}</strong></div>' for label,v in stats)+'</div>'
    content+='<p class="muted">포트폴리오 기준일: '+_e(data.get('as_of','미기록'))+' · 열 제목으로 유형 안에서 정렬 · 결과에 마우스를 올리면 전문 표시</p><div class="timeline">'
    for month in range(6,11):
        date=f'2026-{month:02}'; dots=''.join(f'<a class="dot" href="#portfolio-{i}" style="background:{TYPES.get(n["type"],"#475569")}" aria-label="{_e(n.get("title"))}" title="{_e(n.get("title"))}"></a>' for i,n in enumerate(nodes) if str(n.get('date','')).startswith(date))
        content+=f'<div class="month"><b>{date}</b><div>{dots or "—"}</div></div>'
    content+='</div><div class="card scroll"><table class="portfolio"><thead><tr>'+''.join(f'<th><button type="button">{h} ↕</button></th>' for h in ('제목','발표처','날짜','상태','한 줄 결과','파일 · 근거'))+'</tr></thead>'
    order = 'paper report seminar competition tool harness dataset finding'.split()
    for kind in sorted(counts, key=lambda k: (order.index(k) if k in order else len(order), k)):
        content+=f'<tbody><tr><th colspan="6">{_e(kind)} · {counts[kind]}</th></tr></tbody><tbody>'
        for i,n in enumerate(nodes):
            if n['type']!=kind: continue
            content+=f'<tr id="portfolio-{i}"><td>{_e(n.get("title"))}</td><td>{_e(n.get("venue"))}</td><td>{_e(n.get("date"))}</td><td>{_status(n)}</td><td><div class="result" title="{_e(n.get("result"))}">{_e(n.get("result"))}</div></td><td>{_portfolio_links(n,root)}</td></tr>'
        content+='</tbody>'
    return content+'</table>'+('' if nodes else '<p>포트폴리오 기록 없음</p>')+'</div>', _graph(nodes,edges,root)



PAPER_STAGES = 'idea draft internal_review submitted under_review revise accepted camera_ready published rejected withdrawn'.split()
CSS += '.paper-board{display:flex;gap:16px;overflow:auto}.paper-column{flex:0 0 280px;min-width:0}.paper-column .card{padding:16px;overflow-wrap:anywhere}.paper-column h3{font-size:18px}.unverified{color:var(--muted);opacity:.65}.fit{width:90px;vertical-align:middle}'


def _papers(root):
    registry = _dict(root/'papers/registry.json'); catalog = _dict(root/'papers/venues.json')
    def records(value): return [v for v in value if isinstance(v, dict)] if isinstance(value, list) else []
    papers = records(registry.get('papers')); venues = records(catalog.get('venues'))
    groups = {stage: [p for p in papers if p.get('stage') == stage] for stage in PAPER_STAGES}
    last = max(i for i, stage in enumerate(PAPER_STAGES) if groups[stage] or stage in ('draft', 'submitted', 'under_review', 'accepted', 'rejected'))
    parts = [f'<p class="muted">논문 기준일: {_e(registry.get("as_of", "미기록"))}</p><div class="paper-board">']
    for stage in PAPER_STAGES[:last+1]:
        parts.append(f'<div class="paper-column"><h3>{_chip(stage)} · {len(groups[stage])}</h3>')
        for paper in groups[stage]:
            date = max((str(h.get('date', '')) for h in records(paper.get('history'))), default='미기록')
            parts.append(f'<article class="card"><h3>{_e(paper.get("title"))}</h3><p>{_e(paper.get("venue"))}</p><p>{_e(paper.get("result"))}</p><small>최근 이력: {_e(date)}</small></article>')
        parts.append('</div>')
    parts.append('</div>' + ('' if papers else '<p class="muted">논문 기록 없음</p>'))
    deadlines = []
    for paper in papers:
        deadline = paper.get('next_deadline')
        if isinstance(deadline, dict): deadlines.append((paper.get('title', ''), deadline))
    for venue in venues:
        if venue.get('next_deadline'):
            deadlines.append((venue.get('name', venue.get('id', '')), {'venue': venue.get('name', ''), 'date': venue['next_deadline'], 'verified': venue.get('deadline_verified'), 'source': venue.get('source', '')}))
    parts.append('<h3>다음 마감</h3><ul>')
    checked, unchecked = [], []
    for title, deadline in deadlines:
        date = str(deadline.get('date', ''))
        try: valid = dt.date.fromisoformat(date).isoformat() == date
        except ValueError: valid = False
        (checked if deadline.get('verified') is True and valid else unchecked).append((date, title, deadline))
    for entries, verified in ((checked, True), (unchecked, False)):
        for date, title, deadline in sorted(entries, key=lambda entry: (entry[0], str(entry[1]))):
            parts.append(f'<li class="{"" if verified else "unverified"}">{_e(date or "날짜 없음")} · {_e(title)} · {_e(deadline.get("venue"))} · {"확인됨" if verified else "미확인"}<small> · {_e(deadline.get("source"))}</small></li>')
    parts.append('</ul>' + ('' if deadlines else '<p class="muted">마감 기록 없음</p>'))
    for paper in papers:
        pivot = paper.get('pivot'); candidates = records(pivot.get('candidates')) if isinstance(pivot, dict) else []
        if not candidates: continue
        parts.append(f'<h3>{_e(paper.get("title"))} · 전환 후보</h3><div class="scroll"><table><thead><tr><th>발표처</th><th>적합도</th><th>마감</th><th>변경 사항</th></tr></thead><tbody>')
        for candidate in candidates:
            fit = max(0, min(1, _num(candidate.get('fit')))); changes = candidate.get('changes', [])
            deadline = _e(candidate.get('deadline') or '날짜 없음')
            if candidate.get('deadline_verified') is not True: deadline = f'<span class="unverified">{deadline} · 미확인</span>'
            parts.append(f'<tr><td>{_e(candidate.get("venue"))}</td><td><meter class="fit" min="0" max="1" value="{fit}" aria-label="적합도">{fit:.0%}</meter> {fit:.0%}</td><td>{deadline}</td><td>{_e(" · ".join(map(str, changes)) if isinstance(changes, list) else changes)}</td></tr>')
        parts.append('</tbody></table></div>')
    return ''.join(parts)


def _markdown(text):
    def inline(s):
        return ''.join('<code>'+_e(t[1:-1])+'</code>' if t.startswith('`') else re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', _e(t)) for t in re.split(r'(`[^`]+`)',s))
    parts=[]; block=''; fenced=False
    for line in text.splitlines():
        if line.startswith('```'):
            if block: parts.append(f'</{block}>'); block=''
            parts.append('</code></pre>' if fenced else '<pre><code>'); fenced=not fenced; continue
        if fenced: parts.append(_e(line)+'\n'); continue
        kind='table' if line.strip().startswith('|') else 'ul' if re.match(r'^\s*[-*] ',line) else ''
        if kind!=block:
            if block: parts.append(f'</{block}>')
            if kind: parts.append(f'<{kind}>')
            block=kind
        if kind=='table':
            cells=line.strip().strip('|').split('|')
            if not all(re.fullmatch(r'\s*:?-+:?\s*',c) for c in cells): parts.append('<tr>'+''.join('<td>'+inline(c.strip())+'</td>' for c in cells)+'</tr>')
        elif kind=='ul': parts.append('<li>'+inline(re.sub(r'^\s*[-*] ', '',line))+'</li>')
        elif re.match(r'^#{1,6} ',line):
            mark,title=line.split(' ',1); level=min(6,len(mark)+1); parts.append(f'<h{level}>{inline(title)}</h{level}>')
        elif line.strip(): parts.append('<p>'+inline(line)+'</p>')
    return '<article class="card design">'+(''.join(parts) or '<p>DESIGN.md 기록 없음</p>')+(f'</{block}>' if block else '')+('</code></pre>' if fenced else '')+'</article>'


def dashboard(a):
    root = Path(core.ROOT).resolve(); out = Path(getattr(a, 'output', None) or root / 'dashboard.html').resolve()
    bodies = [[] for _ in range(8)]; calls, resources, trials, verdicts, times = [], [], {}, Counter(), []
    programs = sorted((root / 'programs').glob('*/program.json'))
    runs = sorted(p for p in (root / 'runs').glob('*') if p.is_dir())
    for p in programs:
        j = _dict(p); board = []
        for t in j.get('theses', []):
            evidence = _table(('실행', '방향', '근거'), [(e.get('run'), e.get('direction'), e.get('note')) for e in t.get('evidence', [])])
            board.append(f'<tr><td>{_e(t.get("id"))}</td><td>{_e(t.get("statement"))}</td><td>{_chip(t.get("status", "open"))}</td><td>{evidence}</td></tr>')
        history = ''.join('<li>'+_e(' · '.join(str(h.get(k, '')) for k in ('utc', 'action', 'run', 'note')))+'</li>' for h in j.get('history', []))
        bodies[2].append(f'<article class="card"><h3>{_e(j.get("name", p.parent.name))}</h3><p>{_e(j.get("question"))}</p><div class="scroll"><table><thead><tr><th>ID</th><th>논지</th><th>상태</th><th>증거</th></tr></thead><tbody>{"".join(board)}</tbody></table></div><details><summary>프로그램 이력</summary><ol class="history">{history}</ol></details>{_link(p.parent/"DIGEST.md", out, "아침 검토 DIGEST.md")}</article>')
    seen_resources = set()
    for d in runs:
        c = _dict(d / 'run.json'); exp = Path(c.get('exp_dir') or d / 'exp'); exp = (root / exp).resolve() if not exp.is_absolute() else exp.resolve()
        q = c.get('q', 'Q1'); events = _lines(d / 'events.jsonl'); cs = _lines(d / 'calls.jsonl'); rs = _lines(exp / 'resources.jsonl')
        calls.extend(cs)
        if exp not in seen_resources: resources.extend(rs); seen_resources.add(exp)
        rows = [r for r in csv.DictReader(_text(exp / 'results.tsv').splitlines(), delimiter='\t') if r.get('q', q) == q]
        trials[(exp, q)] = rows
        try: nxt = director.next_cmd(d)
        except Exception as e: nxt = '단계 확인 불가: ' + str(e)
        try: verdict = director._verdict(exp, q) or '미판정'
        except Exception: verdict = '판정 확인 불가'
        verdicts[verdict] += 1
        parked = any(e.get('stage') == 'park' or e.get('action') == 'park' for e in events)
        heading = next((s.lstrip('#').strip() for s in _text(d/'brief.md').splitlines() if s.startswith('#')), d.name)
        pipeline = ''
        for stage, artifact in STAGES:
            done = any((base / artifact).exists() for base in (d, exp))
            state = 'done' if done else 'parked' if parked else 'current' if f'./lab.py {stage} ' in nxt else 'todo'
            pipeline += f'<span class="{state}" title="{_e(artifact)} · {state}">{stage}</span>'
        bodies[3].append(f'<article class="card"><h3>{_e(d.name)} {_chip("parked") if parked else ""}</h3><p>{_e(heading)}</p><p>판정: <b>{_e(verdict)}</b></p><p>다음 단계: {_e(nxt)}</p><div class="pipeline">{pipeline}</div>{_link(d/"index.html",out,"실행 상세")} · {_link(d/"paper/paper.pdf",out,"논문 PDF")}</article>')
        if (exp / 'results.tsv').exists():
            direction = _dict(exp/'loop.json').get(q, {}).get('direction', 'max')
            bodies[4].append(f'<article class="card"><h3>{_e(d.name)} · {_e(q)}</h3><div class="scroll">{_chart(rows, direction)}{_tree(_lines(exp/"tree.jsonl"))}</div></article>')
        critique = Counter(x.get('severity', 'unknown') for x in _dict(d/'critique.json').get('findings', []))
        hypotheses = _dict(d/'discrimination.json').get('hypotheses', [])
        panel = _dict(d/'panel/scores.json'); history = _lines(d/'panel/history.jsonl')
        bodies[5].append(f'<article class="card"><h3>{_e(d.name)}</h3><p>설계 지적: {_e(dict(critique) if critique else "미기록")}</p>' + _table(('가설', '기제', '판정'), [(h.get('id'), str(h.get('mechanism', '')).split('\n')[0], h.get('status')) for h in hypotheses]) + f'<p>패널: {_e(panel.get("meta_decision", "미기록"))} · 평균 {_e(panel.get("mean_overall", "—"))}</p>' + _table(('심사자', '관점', '점수', '판정'), [(r.get('id'), r.get('persona'), r.get('overall'), r.get('decision')) for r in panel.get('reviewers', [])]) + '<details><summary>수정 이력</summary>' + _table(('시각 / 회차', '내용'), [(h.get('utc', h.get('round', '')), json.dumps(h, ensure_ascii=False)) for h in history + [e for e in events if e.get('stage') in ('revise', 'replan')]]) + '</details></article>')
        groups = defaultdict(list)
        for call in cs: groups[(str(call.get('role', '미기록')), str(call.get('provider', '미기록')))].append(call)
        costs = [(role, provider, len(items), sum(_num(x.get('chars_in')) for x in items), sum(_num(x.get('chars_out')) for x in items), round(sum(_num(x.get('seconds')) for x in items), 2)) for (role, provider), items in groups.items()]
        bodies[6].append(f'<article class="card"><h3>{_e(d.name)}</h3>' + (_table(('역할', '제공자', '호출', '입력 문자', '출력 문자', '초'), costs) if cs else '<p class="muted">모델 호출 미기록</p>') + (_table(('시행 로그', '경과 초', 'CPU 초', '최대 RSS MB'), [(r.get('log'), r.get('seconds'), r.get('cpu_s'), r.get('peak_rss_mb')) for r in rs]) if rs else '<p class="muted">시행 자원 미기록</p>') + '</article>')
    for folder in [root/'programs', root/'runs', *seen_resources]:
        for p in folder.rglob('*') if folder in (root/'programs', root/'runs') else folder.glob('*'):
            try:
                if p.is_file(): times.append(p.stat().st_mtime)
            except OSError: pass
    allrows = [r for rows in trials.values() for r in rows]
    stats = [('프로그램', len(programs)), ('실행', len(runs)), ('개발 시행', sum(r.get('status') != 'holdout' for r in allrows)), ('채택', sum(r.get('status') == 'keep' for r in allrows)), ('모델 호출 기록', len(calls)), ('입력 / 출력 문자', f'{sum(_num(c.get("chars_in")) for c in calls):g} / {sum(_num(c.get("chars_out")) for c in calls):g}'), ('CPU 초 / 최대 RSS MB', f'{sum(_num(r.get("cpu_s")) for r in resources):.1f} / {max((_num(r.get("peak_rss_mb")) for r in resources), default=0):.1f}' if resources else '미기록')]
    bodies[0] = ['<div class="grid">' + ''.join(f'<div class="stat">{_e(k)}<strong>{_e(v)}</strong></div>' for k, v in stats) + '</div>' + _table(('판정', '실행 수'), sorted(verdicts.items())) + '<p class="muted">마지막 파일 활동: ' + _e(dt.datetime.fromtimestamp(max(times)).astimezone().isoformat(timespec='seconds') if times else '없음') + ' · 기록이 있는 호출·자원만 집계</p><p>' + _link(root/'lineage.html', out, '계보·퍼트 차트') + ' · ' + _link(root/'METHODS.md', out, '방법 리뷰 장부') + '</p>']
    bodies[1] = ['<div class="card scroll">' + _architecture(root) + '</div>']
    bodies[7] = [_kanban()]
    portfolio, graph = _portfolio(root)
    bodies = [bodies[0], [portfolio], [_papers(root)], [graph], *bodies[1:], [_markdown(_text(root/'DESIGN.md'))]]
    doc = '<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>ResearchLab · 연구실 현황</title><style>' + CSS + '</style></head><body><nav aria-label="섹션 이동">' + ''.join(f'<a href="#s{i}">{name}</a>' for i, name in enumerate(SECTIONS)) + '</nav><main><header><p class="eyebrow">RESEARCH LAB / 연구실 현황</p><h1>질문에서 증거까지</h1><p class="muted">연구 프로그램 · 실행 · 검토 · 자원 현황</p></header>'
    doc += ''.join(f'<section id="s{i}"><h2>{name}</h2>{"".join(bodies[i]) or "<p class=muted>기록 없음</p>"}</section>' for i, name in enumerate(SECTIONS))
    doc += '<footer>생성 시각: ' + _e(core.now()) + ' · 읽기 전용 스냅샷 · 확인 표본은 기존 기록만 표시</footer></main>' + SCRIPT + '</body></html>'
    out.parent.mkdir(parents=True, exist_ok=True); out.write_text(doc, encoding='utf-8')
    return out


def selftest():
    import tempfile
    from types import SimpleNamespace
    from unittest.mock import patch
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp); p = root/'programs/toy'; p.mkdir(parents=True)
        (p/'program.json').write_text(json.dumps({'name':'<program>', 'theses':[{'id':'T1','statement':'<thesis>','status':'supported','evidence':[{'run':'done','direction':'supports','note':'<evidence>'}]}], 'history':[{'action':'init'}]}))
        (root/'fake.py').write_text("print('# <script>alert(1)</script>')\n")
        (root/'prompts').mkdir(); (root/'prompts/test.md').write_text('fixture')
        brief = core.invoke('cmd:python3 fake.py', (root/'prompts/test.md').read_text(), root, None, False)
        (root/'portfolio').mkdir()
        toy = {'nodes': [{'id':str(i), 'type':['paper','tool','finding','dataset'][i], 'title':'<script>alert(1)</script>', 'status':'negative', 'date':'2026-06', 'result':'<result>', 'path':'a b.md', 'evidence':['<evidence>.md']} for i in range(4)],
               'edges':[{'from':'0','to':str(i),'rel':'uses_data'} for i in range(1,4)]}
        (root/'portfolio/portfolio.json').write_text(json.dumps(toy))
        assert '논문 기록 없음' in _papers(root)
        (root/'papers').mkdir()
        registry = {'as_of':'2026-10-20', 'stages':PAPER_STAGES, 'papers':[
            {'title':'<paper>', 'stage':'draft', 'venue':'<venue>', 'result':'<finding>',
             'history':[{'date':'2026-10-19'}, {'date':'2026-10-01'}],
             'next_deadline':{'date':'2026-12-02', 'venue':'V2', 'verified':True},
             'pivot':{'candidates':[{'venue':'<pivot>', 'fit':.75, 'deadline':'2026-12-03', 'deadline_verified':False, 'changes':['<cut>']}]}},
            {'title':'unverified paper', 'stage':'rejected', 'next_deadline':{'date':'2026-01-01', 'verified':False}}]}
        (root/'papers/registry.json').write_text(json.dumps(registry))
        (root/'papers/venues.json').write_text(json.dumps({'venues':[{'name':'<catalog>', 'next_deadline':'2026-11-01', 'deadline_verified':True}]}))
        board = _papers(root)
        for value in ('&lt;paper&gt;', '&lt;venue&gt;', '&lt;finding&gt;', '&lt;pivot&gt;', '&lt;cut&gt;', '75%', '미확인', '2026-10-19'):
            assert value in board, value
        assert board.index('2026-11-01') < board.index('2026-12-02') < board.index('2026-01-01')
        assert all(f'>{stage}</span>' in board for stage in PAPER_STAGES[:-1]) and '>withdrawn</span>' not in board
        for bad in ('{broken', 'null', '{"papers":null}', '{"papers":[null,{}]}'):
            (root/'papers/registry.json').write_text(bad); _papers(root)
        (root/'papers/registry.json').write_text(json.dumps(registry))
        portfolio = _portfolio(root)[0]
        assert portfolio.index('paper ·') < portfolio.index('tool ·') < portfolio.index('dataset ·') < portfolio.index('finding ·')

        (root/'DESIGN.md').write_text('# <design>\n- **bold** and `code`\n\n| a | b |\n|---|---|\n| <script> | safe |\n```\n<x>\n```')
        for rid in ('done', 'parked'):
            d = root/'runs'/rid; exp = d/'exp'; exp.mkdir(parents=True)
            (d/'run.json').write_text(json.dumps({'exp_dir':str(exp), 'q':'Q1'})); (d/'brief.md').write_text(brief)
            (d/'events.jsonl').write_text('{"stage":"park"}\n' if rid == 'parked' else '')
            (d/'calls.jsonl').write_text('{"role":"researcher","provider":"fake","chars_in":12,"chars_out":3,"seconds":1}\n{broken')
            (d/'critique.json').write_text('{"findings":[{"severity":"major"}]}')
            (d/'discrimination.json').write_text('{"hypotheses":[{"id":"H1","mechanism":"<mechanism>","status":"survives"}]}')
            (exp/'results.tsv').write_text('n\tq\tscore\tstatus\n1\tQ1\t2\tkeep\n2\tQ1\t1\tkeep\n3\tQ1\t9\tholdout\n4\tQ1\tnan\tinvalid\n')
            (exp/'loop.json').write_text('{"Q1":{"direction":"min"}}')
            (exp/'tree.jsonl').write_text('{"n":1,"parent":null}\n{"n":2,"parent":1}\n')
            (exp/'resources.jsonl').write_text('{"log":"1.log","cpu_s":2,"peak_rss_mb":4}\n')
            if rid == 'done':
                for _, artifact in STAGES:
                    f = d/artifact
                    if not f.exists(): f.parent.mkdir(parents=True, exist_ok=True); f.write_text('{}')
                (d/'panel/scores.json').write_text('{"meta_decision":"accept"}')
                (d/'paper/paper.tex').write_text('paper')
                (exp/'DECISIONS.md').write_text('- Q1 **no_improvement**')
        with patch.object(core, 'ROOT', root), patch(__name__+'.urlopen', side_effect=OSError('offline')):
            output = dashboard(SimpleNamespace(output=None)); text = output.read_text()
            assert director.next_cmd(root/'runs/done').startswith('DONE')
            assert all(f'<h2>{s}</h2>' in text for s in SECTIONS)
            assert [text.index(f'<h2>{s}</h2>') for s in SECTIONS] == sorted(text.index(f'<h2>{s}</h2>') for s in SECTIONS)
            for s in ('&lt;script&gt;', '&lt;program&gt;', '&lt;evidence&gt;', '&lt;mechanism&gt;', 'parked', 'no_improvement', '칸반 서버 꺼짐', 'polyline', 'class="hold"'): assert s in text, s
            assert '<script>alert(1)</script>' not in text and 'href="runs/done/index.html"' in text
            graph = re.search(r'<svg class="kg".*?</svg>', text, re.S)[0]
            assert graph.count('<circle ') == 4 and graph.count('class="edge"') == 3
            assert '&lt;design&gt;' in text and '<strong>bold</strong>' in text and '<code>code</code>' in text
            assert (root/'a b.md').resolve().as_uri() in text and '&lt;result&gt;' in text
            for bad in ('{broken', 'null', '{"nodes":null,"edges":12}', '{"nodes":[null,{}],"edges":[{}]}'):
                (root/'portfolio/portfolio.json').write_text(bad)
                assert '포트폴리오 기록 없음' in _portfolio(root)[1]
            assert '<svg' in _chart([{'n':1,'score':1,'status':'keep'}], 'max')
            assert not _lines(root/'missing')
        from io import BytesIO
        responses = [BytesIO(b'[{"id":1,"key":"LAB"}]'), BytesIO(b'{"columns":[{"name":"Todo","tasks":[{"title":"<task>"}]}]}')]
        with patch.dict(os.environ, {'LAB_KANBAN_URL':'http://127.0.0.1:9898/'}), patch(__name__+'.urlopen', side_effect=responses) as fetch:
            board = _kanban()
            assert '&lt;task&gt;' in board and 'href="http://127.0.0.1:9898"' in board
            assert fetch.call_args.args[0].full_url.startswith('http://127.0.0.1:9898/api/')
            assert fetch.call_args.args[0].full_url.endswith('/projects/LAB/board')
            assert fetch.call_args.kwargs['timeout'] == 2
        with patch.dict(os.environ, {'LAB_KANBAN_URL':'javascript:alert(1)'}):
            assert 'href=' not in _kanban()
        with patch.object(core, 'ROOT', root/'empty'), patch(__name__+'.urlopen', side_effect=OSError):
            assert '기록 없음' in dashboard(SimpleNamespace(output=root/'empty.html')).read_text()
        chart = _chart([{'n':1,'score':2,'status':'keep'}, {'n':2,'score':1,'status':'keep'}, {'n':3,'score':9,'status':'holdout'}], 'min')
        assert 'class="best" points="80.0,207.5 500.0,207.5 500.0,230.0"' in chart
    print('dashboard selftest ok (12 sections, paper registry/deadlines/pivots, kanban URL, 4 nodes/3 edges, escaping, deterministic layout, malformed inputs, done/parked, charts, resources, offline)')
