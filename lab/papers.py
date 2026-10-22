"""Evidence-backed paper board and explicit venue pivots. Never submits papers."""
import datetime as dt
import json
import math
import re
import shutil
import subprocess
from pathlib import Path
from lab import core

ROOT = core.ROOT
STAGES = 'idea draft internal_review submitted under_review revise accepted camera_ready published rejected withdrawn'.split()
TRANSITIONS = dict(idea='draft withdrawn', draft='internal_review submitted withdrawn',
    internal_review='draft submitted withdrawn', submitted='under_review revise accepted rejected withdrawn',
    under_review='revise accepted rejected withdrawn', revise='draft submitted under_review accepted rejected withdrawn',
    accepted='camera_ready published withdrawn', camera_ready='published withdrawn', published='',
    rejected='withdrawn submitted', withdrawn='draft submitted')


def _load(a):
    data = json.loads((ROOT / 'papers/registry.json').read_text())
    pid = getattr(a, 'id', None)
    if pid is None:
        return data, None
    if not re.fullmatch(r'[\w-]+', pid):
        raise ValueError('invalid paper id')
    paper = next((p for p in data['papers'] if p['id'] == pid), None)
    if paper is None:
        raise ValueError(f'unknown paper: {pid}')
    return data, paper


def _save(data):
    data['as_of'] = dt.date.today().isoformat()
    path = ROOT / 'papers/registry.json'
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
    tmp.replace(path)


def _history(p, note, evidence):
    p['history'].append(dict(date=dt.date.today().isoformat(), stage=p['stage'], venue=p['venue'],
                             note=note, evidence=evidence))


def paper_status(a):
    data, _ = _load(a)
    groups = [[p['id'] for p in data['papers'] if p['stage'] == s] for s in STAGES]
    print(' | '.join(f'{s} ({len(g)})' for s, g in zip(STAGES, groups)))
    for i in range(max(map(len, groups), default=0)):
        print(' | '.join(g[i] if i < len(g) else '' for g in groups))
    deadlines = [(p['next_deadline']['date'], p) for p in data['papers'] if p.get('next_deadline')
                 and p['next_deadline'].get('verified') and p['next_deadline']['date'] >= dt.date.today().isoformat()]
    for date, p in sorted(deadlines, key=lambda row: row[0]):
        print(f"{date} {p['id']} → {p['next_deadline']['venue']}")


def paper_set(a):
    data, p = _load(a)
    venue = getattr(a, 'venue', None) or p['venue']
    if a.stage == 'submitted' and not venue.strip():
        raise ValueError('submission requires a venue')
    if a.stage not in TRANSITIONS[p['stage']].split():
        raise ValueError(f"forbidden transition: {p['stage']} → {a.stage}; use paper-pivot for rejection")
    if p['stage'] == 'rejected' and a.stage == 'submitted' and venue.casefold().strip() == p['venue'].casefold().strip():
        raise ValueError('rejected paper must be submitted elsewhere')
    if venue != p['venue'] and a.stage != 'submitted':
        raise ValueError('venue changes require pivot or a new submission')
    if not a.note.strip() or not Path(a.evidence).is_file():
        raise ValueError('a note and existing evidence file are required')
    p.update(stage=a.stage, venue=venue, next_deadline=None)
    _history(p, a.note, str(Path(a.evidence).resolve()))
    _save(data)


def _areas(p):
    text = (p['title'] + ' ' + p['result'] + ' ' + p['path']).lower()
    words = {'market microstructure': 'oracle 오라클 호가 execution 체결 microstructure',
             'asset pricing': 'portfolio 자산 거버 gerber 요인 gmv 틸트 캐리',
             'ML for finance': '학습 예측 attention',
             'LLM evaluation': 'llm jev aurora evs 판단 뉴스 filings',
             'DeFi': 'defi amm 무기한 perp dex', 'macro': 'macro 거시'}
    return set(p.get('areas', [])) | {area for area, keys in words.items() if any(k in text for k in keys.split())}


