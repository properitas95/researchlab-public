"""Paper cards from alpha full text; quoted evidence is checked before storage."""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from lab import core

ROOT = core.ROOT
LOCK = threading.Lock()  # card writes and INDEX.md rebuilds from concurrent read-field workers


def _command(*args):
    p = subprocess.run([shutil.which(args[0]) or args[0], *args[1:]], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180)
    if p.returncode or not p.stdout.strip():
        raise RuntimeError(p.stderr.strip() or 'empty paper response')
    return p.stdout


def _sections(text):
    heading = r'(?mi)(?=^(?:#{1,6}\s+\S|\d+(?:\.\d+)*[.\s]+[A-Z][^\n]{0,100}$|(?:Abstract|Introduction|Conclusions?|References)\s*$))'
    return [s for s in re.split(heading, text) if s.strip()]


def _skim(text):
    parts = _sections(text)
    kept = [' '.join(parts[0].split()[:1500])] if parts else []
    for s in parts:
        head = s.splitlines()[0].lower()
        if 'abstract' in head or 'conclu' in head:
            kept.append(s)
        elif 'intro' in head:
            kept.append(' '.join(s.split()[:1500]))
    if len(kept) == 1:
        kept.append(' '.join(text.split()[:1500]))
    kept.extend(m[0] for m in re.finditer(r'(?mi)^\s*(?:Figure|Fig\.|Table)\s+\d+[^\n]*(?:\n(?!\s*\n)[^\n]+)*', text))
    return '\n\n'.join(kept)


def _checked(obj, text):
    source, dropped = ' '.join(text.split()), 0
    def visit(value):
        nonlocal dropped
        if isinstance(value, dict):
            q = value.get('quote')
            if 'quote' in value and (not isinstance(q, str) or not q.strip() or ' '.join(q.split()) not in source):
                dropped += 1
                return None
            for key in ('claims', 'numbers'):
                for item in value.get(key, []):
                    if isinstance(item, dict):
                        item.setdefault('quote', '')
            return {k: visit(v) for k, v in value.items()}
        if isinstance(value, list):
            return [v for item in value if (v := visit(item)) is not None]
        return value
    return visit(obj), dropped


def _call(name, text, thesis, d, c, phase=''):
    instruction = core.prompt(name, text=text, thesis=thesis, phase=phase)
    with tempfile.TemporaryDirectory(prefix='read-') as scratch:  # codex writes its last message into cwd: parallel readers need their own
        scratch = scratch if c['provider'] == 'codex' else d or ROOT
        try:
            from lab import roles
        except ImportError:
            reply = core.invoke(c['provider'], instruction, scratch, c.get('model'), False)
        else:
            reply = roles.call('novelty', instruction, cwd=scratch, run=d, write=False,
                               provider=c['provider'], model=c.get('model') or '')
    result = core.last_json(reply)
    if not isinstance(result, dict):
        raise ValueError('paper reply must be a JSON object')
    return result


def _fetch_pdf(sid, dest):
    """Save arxiv.org/pdf/<sid> (cap 20 MB); return '' on success, else the reason."""
    if not re.fullmatch(r'\d{4}\.\d{4,5}(?:v\d+)?', sid):
        return 'not an arXiv id'
    try:
        with urllib.request.urlopen(f'https://arxiv.org/pdf/{sid}', timeout=30) as r:
            data = r.read(20_000_001)
        if len(data) > 20_000_000 or not data.startswith(b'%PDF'):
            return 'too large' if len(data) > 20_000_000 else 'not a PDF'
        dest.write_bytes(data)
        return ''
    except Exception as e:
        return f'{type(e).__name__}: {e}'[:200]


def _store(sid, source, text, card, meta):
    """<workspace>/papers/<sid>/{paper.md, meta.json, card.json, paper.pdf}: a deep card is not replaced by a skim."""
    out = ROOT / 'papers' / sid
    out.mkdir(parents=True, exist_ok=True)
    (out / 'paper.md').write_text(text, encoding='utf-8')
    old = json.loads((out / 'meta.json').read_text()) if (out / 'meta.json').exists() else {}
    m = dict(id=sid, title=card.get('title') or meta.get('title') or sid, authors=card.get('authors') or meta.get('authors') or [],
             year=meta.get('year') or old.get('year'), url=meta.get('url') or old.get('url') or '', venue=meta.get('venue') or old.get('venue') or '',
             source=meta.get('source') or old.get('source') or 'alpha', fetched_utc=old.get('fetched_utc') or core.now(), sha256=hashlib.sha256(text.encode()).hexdigest())
    if meta.get('full_text') or old.get('full_text'):
        m['full_text'] = meta.get('full_text') or old['full_text']
    if (out / 'paper.pdf').exists():
        m['pdf'] = 'paper.pdf'
    elif not Path(source).is_file():
        why = _fetch_pdf(sid, out / 'paper.pdf')
        m.update(pdf='paper.pdf') if not why else m.update(pdf_note=why)
    (out / 'meta.json').write_text(json.dumps(m, ensure_ascii=False, indent=2) + '\n')
    cp = out / 'card.json'
    if not (card.get('mode') == 'skim' and cp.exists() and json.loads(cp.read_text()).get('mode') == 'deep'):
        cp.write_text(json.dumps(card, ensure_ascii=False, indent=2) + '\n')


