"""Read manuscript sources into portfolio/citations.json; no model or TeX execution."""
import datetime as dt
import json
import os
import re
import tempfile
import unicodedata
from collections import Counter
from pathlib import Path
from lab import core

ROOTS = tuple(Path(p) for p in (os.environ.get('LAB_PAPER_ROOTS') or str(core.ROOT / 'papers')).split(os.pathsep))  # unset: <workspace>/papers
SKIP = re.compile(r'^(?:archive|old|backup|tmp)(?:s|$|[_-])|^\.|^(?:third_party|vendor|node_modules)$', re.I)
GROUPS = (
    ('DeFi/crypto', r'\bdefi\b|\bamm\b|perpetual|crypto|bitcoin|funding rate'),
    ('ML/LLM', r'\bllms?\b|language model|transformer|neural|machine learning|deep learning'),
    ('microstructure', r'order.?book|liquidity|market.?mak|microstructure|price discovery'),
    ('portfolio/risk', r'portfolio|covariance|shrinkage|minimum variance|\brisk\b'),
    ('econometrics', r'garch|regression|bootstrap|inference|econometric'),
    ('asset pricing', r'asset pricing|factor|anomal|return predict'),
    ('macro', r'inflation|monetary|macro|\bfed\b|federal reserve'))

def allowed(p):
    p = p.resolve()
    return any(p.is_relative_to(r) for r in ROOTS) and 'external' not in p.parts

def read(p):
    return re.sub(r'(?m)(?<!\\)%.*$', '', p.read_text(encoding='utf-8', errors='replace'))

def clean(s):
    s = re.sub(r'\\[\'`"^~=.]\s*\{?([A-Za-z])\}?|\\[uvHckbdtr]\s*\{([A-Za-z])\}',
               lambda m: m[1] or m[2], s)
    s = re.sub(r'\\(?:ae|oe|ss|o|l|AA|AE|OE)\b', lambda m: m[0][1:], s)
    s = re.sub(r'\\[a-zA-Z]+\*?', '', s).replace('\\', '')
    return ' '.join(s.translate(str.maketrans('', '', '{}')).split())

def split(s, delimiter=','):
    depth, quote, escape, start = 0, False, False, 0
    for i, c in enumerate(s):
        if escape:
            escape = False
            continue
        if c == '\\':
            escape = True
        elif c == '"' and depth == 0:
            quote = not quote
        elif not quote:
            depth += (c == '{') - (c == '}')
            if c == delimiter and depth == 0:
                yield s[start:i].strip()
                start = i + 1
    yield s[start:].strip()

def bib(text):
    pos = 0
    while m := re.search(r'@(\w+)\s*([{(])', text[pos:]):
        start = pos + m.end(); end = start; depth = 0; quote = False; escape = False
        closing = '}' if m[2] == '{' else ')'
        while end < len(text):
            c = text[end]; end += 1
            if escape:
                escape = False
            elif c == '\\':
                escape = True
            elif c == '"' and depth == 0:
                quote = not quote
            elif not quote:
                if c == closing and depth == 0:
                    break
                depth += (c == '{') - (c == '}')
        pos = end
        if m[1].lower() in ('comment', 'preamble', 'string'):
            continue
        parts = list(split(text[start:end - 1])); fields = {}
        for item in parts[1:]:
            k, sep, v = item.partition('=')
            if sep:
                fields[k.strip().lower()] = clean(v.strip().strip('"'))
        if parts and parts[0]:
            yield parts[0], fields

def sources(path, boundaries=()):
    folder = path if path.is_dir() or not path.exists() else path.parent
    files = []
    for base, dirs, names in os.walk(folder):
        dirs[:] = sorted(d for d in dirs if not SKIP.search(d) and allowed(Path(base) / d)
                         and (Path(base) / d).resolve() not in boundaries)
        files += [Path(base) / n for n in names if Path(n).suffix.lower() in ('.tex', '.bib')
                  and allowed(Path(base) / n)]
    tex = [p for p in files if p.suffix.lower() == '.tex'
           and not re.search(r'\\documentclass(?:\[[^]]*\])?\{(?:beamer|tikzposter|standalone)\}', read(p))
           and not re.search(r'cover.?letter|response|template', p.stem, re.I)]
    tex.sort(key=lambda p: (bool(re.search(r'\\documentclass\b', read(p))), p.stat().st_mtime, str(p)), reverse=True)
    main = tex[:1]; texts = []; stack = []

    def visit(p):
        p = p.resolve()
        if p in stack or not allowed(p) or not p.is_file():
            return
        stack.append(p); text = read(p); texts.append((p, text))
        for match in re.finditer(r'\\(?:input|include)\s*(?:\{([^}]+)\}|([^\s{}]+))', text):
            name = match[1] or match[2]
            name = name if Path(name).suffix else name + '.tex'
            child = p.parent / name
            visit(child if child.is_file() else main[0].parent / name)
        stack.pop()
    for p in main:
        visit(p)
    used = []
    for p, text in texts:
        for names in re.findall(r'\\(?:bibliography|addbibresource)\s*(?:\[[^]]*\]\s*)?\{([^}]+)\}', text):
            for name in names.split(','):
                name = name.strip(); f = p.parent / (name if name.endswith('.bib') else name + '.bib')
                if allowed(f) and f.is_file() and f.resolve() not in used:
                    used.append(f.resolve())
    if not used:
        used = sorted((p.resolve() for p in files if p.suffix.lower() == '.bib'),
                      key=lambda p: (p.stat().st_mtime, str(p)), reverse=True)
    return folder, main, used, '\n'.join(t for _, t in texts)