def _pages(path):
    path = Path(path)
    if path.is_dir():
        path = next((path / f for f in ('main.tex', 'paper.tex', 'main.pdf', 'paper.pdf') if (path / f).is_file()), path)
    if path.suffix == '.tex':
        aux = path.with_suffix('.aux')
        match = re.search(r'\\gdef\s*\\@abspage@last\{(\d+)\}', aux.read_text()) if aux.exists() else None
        if match:
            return int(match[1])
        path = path.with_suffix('.pdf')
    if path.suffix == '.pdf' and path.exists() and shutil.which('pdfinfo'):
        out = subprocess.run(['pdfinfo', str(path)], capture_output=True, text=True, timeout=10)
        match = re.search(r'^Pages:\s*(\d+)', out.stdout, re.M)
        if match:
            return int(match[1])
    return None


def venue_fit(a):
    data, p = _load(a)
    venues = json.loads((ROOT / 'papers/venues.json').read_text())['venues']
    pages, areas, today = _pages(p['path']), _areas(p), dt.date.today().isoformat()
    eligible = {v['name']: v for v in venues if areas.intersection(v['areas'])
                and not (v['deadline_verified'] and v['next_deadline'] and v['next_deadline'] <= today)
                and not (pages and v['page_limit'] and pages > v['page_limit'])}
    reviews = list(dict.fromkeys(p['reviews'] + [str(Path(x).resolve()) for x in (getattr(a, 'reviews', None) or [])]))
    if any('B_refereeing' in Path(x).resolve().parts for x in reviews):
        raise ValueError('external refereeing reviews are excluded')
    excerpts = [{'path': x, 'excerpt': Path(x).read_text()[:16000]} for x in reviews]
    if not eligible:
        raise ValueError('no eligible venues; check paper areas, page budget and deadlines')
    instruction = core.prompt('venue_fit', paper=json.dumps(p, ensure_ascii=False), pages=pages,
                              venues=json.dumps(list(eligible.values()), ensure_ascii=False),
                              reviews=json.dumps(excerpts, ensure_ascii=False))
    provider = getattr(a, 'provider', None)
    if (Path(__file__).parent / 'roles.py').exists():
        from lab.roles import call
        reply = call('review', instruction, ROOT, write=False, provider=provider)
    else:
        reply = core.invoke(provider or 'claude', instruction, ROOT, model='haiku', write=False)
    candidates = core.last_json(reply)['candidates']
    if not isinstance(candidates, list) or not candidates:
        raise ValueError('expected nonempty candidates list')
    clean, seen = [], set()
    for c in candidates:
        v = eligible.get(c['venue'])
        if not v or c['venue'] in seen or type(c['fit']) not in (int, float) or not math.isfinite(c['fit']) or not 0 <= c['fit'] <= 1:
            raise ValueError('invalid or ineligible candidate')
        if not isinstance(c['why'], str) or not c['why'].strip() or not isinstance(c['changes'], list) or not all(isinstance(x, str) and x.strip() for x in c['changes']) or not c['changes']:
            raise ValueError('candidate needs reasons and required changes')
        seen.add(c['venue'])
        clean.append(dict(venue=c['venue'], fit=c['fit'], why=c['why'], changes=c['changes'],
                          deadline=v['next_deadline'], deadline_verified=v['deadline_verified']))
    p['reviews'] = reviews
    p['pivot'] = dict(trigger='rejected' if p['stage'] == 'rejected' else 'manual',
                      **{'from': p['venue']}, candidates=sorted(clean, key=lambda c: -c['fit']), chosen='')
    _save(data)
    print(json.dumps(p['pivot']['candidates'], ensure_ascii=False, indent=2))