def read(a):
    d = core.run_dir(a.run) if getattr(a, 'run', None) else None
    c = core.cfg(d) if d else core.DEFAULTS.copy()
    if getattr(a, 'provider', None):
        c['provider'] = a.provider
    if getattr(a, 'model', None):
        c['model'] = a.model
    thesis = getattr(a, 'thesis', None)
    if thesis is None and d and (d / 'pick.json').exists():
        thesis = json.loads((d / 'pick.json').read_text()).get('hypothesis', '')
    paper, mode = str(a.paper), getattr(a, 'mode', None) or 'skim'
    if mode not in ('skim', 'deep'):
        raise ValueError('mode must be skim or deep')
    local = Path(paper).is_file()
    if local and not shutil.which('pdftotext'):
        raise RuntimeError('local PDF requires pdftotext')
    match = re.search(r'(?:\d{4}\.\d{4,5}|[a-z-]+/\d{7})(?:v\d+)?', paper)
    sid = match[0] if match and not local else Path(paper).stem + '-' + hashlib.sha256(paper.encode()).hexdigest()[:10]
    sid = re.sub(r'[^\w.-]', '_', sid, flags=re.ASCII).strip('.')
    stored = ROOT / 'papers' / sid / 'paper.md'
    meta = dict(getattr(a, 'meta', None) or {})
    from lab import arxiv
    via_arxiv = not local and arxiv.use_arxiv()
    if stored.exists():  # a paper already in the workspace is not downloaded again
        text = stored.read_text(encoding='utf-8')
    elif via_arxiv:
        text, meta['full_text'] = arxiv.fulltext(sid)
        meta['source'] = 'arxiv-api'
    else:
        try:
            text = _command('pdftotext', '-layout', str(Path(paper).resolve()), '-') if local else _command('alpha', 'get', '--full-text', paper)
        except (RuntimeError, OSError, subprocess.TimeoutExpired):
            if local or via_arxiv:
                raise
            text, meta['full_text'] = arxiv.fulltext(sid)  # alpha failed for this paper: one retry through the arXiv path
            meta['source'], via_arxiv = 'arxiv-api', True
    dropped, sections, implementation = 0, [], ''
    if mode == 'skim':
        card = _call('read_skim', _skim(text), thesis or '', d, c)
        if type(card.get('worth_deep')) is not bool:
            raise ValueError('skim reply needs boolean worth_deep')
    else:
        chunks, buf = [], ''
        for section in _sections(text):  # adjacent small sections share a call: one call per heading made a deep read take 100+ calls
            for start in range(0, len(section), 12000):
                piece = section[start:start+12000]
                if buf and len(buf) + len(piece) > 12000:
                    chunks.append(buf)
                    buf = ''
                buf += piece
        for chunk in chunks + [buf] * bool(buf):
            obj, n = _checked(_call('read_deep', chunk, thesis or '', d, c, 'extract'), text)
            dropped += n
            sections.append(obj)
        link = re.search(r'https://github\.com/[\w.-]+/[\w.-]+', text)
        if link and not via_arxiv:  # `alpha code` needs alpha
            url = link[0].rstrip('.')
            try:
                implementation = url + '\n' + _command('alpha', 'code', url) + '\n' + _command('alpha', 'code', url, 'README.md')
            except (RuntimeError, OSError, subprocess.TimeoutExpired) as e:
                implementation = url + ': unavailable: ' + str(e)
        card = _call('read_deep', json.dumps(sections, ensure_ascii=False), thesis or '', d, c, 'synthesize')
    card, n = _checked(card, text)
    if card is None or card.get('relation') not in ('supports', 'refutes', 'neutral', 'unclear'):
        raise ValueError('paper reply needs a valid relation')
    card.update(id=sid, source=paper, date_read=core.now()[:10], mode=mode, thesis=thesis or '',
                dropped_quotes=dropped+n, sections=sections, implementation=implementation)
    _store(sid, paper, text, card, meta)
    out = ROOT / 'memory/global/papers'
    out.mkdir(parents=True, exist_ok=True)
    fields = ('authors', 'source', 'date_read', 'mode', 'worth_deep', 'claim', 'method', 'data', 'equations',
              'numbers', 'limitations', 'reproduction', 'implementation', 'thesis', 'relation', 'why', 'dropped_quotes')
    body = '# ' + str(card.get('title') or sid) + '\n'
    for k in fields:
        v = card.get(k, 'Not reported')
        body += '\n## ' + k + '\n' + (v if isinstance(v, str) else json.dumps(v, ensure_ascii=False, indent=2)) + '\n'
    with LOCK:
        (out / f'{sid}.json').write_text(json.dumps(card, ensure_ascii=False, indent=2) + '\n')
        (out / f'{sid}.md').write_text(body)
        index = []
        for path in sorted(out.glob('*.json')):
            saved = json.loads(path.read_text())
            title = ' '.join(str(saved.get('title') or path.stem).split())
            index.append(f'- [{path.stem}]({path.stem}.md) {title}')
        (out / 'INDEX.md').write_text('\n'.join(index) + '\n')
    if d:
        core.event(d, 'read', f'{sid} {mode}', source='arxiv-api' if via_arxiv else 'alpha', card=str(out / f'{sid}.md'), dropped_quotes=dropped+n)
    print(out / f'{sid}.md')
    return card