def collect(p, boundaries=()):
    folder, tex, files, text = sources(Path(p['path']), boundaries)
    counts = Counter()
    for m in re.finditer(r'\\(?!nocite\b)([A-Za-z]*[Cc]ite[A-Za-z]*)\*?\s*'
                         r'((?:(?:\[[^]]*\]\s*)*\{[^}]+\}\s*)+)', text):
        groups = re.findall(r'\{([^}]+)\}', re.sub(r'\[[^]]*\]', '', m[2]))
        for keys in groups if m[1].lower().endswith('cites') else groups[:1]:
            counts.update(k.strip() for k in keys.split(',') if k.strip())
    refs = {}
    for f in files:
        for key, fields in bib(read(f)):
            if key in refs:
                continue
            title = fields.get('title', ''); authors = fields.get('author', '').split(' and ')
            venue = next((fields[k] for k in ('journal', 'booktitle', 'howpublished') if fields.get(k)), '')
            venue = venue or ('arXiv' if fields.get('eprint') else '')
            year = re.search(r'\b\d{4}\b', fields.get('year', ''))
            group = next((g for g, pattern in GROUPS if re.search(pattern, title + ' ' + venue, re.I)), 'other')
            refs[key] = dict(key=key, title=title, authors=authors[0] + (' et al.' if len(authors) > 1 else ''),
                             year=int(year[0]) if year else None, venue=venue, cited=bool(counts[key]), count=counts[key], group=group)
    row = {k: p.get(k, '') for k in ('id', 'title', 'stage', 'venue')}
    row.update(folder=str(folder.resolve()), bib_files=list(map(str, files)), tex_files=list(map(str, tex)),
               refs=list(refs.values()), n_bib=len(refs), n_cited=sum(r['cited'] for r in refs.values()))
    if not tex and not files:
        row['note'] = 'no manuscript source found'
    return row

def citations(a=None):
    registry = json.loads((core.ROOT / 'papers/registry.json').read_text())['papers']
    boundaries = {Path(p['path']).resolve() for p in registry if Path(p['path']).is_dir()}
    papers = [collect(p, boundaries) for p in registry if allowed(Path(p['path']))]
    shared = {}
    for p in papers:
        print(f"{p['id']}: n_bib={p['n_bib']} n_cited={p['n_cited']}")
        for r in p['refs']:
            norm = ''.join(c for c in unicodedata.normalize('NFKD', r['title']).lower() if c.isalnum())
            if r['cited'] and norm:
                s = shared.setdefault(norm, dict(norm_title=norm, title=r['title'], year=r['year'], papers=[]))
                if p['id'] not in s['papers']:
                    s['papers'].append(p['id'])
    result = dict(as_of=dt.date.today().isoformat(), papers=papers,
                  shared=[v for _, v in sorted(shared.items()) if len(v['papers']) >= 2])
    out = core.ROOT / 'portfolio/citations.json'; out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=1) + '\n')
    print(f"shared refs: {len(result['shared'])}")
    return result

def selftest():
    from unittest.mock import patch
    with tempfile.TemporaryDirectory() as t:
        d = Path(t).resolve(); (d / 'papers').mkdir(); (d / 'prompts').mkdir()
        (d / 'main.tex').write_text(r'\documentclass{article}\citet*{a,b}\input{section}\bibliography{refs}')
        (d / 'section.tex').write_text(r'\parencite[p. 2]{a}\textcite{a}\nocite{b}% \cite{b}')
        (d / 'refs.bib').write_text('@article{a,title={An {order book}},author={One and Two},year={2024}}\n'
                                    '@book(b, title="Neural models", booktitle={ML})')
        (d / 'old').mkdir(); (d / 'old/new.tex').write_text(r'\documentclass{article}\cite{b}')
        (d / 'slides.tex').write_text(r'\documentclass{beamer}\cite{b}')
        (d / 'empty').mkdir()
        (d / 'fake.py').write_text('import sys,json; print(json.dumps({"papers":json.loads(sys.stdin.read())}))')
        rows = [dict(id=str(i), path=t, title='toy') for i in range(2)]
        (d / 'prompts/citations.md').write_text(json.dumps(rows))
        registry = core.invoke('cmd:python3 fake.py', (d / 'prompts/citations.md').read_text(), d, None, False)
        (d / 'papers/registry.json').write_text(registry)
        with patch.object(core, 'ROOT', d), patch(__name__ + '.ROOTS', (d,)):
            result = citations(); a, b = result['papers'][0]['refs']
            assert collect(dict(path=str(d / 'empty')))['note'] == 'no manuscript source found'
            assert not allowed(d / 'Reviews/external/paper.tex')
            (d / 'main.tex').write_text(r'\documentclass{article}\citep{b}\addbibresource[location=local]{refs.bib}')
            assert collect(dict(path=str(d)))['n_cited'] == 1
            (d / 'main.tex').write_text(r'\documentclass{article}\parencites{a}[p. 2]{b}\input section')
            assert [r['count'] for r in collect(dict(path=str(d)))['refs']] == [3, 1]
        assert (a['count'], b['count'], a['group'], b['group']) == (3, 1, 'microstructure', 'ML/LLM')
        assert len(result['shared']) == 2 and a['authors'] == 'One et al.' and a['year'] == 2024
        assert dict(bib('@book(x,title={A (nested) title},year=2020)'))['x']['year'] == '2020'
        assert clean(r"{Caf\'{e}}") == 'Cafe'
    print('citations selftest ok')