def paper_pivot(a):
    data, p = _load(a)
    pivot = p.get('pivot')
    if not pivot or p['stage'] not in ('rejected', 'withdrawn', 'draft', 'internal_review'):
        raise ValueError('rank venues first; pivot only inactive submissions or drafts')
    c = next((c for c in pivot['candidates'] if c['venue'] == a.venue), None)
    if not c or a.venue.casefold().strip() == p['venue'].casefold().strip():
        raise ValueError('choose a different ranked venue')
    if c['deadline_verified'] and c['deadline'] and c['deadline'] <= dt.date.today().isoformat():
        raise ValueError('chosen deadline has expired')
    v = next(v for v in json.loads((ROOT / 'papers/venues.json').read_text())['venues'] if v['name'] == a.venue)
    path = ROOT / 'papers/pivots' / f"{p['id']}.md"
    path.parent.mkdir(exist_ok=True)
    old, stage = p['venue'], p['stage']
    checklist = [f"# {p['title']} → {a.venue}", f"- [ ] Format: {v['format']}",
                 f"- [ ] Page budget: {v['page_limit'] or '미확인; official CFP 확인'}", f"- [ ] Reframing: {c['why']}"]
    checklist += [f'- [ ] {x}' for x in c['changes']] + [f'- [ ] Reviewer concerns: [{Path(x).name}]({x})' for x in p['reviews']]
    path.write_text('\n'.join(checklist) + '\n')
    pivot['chosen'] = a.venue
    p.update(stage='draft', venue=a.venue, next_deadline=dict(venue=a.venue, date=c['deadline'],
             verified=c['deadline_verified'], source=v['source']) if c['deadline'] else None)
    _history(p, f"pivot from {old} after {'rejection' if stage == 'rejected' else stage}", str(path))
    _save(data)
    print(path)


def selftest():
    import tempfile
    from types import SimpleNamespace as NS
    from unittest.mock import patch
    with tempfile.TemporaryDirectory() as tmp, patch(__name__ + '.ROOT', Path(tmp)):
        (ROOT / 'papers').mkdir()
        evidence = ROOT / 'review.md'; evidence.write_text('Address leakage and baselines.')
        p = dict(id='test', title='DeFi oracle', result='', path=str(evidence), stage='draft', venue='Old', history=[], reviews=[], pivot=None)
        _save(dict(stages=STAGES, papers=[p]))
        a = NS(id='test', stage='submitted', venue='Old', note='receipt', evidence=str(evidence))
        paper_set(a); a.stage = 'rejected'; paper_set(a)
        for stage in ('accepted', 'draft', 'submitted'):
            a.stage = stage
            try:
                paper_set(a)
            except ValueError:
                pass
            else:
                raise AssertionError('forbidden transition accepted')
        base = dict(kind='journal', areas=['DeFi'], page_limit=None, format='LNCS', next_deadline='', deadline_verified=False, source='test')
        venues = [dict(base, name='Good', id='good'), dict(base, name='Expired', id='old', next_deadline='2000-01-01', deadline_verified=True)]
        (ROOT / 'papers/venues.json').write_text(json.dumps(dict(venues=venues)))
        venues += [dict(base, name='Second', id='second'), dict(base, name='WrongArea', id='wrong', areas=['macro'])]
        (ROOT / 'papers/venues.json').write_text(json.dumps(dict(venues=venues)))
        result = dict(candidates=[dict(venue='Second', fit=.2, why='Broad fit', changes=['Reframe']),
                                  dict(venue='Good', fit=.8, why='DeFi fit', changes=['Address leakage from review.md'])])
        (ROOT / 'fake.py').write_text('import sys,json\ns=sys.stdin.read()\nassert "Expired" not in s and "WrongArea" not in s\nprint(' + repr(json.dumps(result)) + ')\n')
        venue_fit(NS(id='test', reviews=[str(evidence)], provider='cmd:python3 fake.py'))
        assert _load(a)[1]['pivot']['candidates'][0]['venue'] == 'Good'
        tex = ROOT / 'main.tex'; tex.write_text('test')
        tex.with_suffix('.aux').write_text(r'\gdef\@abspage@last{12}')
        assert _pages(tex) == 12
        paper_pivot(NS(id='test', venue='Good'))
        _, p = _load(a)
        assert p['stage'] == 'draft' and p['history'][-1]['note'] == 'pivot from Old after rejection'
        assert 'Address leakage' in (ROOT / 'papers/pivots/test.md').read_text()
        a.stage = 'submitted'; a.venue = 'Good'; paper_set(a)
        paper_status(NS())
    print('papers selftest ok')