def novelty_from_cards(run):
    d = core.run_dir(run)
    path = d / 'novelty.json'
    novelty = json.loads(path.read_text())
    candidates = json.loads((d / 'candidates.json').read_text())
    cards = [json.loads(p.read_text()) for p in (ROOT / 'memory/global/papers').glob('*.json')]
    def words(text):
        return set(re.findall(r'\w{3,}', text.lower())) - {'the', 'and', 'for', 'with', 'from', 'that', 'this'}
    for candidate in candidates:
        entry = novelty.get(candidate['id'])
        if entry is not None:
            keys = words(candidate.get('title', '') + ' ' + candidate.get('hypothesis', ''))
            entry['cards'] = [x['id'] for x in cards if keys & words(str(x.get('title', '')) + ' ' + str(x.get('claim', '')))]
    path.write_text(json.dumps(novelty, ensure_ascii=False, indent=1) + '\n')
    return novelty


def selftest():
    from unittest.mock import patch
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        alpha = root / 'alpha'
        alpha.write_text('#!/usr/bin/env python3\nprint("# Portfolio\\nAbstract\\nRisk is 7 percent.\\n1 Introduction\\nPortfolio risk.\\n2 Conclusion\\nRisk is 7 percent.")\n')
        alpha.chmod(0o755)
        if os.name == 'nt':  # Windows does not run extensionless scripts; a .cmd shim finds the fake first
            (root / 'alpha.cmd').write_text(f'@"{sys.executable}" "%~dp0alpha" %*\n', encoding='utf-8')
        payload = dict(title='Portfolio', worth_deep=True, claim='Portfolio risk', relation='neutral', why='test',
                       claims=[dict(text='real', quote='Risk is 7 percent.', page_or_section='Abstract'), dict(text='bad', quote='fabricated')],
                       numbers=[dict(value=7, unit='percent', context='risk', quote='Risk is 7 percent.')])
        (root / 'fake.py').write_text('import sys\nsys.stdin.read()\nprint(' + repr(json.dumps(payload)) + ')\n')
        core.save_cfg(root, dict(provider='cmd:python3 fake.py'))
        (root / 'pick.json').write_text('{"hypothesis":"risk"}')
        a = SimpleNamespace(run=str(root), paper='2607.18624', mode='skim')
        with patch(__name__ + '.ROOT', root), patch(__name__ + '._fetch_pdf', return_value='offline'), patch.dict(os.environ, PATH=str(root)+os.pathsep+os.environ['PATH']):
            assert read(a)['worth_deep'] is True
            store = root / 'papers/2607.18624'
            meta = json.loads((store / 'meta.json').read_text())
            assert meta['sha256'] == hashlib.sha256((store / 'paper.md').read_bytes()).hexdigest() and meta['pdf_note'] == 'offline' and meta['source'] == 'alpha'
            alpha.write_text('#!/usr/bin/env python3\nraise SystemExit(1)\n')  # stored text: no second download
            assert read(a)['worth_deep'] is True
            checked, count = _checked({'numbers': [{'value': 3}, {'quote': 'Risk\n is 7 percent.'}]}, 'Risk is 7 percent.')
            assert count == 1 and len(checked['numbers']) == 1
            assert len(_skim('word '*5000).split()) <= 3000
            a.mode = 'deep'
            card = read(a)
            out = root / 'memory/global/papers'
            assert json.loads((store / 'card.json').read_text())['mode'] == 'deep'
            read(SimpleNamespace(run=str(root), paper='2607.18624', mode='skim'))
            assert json.loads((store / 'card.json').read_text())['mode'] == 'deep'  # a skim never replaces a deep card
            assert card['dropped_quotes'] > 0 and all(x['text'] != 'bad' for x in card['claims'])
            assert (out / '2607.18624.md').exists() and '2607.18624' in (out / 'INDEX.md').read_text()
            assert json.loads((out / '2607.18624.json').read_text())['thesis'] == 'risk'
            (root / 'candidates.json').write_text('[{"id":"C1","title":"Portfolio risk"}]')
            (root / 'novelty.json').write_text('{"C1":{"verdict":"unclear"}}')
            assert novelty_from_cards(str(root))['C1'] == dict(verdict='unclear', cards=['2607.18624'])
            assert all(len(s[i:i+12000]) <= 12000 for s in _sections('x'*25000) for i in range(0, len(s), 12000))
    print('read selftest ok (skim, deep, quotes, index, novelty)')
