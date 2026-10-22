"""Paper workspaces in local Foundry. Read-only sources; owned dataset IDs only."""
import contextlib
import csv
import hashlib
import heapq
import json
import os
import re
import shlex
import tempfile
import time
import unicodedata
import urllib.error
import urllib.request
from pathlib import Path
from lab import core

PROTECTED = os.environ.get('LAB_HELIX_PROTECTED') or '00000000-0000-0000-0000-000000000000'  # workspace id that must never be written
RELATIONS = set('uses_data uses_tool derived_from extends reviewed_by supports refutes submitted_to pivoted_to'.split())
SKIP = {'.git', '.venv', 'node_modules', '__pycache__', 'build', '_render', 'tmp', 'external'}
REVIEW_ROOT = Path(os.environ['LAB_REVIEW_ROOT']) if os.environ.get('LAB_REVIEW_ROOT') else None  # unset: no external review folder is scanned
GLM = 'cmd:python3 -c ' + shlex.quote('import subprocess,sys; sys.exit(subprocess.call(["opencode","run","-m","opencode/glm-5.3-flash-free","--pure",sys.stdin.read()]))')
def read(p):
    return p.read_text(errors='replace') if p.is_file() else ''
def save(p, obj):
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix('.tmp')
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1) + '\n')
    tmp.replace(p)
def lines(p, rows):
    p.write_text(''.join(json.dumps(x, ensure_ascii=False) + '\n' for x in rows))
class API:
    def __init__(self, base, out):
        self.base, self.out = base.rstrip('/'), out

    def call(self, method, path, body=None):
        if PROTECTED in path and method != 'GET':
            raise ValueError('protected workspace')
        req = urllib.request.Request(self.base + '/api/v1' + path, method=method,
            data=json.dumps(body).encode() if body is not None else None, headers={'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(req, timeout=600 if path.endswith('/publish') else 180) as r:
                result, status = json.load(r), r.status
        except urllib.error.HTTPError as e:
            result, status = {'error': e.read().decode()}, e.code
        except OSError as e:
            result, status = {'error': str(e)}, 0
        with (self.out / 'upload_log.jsonl').open('a') as f:
            f.write(json.dumps(dict(utc=core.now(), method=method, path=path, status=status, result=result), ensure_ascii=False) + '\n')
        if not 200 <= status < 300:
            raise RuntimeError(f'{method} {path}: {status} {result}')
        return result
def inventory(path, deep=False, exclude=()):
    root = path if path.is_dir() else path.parent
    for base, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d.lower() not in SKIP and Path(base) / d not in exclude and not (Path(base) / d).is_symlink()) if deep or len(Path(base).relative_to(root).parts) < 2 else []
        for name in sorted(files):
            f = Path(base) / name
            if f.is_symlink() or (path.is_file() and f != path):
                continue
            s = f.stat()
            if not deep and s.st_size > 50 * 1024**2:
                continue
            ext = f.suffix.lower().lstrip('.') or 'none'
            role = next((r for r in ('review', 'log') if r in str(f.relative_to(root)).lower()), None)
            role = role or next((r for r, exts in [('manuscript', 'tex docx pdf'), ('figure', 'png jpg svg eps'), ('data', 'csv tsv json jsonl parquet'), ('code', 'py r sh ipynb')] if ext in exts.split()), 'other')
            yield f, dict(relpath=f.relative_to(root).as_posix(), kind=ext, bytes=s.st_size, mtime=core.dt.datetime.fromtimestamp(s.st_mtime, core.dt.timezone.utc).isoformat(), role=role)
def prepare(p, memories):
    path = Path(p['path']).expanduser()
    files, claims, sources = [], [], {}
    budget = 6000
    for f, row in inventory(path) if path.exists() else []:
        files.append(row)
        if f.name == 'claim_ledger.csv':
            with f.open(errors='replace') as stream:
                claims.extend(dict(claim=r.get('claim') or r.get('statement') or json.dumps(r, ensure_ascii=False), verdict=r.get('verdict') or r.get('status') or 'unassessed', source=str(f)) for r in csv.DictReader(stream))
        if f.name in {'RESULT.md', 'DECISIONS.md'}:
            claims.extend(dict(claim=ln, verdict='source_statement_unassessed', source=str(f)) for ln in read(f).splitlines() if ln.strip() and not ln.startswith('#'))
        if budget and f.suffix == '.tex':
            abstract = re.search(r'\\begin\{abstract\}(.*?)\\end\{abstract\}', read(f), re.S)
            if abstract: sources[str(f)] = abstract[1][:budget]; budget -= len(sources[str(f)])
        if budget and (f.name in {'README.md', 'AGENTS.md'} or 'abstract' in f.stem.lower()):
            sources[str(f)] = read(f)[:budget]
            budget -= len(sources[str(f)])
    if path.is_file() and budget:
        sources[str(path)] = read(path)[:budget]
    for name in ('README.md', 'AGENTS.md'):
        f = path.parent / name
        if budget and f.is_file() and str(f) not in sources:
            sources[str(f)] = read(f)[:budget]; budget -= len(sources[str(f)])
    project = path.parent.name if path.is_file() else path.name
    normal = lambda x: re.sub(r'[^a-z0-9]', '', x.lower())
    notes = [(3 if set(p['id'].split('-')) <= set(f.stem.split('-')) else 2 if normal(project) == normal(f.stem) else 1 if str(path) in read(f) else 0, f) for f in memories]
    for score, f in sorted(notes, reverse=True)[:2]:
        if score and score == max(x[0] for x in notes): sources[str(f)] = read(f)[:12000]
    tables = dict(paper=[{k: p.get(k, p.get('status', '') if k == 'stage' else '') for k in ('id', 'title', 'stage', 'venue', 'result', 'path')}],
        history=[{k: h.get(k, '') for k in ('date', 'stage', 'venue', 'note', 'evidence')} for h in p.get('history', [])], files=files, claims=claims)
    return tables, sources
def relations(p, sources, known, folder, provider):
    instruction = core.prompt('relations', paper=p['id'], sources=json.dumps(sources, ensure_ascii=False), known=json.dumps(known, ensure_ascii=False))
    save(folder / 'relations_sources.json', sources)
    digest = hashlib.sha256((provider + instruction).encode()).hexdigest()
    cache = folder / 'relations_cache.json'
    old = json.loads(read(cache) or '{}')
    if old.get('hash') == digest:
        return old['edges'], old['dropped']
    with tempfile.TemporaryDirectory(prefix='helix-glm-') as cwd:
        reply = core.invoke(provider, instruction, cwd, None, False, timeout=180)
    (folder / 'relations_reply.txt').write_text(reply)
    edges, dropped = [], []
    candidates = core.last_json(reply)
    if not isinstance(candidates, list):
        raise ValueError('relations response is not an array')
    norm = lambda s: ' '.join(s.split())
    for e in candidates:
        reason = 'invalid shape'
        if isinstance(e, dict) and all(isinstance(e.get(k), str) for k in ('subject', 'relation', 'object', 'quote', 'source')):
            reason = ('unknown relation' if e['relation'] not in RELATIONS else 'unknown endpoint' if e['subject'] not in known or e['object'] not in known else 'quote not in source' if not norm(e['quote']) or norm(e['quote']) not in norm(sources.get(e['source'], '')) else '')
        (dropped if reason else edges).append({'edge': e, 'reason': reason} if reason else e)
    save(cache, dict(hash=digest, edges=edges, dropped=dropped))
    return edges, dropped
def capped(files):
    counts = {}
    def counted():
        for r in files:
            directory = os.path.dirname(r['relpath']) or '.'
            counts[directory] = counts.get(directory, 0) + 1
            yield r
    ordered = heapq.nsmallest(2000, counted(), key=lambda r: (r['role'] not in {'manuscript', 'review', 'figure', 'code'}, r['relpath']))
    if sum(counts.values()) <= 2000: return ordered
    keep = 1900
    for r in ordered[:keep]:
        directory = os.path.dirname(r['relpath']) or '.'
        counts[directory] -= 1
    counts = {d: n for d, n in counts.items() if n}
    # Deep repositories need ancestor summaries to leave room for priority files.
    while len(counts) > 100:
        parents = {}
        for directory, count in counts.items():
            parent = os.path.dirname(directory) or '.'
            parents[parent] = parents.get(parent, 0) + count
        counts = parents
    return ordered[:keep] + [dict(relpath=d + '/[summary]', role='directory_summary', count=n) for d, n in sorted(counts.items())]

def relation_rows(edges):
    rows = [dict(from_id=e.get('from', e.get('subject')), to_id=e.get('to', e.get('object')),
                 rel=e.get('rel', e.get('relation')), quote=e.get('quote', ''), source=e.get('source', 'portfolio/portfolio.json')) for e in edges]
    return [dict(relation_id=hashlib.sha256(json.dumps(r, sort_keys=True).encode()).hexdigest()[:24], **r) for r in rows]

def citation_tables(data, key):
    if key == 'overview':
        refs, edges = [], []
        for r in data['shared']:
            rid = hashlib.sha256(r['norm_title'].encode()).hexdigest()[:24]
            refs.append(dict(ref_id=rid, title=r['title'], year=r['year'], norm_title=r['norm_title']))
            edges.extend(dict(citation_id=p + ':' + rid, paper_id=p, ref_id=rid) for p in sorted(set(r['papers'])))
        if len({r['ref_id'] for r in refs}) != len(refs): raise ValueError('duplicate shared reference')
        return dict(shared_references=refs, share_citations=edges), [
            ('SHARE_PAPER', 'ShareCitation', 'Paper', 'paper_id', 'id'),
            ('SHARE_REFERENCE', 'ShareCitation', 'SharedReference', 'ref_id', 'ref_id')]
    refs, edges = [], []
    shared = {r['norm_title']: r['papers'] for r in data['shared']}
    paper = next((p for p in data['papers'] if p['id'] == key), {})
    for r in paper.get('refs', []):
        rid = key + ':' + r['key']
        norm = ''.join(c for c in unicodedata.normalize('NFKD', r['title']).lower() if c.isalnum())
        refs.append(dict(ref_id=rid, **{k: r[k] for k in ('title', 'authors', 'year', 'venue', 'group', 'count', 'cited')},
                         shared_with=','.join(sorted(set(shared.get(norm, [])) - {key}))))
        if r['cited']: edges.append(dict(citation_id=rid, paper_id=key, ref_id=rid, count=r['count']))
    if len({r['ref_id'] for r in refs}) != len(refs): raise ValueError('duplicate reference key: ' + key)
    return dict(references=refs, citations=edges), [
        ('CITATION_PAPER', 'Citation', 'Paper', 'paper_id', 'id'),
        ('CITATION_REFERENCE', 'Citation', 'Reference', 'ref_id', 'ref_id')]

def paper_root(p):
    path = Path(p.get('path') or p['folder']).expanduser().resolve()
    start = path.parent if path.is_file() else path
    for root in (start, *start.parents):
        if (root / '.git').exists(): return root
    return start

def paper_keywords(p):
    path = Path(p.get('path') or p['folder'])
    text = ' '.join((p['id'], p.get('title', ''), p.get('venue', ''), path.stem, paper_root(p).name)).lower()
    stop = set('paper papers research manuscript main chapter simulation report active 2026 workshop finance financial market only with from that what when where does into before after still trading risk model models cost price stock tokenized the and for not are docs user brief'.split())
    words = set(re.findall(r'[a-z][a-z0-9]+', text)) - stop
    words |= {w for w in re.findall(r'[a-z0-9]+(?:-[a-z0-9]+)+', text) if w.replace('-', '') in re.sub(r'[^a-z0-9]', '', text)}
    return {w for w in words if len(w) >= 3}

def paper_files(p, papers=(), review_root=REVIEW_ROOT):
    path = Path(p.get('path') or p['folder']).expanduser().resolve()
    repo = paper_root(p)
    peers = [q for q in papers if q['id'] != p['id'] and paper_root(q) == repo]
    keywords = paper_keywords(p)
    for q in peers: keywords -= paper_keywords(q)
    match = lambda name: any(re.search(r'(?<![a-z0-9])' + re.escape(w) + r'(?![a-z0-9])', name.lower()) for w in keywords)
    siblings = [Path(q.get('path') or q['folder']).expanduser().resolve() for q in peers]
    roots = [(path, False), (repo, bool(peers))]
    for base, dirs, _ in (os.walk(review_root, followlinks=False) if review_root else ()):
        dirs[:] = sorted(d for d in dirs if d.lower() not in SKIP | {'archive'} and not (Path(base) / d).is_symlink())
        for name in dirs[:]:
            if match(name): roots.append((Path(base) / name, False)); dirs.remove(name)
    visited = []
    for root, filtered in roots:
        if root in visited: continue
        excluded = set(visited) | {q for q in siblings if q != path and not path.is_relative_to(q)} | {core.ROOT / 'helix'}
        for f, row in inventory(root, deep=True, exclude=excluded) if root.exists() else []:
            if any(f == r or f.is_relative_to(r) for r in visited) or 'external' in {x.lower() for x in f.parts}: continue
            if review_root and f.is_relative_to(review_root) and 'archive' in {x.lower() for x in f.relative_to(review_root).parts}: continue
            if any(f != path and (f == q or f.is_relative_to(q)) and q != path and not path.is_relative_to(q) for q in siblings): continue
            if filtered and not match(str(f.relative_to(root))): continue
            if f.is_relative_to(core.ROOT / 'helix'): continue
            row['relpath'] = Path(os.path.relpath(f, repo)).as_posix(); row['path'] = f.as_posix()
            if f.suffix.lower() == '.tex': row['role'] = 'manuscript'
            elif document_kind(f) == 'review': row['role'] = 'review'
            yield f, row
        visited.append(root)

def document_kind(f):
    name = str(f).lower()
    if f.suffix.lower() == '.tex':
        return ('talk' if any(w in name for w in ('talk', 'slide', 'beamer')) else 'appendix' if 'appendix' in name else
                'manuscript_ko' if 'korean' in name or re.search(r'[_/-]ko[._/-]', name) else 'manuscript')
    return next((k for k, words in [('review', ('review', 'critique', 'referee')), ('decision', ('decision',)),
                ('protocol', ('protocol', 'amendment')), ('result', ('result', 'evaluation')),
                ('log', ('log', 'session', 'loop')), ('readme', ('readme', 'agents.md'))] if any(w in name for w in words)), 'note')

def document_rows(p, memories, files=None, stats=None):
    root = paper_root(p)
    sources, hashes, duplicates = {}, {}, {}
    candidates = [f for f, _ in (paper_files(p) if files is None else files)]
    for f in memories:
        text = read(f)
        if any(re.search(r'(?<![\w-])' + re.escape(s) + r'(?![\w-])', text, re.I) for s in (p['id'], p.get('title', ''), str(root)) if s): candidates.append(f)
    for f in dict.fromkeys(candidates):
        kind = document_kind(f)
        if f.suffix.lower() not in {'.tex', '.md', '.txt'} or f.is_symlink(): continue
        if (f.suffix.lower() == '.txt' or kind == 'log') and f.stat().st_size > 1024**2: continue
        raw = f.read_bytes()
        if b'\x00' in raw: continue
        try: text = raw.decode('utf-8-sig')
        except UnicodeDecodeError: continue
        digest = hashlib.sha256(raw).hexdigest()
        if digest in hashes:
            duplicates[hashes[digest]] += 1; continue
        hashes[digest] = f; duplicates[f] = 0
        sources[f] = (kind, digest)
    priority = {'manuscript': 0, 'manuscript_ko': 0, 'review': 0, 'decision': 1, 'result': 1}
    rows, total = [], 0
    for f, (kind, digest) in sorted(sources.items(), key=lambda item: (priority.get(item[1][0], 2), str(item[0]))):
        text = f.read_text(encoding='utf-8-sig')
        if f.suffix.lower() == '.tex': text = re.sub(r'(?m)(?<!\\)%.*$', '', text)
        rel = Path(os.path.relpath(f, root)).as_posix()
        if f.suffix.lower() == '.tex':
            text = re.sub(r'\\(?:subsection|section)\*?(?:\[[^\]]*\])?\{([^{}]*)\}', r'\n## \1\n', text)
            math = []
            def keep(m):
                math.append(m[0]); return '\x00' + str(len(math) - 1) + '\x00'
            text = re.sub(r'\\begin\{(equation\*?|align\*?|gather\*?|multline\*?)\}.*?\\end\{\1\}|\\\[.*?\\\]|\\\(.*?\\\)|\$\$.*?\$\$|(?<!\\)\$(?:\\.|[^$])*?\$', keep, text, flags=re.S)
            text = re.sub(r'\\(?:input|include|label|bibliography|bibliographystyle|documentclass|usepackage)(?:\[[^\]]*\])?\{[^}]*\}', '', text)
            text = re.sub(r'\\(?:begin|end)\{[^}]*\}|\\[A-Za-z@]+\*?(?:\[[^\]]*\])?', '', text)
            text = text.replace('{', '').replace('}', '')
            text = re.sub(r'\x00(\d+)\x00', lambda m: math[int(m[1])], text)
        section, body, sections, fence = f.stem, [], [], None
        for line in text.splitlines():
            marker = re.match(r'^\s*(`{3,}|~{3,})', line)
            if marker: fence = None if fence and marker[1][0] == fence else marker[1][0]
            heading = re.match(r'^#{1,2}\s+(.+?)\s*#*\s*$', line) if not fence else None
            if heading:
                sections.append((section, '\n'.join(body).strip())); section, body = heading[1], []
            else: body.append(line)
        sections.append((section, '\n'.join(body).strip()))
        order = 0
        for section, text in sections:
            for start in range(0, len(text), 4000):
                part = text[start:start + 4000]
                total += 1
                if len(rows) < 6000: rows.append(dict(doc_id=f"{p['id']}:{rel}:{order}", paper_id=p['id'], path=f.as_posix(), doc_kind=kind,
                                 sha256=digest, duplicates=duplicates[f],
                                 section=section if len(text) <= 4000 else f'{section} (part {start // 4000 + 1})', order=order, text=part, chars=len(part)))
                order += 1
    if stats is not None: stats.update(documents=len(sources), duplicates=sum(duplicates.values()), sections_cut=max(0, total - 6000))
    return rows[:6000]

def helix_documents(a):
    """Regenerate local documents/files only; published state stays unchanged."""
    out = Path(getattr(a, 'root', core.ROOT)) / 'helix'
    state = json.loads(read(out / 'state.json') or '{}')
    papers = json.loads(read(out.parent / 'papers/registry.json'))['papers']
    memories = sorted(Path(getattr(a, 'memory_dir', Path.home() / '.agents/memory/projects')).glob('*.md'))
    for p in papers:
        key = p['id']
        if not re.fullmatch(r'[\w.-]+', key) or key in {'.', '..', 'overview'}: raise ValueError('unsafe paper id')
        if state.get(key, {}).get('workspace') == PROTECTED: raise ValueError('protected workspace')
    results = {}
    for paper in papers:
        key = paper['id']; folder = out / key; folder.mkdir(parents=True, exist_ok=True)
        before = {}
        for label in ('documents', 'files'):
            f = folder / (label + '.jsonl')
            with f.open() if f.exists() else contextlib.nullcontext(()) as stream:
                before[label] = sum(1 for line in stream if line.strip())
        documents, stats = [], {}
        def file_rows():
            for f, row in paper_files(paper, papers):
                if f.suffix.lower() in {'.tex', '.md', '.txt'}: documents.append((f, row))
                yield row
        files = [dict(r, paper_id=key, id=key + ':files:' + str(i)) for i, r in enumerate(capped(file_rows()))]
        docs = document_rows(paper, memories, documents, stats)
        lines(folder / 'documents.jsonl', docs); lines(folder / 'files.jsonl', files)
        results[key] = dict(before=before, after=dict(documents=len(docs), files=len(files)), **stats)
        print(key, f"sections={before['documents']}->{len(docs)} files={before['files']}->{len(files)}",
              f"duplicates={stats['duplicates']} cut={stats['sections_cut']}", flush=True)
    return results

def publish(api, entry, tables, links, checkpoint, refresh_citations=True):
    if entry['workspace'] == PROTECTED: raise ValueError('protected workspace')
    if 'documents' not in tables and entry.get('counts', {}).get('documents'):
        tables = tables | {'documents': [json.loads(s) for s in read(api.out / entry['key'] / 'documents.jsonl').splitlines()]}
        if hashlib.sha256(json.dumps(tables['documents'], sort_keys=True).encode()).hexdigest() != entry['datasets']['documents']['hash']:
            raise ValueError('cached documents differ from published dataset')
    if 'documents' in tables:
        if any(r['paper_id'] not in {p['id'] for p in tables.get('paper', [])} for r in tables['documents']):
            raise ValueError('document paper endpoint missing')
        links = [l for l in links if l[0] != 'DOCUMENT_PAPER'] + [('DOCUMENT_PAPER', 'Document', 'Paper', 'paper_id', 'id')]
    source = read(api.out.parent / 'portfolio/citations.json')
    if source and refresh_citations:
        extra, citation_links = citation_tables(json.loads(source), entry['key'])
        papers = {r['id'] for r in tables.get('papers', tables.get('paper', []))}
        refs = {r['ref_id'] for r in extra.get('references', extra.get('shared_references', []))}
        if any(r['paper_id'] not in papers or r['ref_id'] not in refs for r in extra.get('citations', extra.get('share_citations', []))):
            raise ValueError('citation endpoint missing from its own dataset')
        tables = tables | extra; links = [l for l in links if l[0] not in {r[0] for r in citation_links}] + citation_links
        for label, rows in extra.items(): lines(api.out / entry['key'] / (label + '.jsonl'), rows)
    base = '/workspaces/' + entry['workspace']; types = []
    names = dict(nodes='Node', papers='Paper', paper='Paper', runs='Run', edges='Relation', relations='Relation', venues='Venue', files='File', claims='Claim', history='HistoryEvent',
                 references='Reference', citations='Citation', shared_references='SharedReference', share_citations='ShareCitation', documents='Document',
                 concepts='Concept', mentions='Mention', extracted_claims='ExtractedClaim')
    for label, rows in tables.items():
        prior = entry['datasets'].get(label, {})
        if not rows: continue
        digest = hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()
        if prior.get('hash') != digest:
            body = dict(name=entry['key'] + '__' + label, rows=rows)
            if prior.get('id'): body['datasetId'] = prior['id']
            ds = api.call('POST', base + '/ingest', body)
            entry['datasets'][label] = dict(id=ds['id'], hash='', rows=len(rows), active=True); checkpoint()
            if ds.get('data', {}).get('pendingVersion'):
                api.call('POST', base + '/datasets/' + ds['id'] + '/accept-version', {})
            entry['datasets'][label]['hash'] = digest; checkpoint()
        key = 'relation_id' if label in ('edges', 'relations') else 'ref_id' if label in ('references', 'shared_references') else 'citation_id' if label in ('citations', 'share_citations') else 'id'
        key = dict(documents='doc_id', concepts='concept_id', mentions='mention_id', extracted_claims='claim_id').get(label, key)
        columns = sorted(set().union(*(r.keys() for r in rows)))
        types.append(dict(name=names[label], datasetId=entry['datasets'][label]['id'], primaryKey=key, properties=columns, description='ResearchLab ' + label))
    for label, ds in entry['datasets'].items(): ds['active'] = bool(tables.get(label))
    onboarding = api.call('GET', base + '/onboarding')
    owned = {ds['id'] for ds in entry['datasets'].values()}
    excluded = (set(onboarding.get('excludedIds', [])) - owned) | {ds['id'] for ds in entry['datasets'].values() if not ds['active']}
    if excluded != set(onboarding.get('excludedIds', [])):
        api.call('PATCH', base + '/onboarding', {'excludedIds': sorted(excluded)})
    present = {t['name'] for t in types}
    links = [dict(name=n, fromType=f, toType=t, fromColumn=c, toColumn=k) for n, f, t, c, k in links if f in present and t in present]
    bundle = dict(summary='ResearchLab explicit ontology', assumptions=['Relations are source statements; joins do not establish scientific validity.'],
                  catalog=[], pipelines=[], ontology=types, relationships=links, actions=[],
                  # Foundry treats a workspace without a dashboard as unfinished onboarding and hides the explorer
                  dashboards=[dict(name='Overview', description='ResearchLab tables', widgets=[
                      dict(title=t['name'], type='metric', sql=f'SELECT count(*) AS "records" FROM "d_{t["datasetId"].replace("-", "")}"',
                           inputs=[t['datasetId']]) for t in types[:8]])])
    digest = hashlib.sha256(json.dumps([bundle, tables], sort_keys=True).encode()).hexdigest()
    current = api.call('GET', base + '/overview')
    generation = current['workspace']['data'].get('activeGeneration')
    owned = {ds['id'] for ds in entry['datasets'].values()}
    # pipeline-backed types come from onboarding's auto-suggestion over our own datasets; republishing replaces them
    if any(t['data'].get('datasetId') not in owned and not str(t['data'].get('datasetId', '')).startswith('pipeline')
           for t in current.get('objectType', [])):
        raise ValueError('workspace contains object types from unowned datasets')
    if entry.get('published_hash') != digest or entry.get('generation') != generation:
        # Import avoids inheriting stale onboarding signatures from auto-generated proposals.
        pending = entry.get('pending', {})
        if pending.get('digest') == digest:
            proposal = json.loads(read(api.out / entry['key'] / 'proposal.json'))
        else:
            proposal = api.call('POST', base + '/definitions/import', dict(version=1, proposals=[bundle]))['items'][0]
            save(api.out / entry['key'] / 'proposal.json', proposal)
            if not proposal['data']['validation']['valid']: raise ValueError('ontology validation failed: ' + str(proposal['data']['validation']['checks']))
            entry['pending'] = dict(digest=digest, id=proposal['id']); checkpoint()
        result = api.call('POST', base + '/proposals/' + proposal['id'] + '/publish', {'hash': proposal['data']['hash']})
        entry['proposal'] = proposal['id']; checkpoint()
        deadline = time.monotonic() + 600
        while True:
            current = api.call('GET', base + '/overview')
            generation = current['workspace']['data'].get('activeGeneration')
            published = next((p for p in current.get('proposal', []) if p['id'] == proposal['id']), result)
            if published.get('data', {}).get('status') == 'published' and generation == published['data'].get('publishedGeneration'): break
            if time.monotonic() >= deadline: raise TimeoutError('publication did not activate within 600s')
            time.sleep(2)
        entry.update(published_hash=digest, generation=generation); entry.pop('pending', None); checkpoint()
    object_counts = {t['name']: t['data']['objectCount'] for t in current.get('objectType', []) if t['data'].get('generation') == generation}
    if object_counts != {names[k]: len(v) for k, v in tables.items() if v}: raise ValueError('published object counts differ from source tables')
    graph = api.call('GET', base + '/graph?limit=3000')
    counts = {r['name']: 0 for r in links}; offset = 0
    while True:
        page = api.call('GET', base + '/resources/relation?limit=500&offset=' + str(offset))
        for e in page['items']:
            if e['data'].get('generation') == generation and e['name'] in counts: counts[e['name']] += 1
        offset += len(page['items'])
        if offset >= page['total']: break
        if not page['items']: raise ValueError('relation pagination stalled')
    for label, names_ in [('citations', ('CITATION_PAPER', 'CITATION_REFERENCE')), ('share_citations', ('SHARE_PAPER', 'SHARE_REFERENCE'))]:
        if tables.get(label) and any(counts.get(n) != len(tables[label]) for n in names_):
            raise ValueError('citation endpoint link counts differ from source: ' + label)
    for label, expected in [('mentions', ('MENTION_CONCEPT', 'MENTION_DOCUMENT', 'MENTION_PAPER')),
                            ('extracted_claims', ('EXTRACTED_CLAIM_DOCUMENT',))]:
        if any(counts.get(n, 0) != len(tables.get(label, [])) for n in expected):
            raise ValueError('concept endpoint link counts differ from source: ' + label)
    if counts.get('DOCUMENT_PAPER', 0) != len(tables.get('documents', [])):
        raise ValueError('document paper link counts differ from source')
    if counts.get('FILE_OF', 0) != len(tables.get('files', [])): raise ValueError('file paper link counts differ from source')
    entry.pop('build', None); entry['build_status'] = 'succeeded'
    entry['counts'] = {k: len(v) for k, v in tables.items()}; checkpoint()
    return dict(utc=core.now(), workspace=entry['workspace'], build_status='succeeded', generation=generation,
                counts=entry['counts'], object_counts=object_counts, link_counts=counts, graph_truncated=graph['truncated'], all_links_positive=all(counts.values()),
                citations_sha256=hashlib.sha256(source.encode()).hexdigest() if source else None)

def index(api, verification):
    overview = verification.get('overview', {})
    text = '# ResearchLab Foundry\n\nSelect the workspace, then open [graph](' + api.base + '/ontology/explore), [table](' + api.base + '/ontology/explore?view=table), or [object types](' + api.base + '/ontology).\n\n'
    text += 'Overview: ' + overview.get('build_status', 'pending') + '. ' + json.dumps(overview.get('counts', {})) + '\n\n| Overview link | Count |\n|---|---:|\n'
    text += ''.join(f'| {k} | {v} |\n' for k, v in overview.get('link_counts', {}).items())
    text += '\n| Paper workspace | Build | Files | Claims | Relations | Link counts |\n|---|---|---:|---:|---:|---|\n'
    for k, v in verification.items():
        if k == 'overview': continue
        c = v.get('counts', {})
        text += f"| {k} | {v.get('build_status')} | {c.get('files', '')} | {c.get('claims', '')} | {c.get('relations', '')} | {json.dumps(v.get('link_counts', {}))} |\n"
    text += '\nCounts enumerate all current-generation relation resources. Graph sampling uses 20 seed records, so the displayed graph can omit disconnected objects. Empty source tables have no object type. Venue joins use exact names; per-paper venue Nodes preserve registry names. Cached extracted relations remain source statements, not independently verified claims.\n\nAPI limits: ingest 10,000 rows; resource pages 500; graph 3,000 nodes. Files are capped at 2,000 rows, with omitted files counted by directory. Retired owned chunks are excluded and retained. HTTP 409 from stale onboarding metadata was resolved by independent definition import. The old polymarket-ofv build had already succeeded, so cancellation was unnecessary. See verification.json for timestamps and generations; upload_log.jsonl records API responses.\n'
    text += '\n## Citation graph\n\nReference includes all bibliography entries; Citation includes only cited entries. Count is the source citation count. SharedReference joins Paper through ShareCitation.\n\n| Workspace | Reference | Citation | SharedReference | ShareCitation |\n|---|---:|---:|---:|---:|\n'
    for k, v in verification.items():
        c = v.get('counts', {})
        text += f"| {k} | {c.get('references', 0)} | {c.get('citations', 0)} | {c.get('shared_references', 0)} | {c.get('share_citations', 0)} |\n"
    text += '\n## Documents\n\nSections are capped at 6,000 per paper; manuscripts and reviews precede decisions/results and other notes. Identical content is deduplicated by SHA256. Files use the same roots, capped at 2,000 rows including directory summaries.\n\n| Paper | Sections before → after | Files before → after | Duplicates | Sections cut |\n|---|---:|---:|---:|---:|\n'
    for k, v in verification.items():
        if 'collection' not in v: continue
        c, n = v['collection'], v['counts']; b = c['before']
        text += f"| {k} | {b.get('documents', 0)} → {n.get('documents', 0)} | {b.get('files', 0)} → {n.get('files', 0)} | {c['duplicates']} | {c['sections_cut']} |\n"
    (api.out / 'README.md').write_text(text)

def helix_citations(a):
    out = Path(getattr(a, 'root', core.ROOT)) / 'helix'
    api = API(getattr(a, 'url', 'http://localhost:3001'), out)
    state = json.loads(read(out / 'state.json')); verification = json.loads(read(out / 'verification.json'))
    data = json.loads(read(out.parent / 'portfolio/citations.json'))
    keys = ['overview'] + [p['id'] for p in data['papers'] if p['refs']]
    jobs = []
    for key in keys:
        entry = state[key]
        if entry['workspace'] == PROTECTED: raise ValueError('protected workspace')
        tables = {k: [json.loads(s) for s in read(out / key / (k + '.jsonl')).splitlines()] for k in entry['counts']}
        for k, rows in tables.items():
            if len(rows) != entry['counts'][k] or (rows and hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest() != entry['datasets'][k]['hash']):
                raise ValueError('cached dataset differs from published dataset: ' + key + '/' + k)
        proposal = json.loads(read(out / key / 'proposal.json'))
        links = [tuple(r[k] for k in ('name', 'fromType', 'toType', 'fromColumn', 'toColumn')) for r in proposal['data']['bundle']['relationships']]
        jobs.append((key, entry, tables, links))
    save(out / 'citations_previous.json', dict(state=state, verification=verification))
    for key, entry, tables, links in jobs:
        result = publish(api, entry, tables, links, lambda: save(out / 'state.json', state))
        verification[key] = result; save(out / 'verification.json', verification); index(api, verification)
        print(key, json.dumps(result, ensure_ascii=False), flush=True)
    return state

def helix_concepts(a):
    out = Path(getattr(a, 'root', core.ROOT)) / 'helix'
    state = json.loads(read(out / 'state.json'))
    verification = json.loads(read(out / 'verification.json') or '{}')
    summaries = json.loads(read(out.parent / 'portfolio/concepts.json') or '{}').get('papers', {})
    api = API(getattr(a, 'url', 'http://localhost:3001'), out)
    selected = getattr(a, 'paper', None)
    jobs = []
    for folder in sorted(out.iterdir()):
        key = folder.name
        if key == 'overview' or (selected and key != selected): continue
        if not all((folder / f).is_file() for f in ('concepts.jsonl', 'mentions.jsonl')): continue
        entry = state[key]
        if entry['workspace'] == PROTECTED: raise ValueError('protected workspace')
        tables = {k: [json.loads(s) for s in read(folder / (k + '.jsonl')).splitlines() if s.strip()]
                  for k in dict.fromkeys([*entry['counts'], 'concepts', 'mentions', 'documents'])}
        for k, rows in tables.items():
            if k in {'concepts', 'mentions', 'extracted_claims'}: continue
            if len(rows) != entry['counts'].get(k, 0) or (rows and hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest() != entry['datasets'][k]['hash']):
                print(f'{key}/{k}: local table changed since last publish; republishing it with the concepts')  # all tables here are module-owned
        summary = summaries.get(key, {})
        if summary.get('documents_sha256') and summary['documents_sha256'] != hashlib.sha256((folder / 'documents.jsonl').read_bytes()).hexdigest():
            raise ValueError('concepts were extracted from different documents: ' + key)
        if 'claims' in summary:
            tables['extracted_claims'] = [dict(r, claim_id=hashlib.sha256(json.dumps([key, r], sort_keys=True).encode()).hexdigest()) for r in summary['claims']]
        ids = {}
        for label, pk in [('concepts', 'concept_id'), ('mentions', 'mention_id'), ('documents', 'doc_id'),
                          ('paper', 'id'), ('extracted_claims', 'claim_id')]:
            rows = tables.get(label, [])
            ids[label] = {r[pk] for r in rows}
            if len(ids[label]) != len(rows) or any(not v for v in ids[label]): raise ValueError('duplicate or empty key: ' + label)
        if ids['paper'] != {key}: raise ValueError('paper workspace mismatch')
        for row in tables['mentions']:
            if any(row[column] not in ids[label] for column, label in [('concept_id', 'concepts'), ('doc_id', 'documents'), ('paper_id', 'paper')]):
                raise ValueError('mention endpoint missing')
        if any(r['doc_id'] not in ids['documents'] for r in tables.get('extracted_claims', [])):
            raise ValueError('extracted claim document missing')
        proposal = json.loads(read(folder / 'proposal.json'))
        links = [tuple(r[k] for k in ('name', 'fromType', 'toType', 'fromColumn', 'toColumn')) for r in proposal['data']['bundle']['relationships']]
        extra = [('MENTION_CONCEPT', 'Mention', 'Concept', 'concept_id', 'concept_id'),
                 ('MENTION_DOCUMENT', 'Mention', 'Document', 'doc_id', 'doc_id'),
                 ('MENTION_PAPER', 'Mention', 'Paper', 'paper_id', 'id'),
                 ('EXTRACTED_CLAIM_DOCUMENT', 'ExtractedClaim', 'Document', 'doc_id', 'doc_id')]
        links = [l for l in links if l[0] not in {r[0] for r in extra}] + extra
        jobs.append((key, entry, tables, links))
    if selected and not jobs: raise ValueError('concept files missing: ' + selected)
    for key, entry, tables, links in jobs:
        if 'extracted_claims' in tables: lines(out / key / 'extracted_claims.jsonl', tables['extracted_claims'])
        try:
            verification[key] = publish(api, entry, tables, links, lambda: save(out / 'state.json', state), refresh_citations=False)
        except Exception as error:
            verification[key] = dict(utc=core.now(), workspace=entry['workspace'], build_status='failed', error=str(error))
            save(out / 'verification.json', verification)
            raise
        save(out / 'verification.json', verification); index(api, verification)
        print(key, json.dumps(verification[key], ensure_ascii=False), flush=True)
    return state

def helix_sync(a):
    if getattr(a, 'concepts', False): return helix_concepts(a)
    if getattr(a, 'documents', False): return helix_documents(a)
    root = Path(getattr(a, 'root', core.ROOT)); out = root / 'helix'; out.mkdir(exist_ok=True)
    api = API(getattr(a, 'url', 'http://localhost:3001'), out)
    statefile = out / 'state.json'; state = json.loads(read(statefile) or '{}')
    verification = json.loads(read(out / 'verification.json') or '{}')
    portfolio = json.loads(read(root / 'portfolio/portfolio.json')); nodes = portfolio['nodes']
    papers = json.loads(read(root / 'papers/registry.json'))['papers']
    venues = json.loads(read(root / 'papers/venues.json') or '{"venues":[]}')['venues']
    workspaces = api.call('GET', '/me')['workspaces']
    def checkpoint(): save(statefile, state)
    def workspace(key, name):
        entry = state.setdefault(key, {'datasets': {}})
        w = next((w for w in workspaces if w['id'] == entry.get('workspace') or w['name'] == name), None)
        if w is None:
            w = api.call('POST', '/workspaces', {'name': name}); workspaces.append(w)
        if w['id'] == PROTECTED: raise ValueError('protected workspace')
        if entry.get('workspace', w['id']) != w['id']: raise ValueError('workspace changed')
        entry.update(workspace=w['id'], name=w['name'], key=key); checkpoint()
        return entry
    def sync(key, name, tables, links):
        folder = out / key; folder.mkdir(exist_ok=True)
        for label, rows in tables.items(): lines(folder / (label + '.jsonl'), rows)
        entry = workspace(key, name)
        verification[key] = dict(utc=core.now(), build_status='pending'); save(out / 'verification.json', verification)
        try: verification[key] = publish(api, entry, tables, links, checkpoint)
        except Exception as e: verification[key] = dict(utc=core.now(), build_status='failed', error=str(e))
        save(out / 'verification.json', verification)
        index(api, verification)
        print(key, json.dumps(verification[key], ensure_ascii=False), flush=True)
    paper_rows = [{k: p.get(k, '') for k in ('id', 'title', 'stage', 'venue', 'result', 'path')} for p in papers]
    runs = []
    for f in sorted((root / 'runs').glob('*/run.json')):
        c = json.loads(read(f)); exp = c.get('exp_dir', '')
        matches = [n for n in nodes if n.get('path') and exp.startswith(n['path'].rstrip('/') + '/')]
        target = max(matches, key=lambda n: len(n['path']))['id'] if matches else c.get('program', '')
        runs.append(dict(id=f.parent.name, node_id=target if target in {n['id'] for n in nodes} else '', program=c.get('program', ''), exp_dir=exp, result=read(f.parent / 'RESULT.md')))
    sync('overview', 'ResearchLab 개요', dict(nodes=nodes, papers=paper_rows, runs=runs, edges=relation_rows(portfolio['edges']), venues=venues),
         [('SOURCE', 'Relation', 'Node', 'from_id', 'id'), ('TARGET', 'Relation', 'Node', 'to_id', 'id'), ('IS_NODE', 'Paper', 'Node', 'id', 'id'), ('RUN_OF', 'Run', 'Node', 'node_id', 'id'), ('SUBMITTED_TO', 'Paper', 'Venue', 'venue', 'name')])
    if getattr(a, 'overview_only', False):
        if verification['overview']['build_status'] != 'succeeded': raise RuntimeError(verification['overview'].get('error'))
        return state
    target_nodes = nodes + [dict(id=v, title=v, type='venue', source='papers/registry.json') for v in sorted({p['venue'] for p in papers if p.get('venue')}) if v not in {n['id'] for n in nodes}]
    known = {n['id']: n.get('title', n['id']) for n in target_nodes + papers}
    memories = sorted(Path(getattr(a, 'memory_dir', Path.home() / '.agents/memory/projects')).glob('*.md'))
    for p in sorted(papers, key=lambda p: p['id'] != 'polymarket-ofv'):
        key = p['id']
        if not re.fullmatch(r'[\w.-]+', key) or key in {'.', '..', 'overview'}: raise ValueError('unsafe paper id')
        folder = out / key; folder.mkdir(exist_ok=True)
        tables, sources = prepare(p, memories); tables['files'] = capped(tables['files'])
        for label in ('files', 'claims', 'history'):
            tables[label] = [dict(r, paper_id=key, id=key + ':' + label + ':' + str(i)) for i, r in enumerate(tables[label])]
        cached = [json.loads(line) for line in read(folder / 'relations.jsonl').splitlines() if line]
        if getattr(a, 'extract', False): cached, dropped = relations(p, sources, known, folder, getattr(a, 'provider', GLM)); lines(folder / 'relations_dropped.jsonl', dropped)
        edges = relation_rows(cached) if cached and 'subject' in cached[0] else cached
        if not getattr(a, 'extract', False): edges += relation_rows(json.loads(read(folder / 'relations_cache.json') or '{}').get('edges', []))
        edges += relation_rows([e for e in portfolio['edges'] if e['from'] == key])
        edges = list({e['relation_id']: e for e in edges if e['from_id'] == key and e['to_id'] in known}.values())
        tables['relations'] = edges
        tables['nodes'] = [n for n in target_nodes if n['id'] in {e['to_id'] for e in edges}]
        sync(key, '논문 · ' + p['title'][:70], tables,
             [('FILE_OF', 'File', 'Paper', 'paper_id', 'id'), ('CLAIM_OF', 'Claim', 'Paper', 'paper_id', 'id'), ('HISTORY_OF', 'HistoryEvent', 'Paper', 'paper_id', 'id'), ('SOURCE', 'Relation', 'Paper', 'from_id', 'id'), ('TARGET', 'Relation', 'Node', 'to_id', 'id')])
    failed = [k for k in ['overview'] + [p['id'] for p in papers] if verification[k]['build_status'] != 'succeeded']
    if failed: raise RuntimeError('Helix sync failed: ' + ', '.join(failed))
    return state
def selftest():
    from types import SimpleNamespace
    from unittest.mock import patch
    with tempfile.TemporaryDirectory() as t:
        root = Path(t); (root / 'doc').mkdir()
        (root / 'doc/README.md').write_text('Uses tool T. Submitted to V.')
        save(root / 'papers/registry.json', {'papers': [dict(id='p', title='Paper', venue='V', path=str(root / 'doc'))]})
        save(root / 'portfolio/portfolio.json', {'nodes': [dict(id='p'), dict(id='T')], 'edges': [dict(from_id='p', to_id='T', rel='uses_tool')]})
        j = json.loads(read(root / 'portfolio/portfolio.json')); j['edges'] = [{'from': 'p', 'to': 'T', 'rel': 'uses_tool'}]; save(root / 'portfolio/portfolio.json', j)
        workspaces, datasets, proposals, generations, requests = [], {}, {}, {}, []
        def call(api, method, path, body=None):
            assert PROTECTED not in path; requests.append((method, path, body))
            if path == '/me': return {'workspaces': workspaces}
            if path == '/workspaces':
                w = dict(id='w' + str(len(workspaces)), name=body['name']); workspaces.append(w); return w
            w = path.split('/')[2]
            if path.endswith('/onboarding'): return {'excludedIds': [], 'complete': w != 'w0'}
            if path.endswith('/ingest'):
                did = body.get('datasetId', 'd' + str(len(datasets))); datasets[did] = body['rows']; return {'id': did}
            if path.endswith('/overview'):
                return dict(workspace={'data': {'activeGeneration': generations.get(w)}}, proposal=[proposals[w]] if w in proposals else [], objectType=[dict(name=t['name'], data=dict(datasetId=t['datasetId'], generation=generations[w], objectCount=len(datasets[t['datasetId']]))) for t in proposals[w]['data']['bundle']['ontology']] if w in generations else [])
            if path.endswith('/definitions/import'):
                proposals[w] = dict(id='p' + w, data=dict(bundle=body['proposals'][0], validation={'valid': True}, hash='h')); return {'items': [proposals[w]]}
            if path.endswith('/publish'):
                generations[w] = 'g' + w; proposals[w]['data'].update(status='published', publishedGeneration=generations[w]); return proposals[w]
            if '/graph?' in path or '/resources/relation?' in path:
                bundle = proposals[w]['data']['bundle']; types = {v['name']: datasets[v['datasetId']] for v in bundle['ontology']}
                edges = [dict(name=r['name']) for r in bundle['relationships'] for f in types[r['fromType']] for to in types[r['toType']] if f.get(r['fromColumn']) == to.get(r['toColumn'])]
                if '/resources/relation?' in path: return dict(items=[dict(e, data={'generation': generations[w]}) for e in edges], total=len(edges))
                return dict(edges=edges, truncated=False)
            raise AssertionError(path)
        original = core.invoke
        def fake(provider, instruction, cwd, *args, **kwargs):
            edge = dict(subject='p', relation='uses_tool', object='T', quote='Uses tool T.', source=str(root / 'doc/README.md'))
            (Path(cwd) / 'fake.py').write_text('import sys\nassert "TASK: relations" in sys.stdin.read()\nprint(' + repr(json.dumps([edge, edge | {'relation': 'submitted_to', 'object': 'V', 'quote': 'Submitted to V.'}, edge | {'quote': 'invented'}, edge | {'object': 'unknown'}])) + ')\n')
            return original(provider, instruction, cwd, *args, **kwargs)
        a = SimpleNamespace(root=root, provider='cmd:python3 fake.py', memory_dir=root / 'none', extract=True)
        with patch.object(API, 'call', call), patch.object(core, 'invoke', fake):
            helix_sync(a); before = len([r for r in requests if r[0] == 'POST']); a.extract = False; helix_sync(a)
            assert before == len([r for r in requests if r[0] == 'POST'])
            v = json.loads(read(root / 'helix/verification.json'))
            assert v['overview']['link_counts'] == {'SOURCE': 1, 'TARGET': 1, 'IS_NODE': 1}
            assert v['p']['link_counts']['FILE_OF'] == 1 and v['p']['link_counts']['SOURCE'] == 3
            assert len(read(root / 'helix/p/relations_dropped.jsonl').splitlines()) == 2
            ref = dict(key='a', title='Café', authors='Author', year=2024, venue='V', group='other', count=3, cited=True)
            data = dict(papers=[dict(id='p', refs=[ref, ref | dict(key='b', title='Other', count=0, cited=False)])],
                        shared=[dict(norm_title='cafe', title='Café', year=2024, papers=['p'])])
            save(root / 'portfolio/citations.json', data)
            helix_citations(a)
            v = json.loads(read(root / 'helix/verification.json'))
            assert v['p']['object_counts']['Reference'] == 2 and v['p']['object_counts']['Citation'] == 1
            assert v['p']['link_counts']['CITATION_REFERENCE'] == v['p']['link_counts']['CITATION_PAPER'] == 1
            assert v['overview']['link_counts']['SHARE_REFERENCE'] == v['overview']['link_counts']['SHARE_PAPER'] == 1
            def missing_link(api, method, path, body=None):
                result = call(api, method, path, body)
                if '/resources/relation?' in path:
                    result['items'] = [r for r in result['items'] if r['name'] != 'SHARE_REFERENCE']
                    result['total'] = len(result['items'])
                return result
            with patch.object(API, 'call', missing_link):
                try: helix_citations(a)
                except ValueError: pass
                else: raise AssertionError('missing citation links accepted')
            cached = root / 'helix/p/references.jsonl'; old = cached.read_text(); cached.write_text('')
            try: helix_citations(a)
            except ValueError: pass
            else: raise AssertionError('missing cached dataset accepted')
            cached.write_text(old)
            data['shared'][0]['papers'].extend(['q', 'p'])
            assert citation_tables(data, 'p')[0]['references'][0]['shared_with'] == 'q'
            assert len(citation_tables(data, 'overview')[0]['share_citations']) == 2
            data['shared'][0]['papers'] = ['p']; save(root / 'portfolio/citations.json', data)
            doc = root / 'doc'
            (doc / 'main.tex').write_text(r'\section{Main} Hello \textbf{world} $x_{i}=\alpha$. % hidden' + '\n' + r'\input{child}')
            (doc / 'child.tex').write_text(r'\subsection{Child} Child text \input{main}')
            (doc / 'RESULT.md').write_text('# Result\n' + 'x' * 4100 + '\n## More\nDone')
            (doc / 'reviews').mkdir(); (doc / 'reviews/own.md').write_text('# Own\nReview')
            (doc / 'reviews/external').mkdir(); (doc / 'reviews/external/private.md').write_text('secret')
            (doc / 'tmp').mkdir(); (doc / 'tmp/RESULT.md').write_text('secret')
            (doc / 'large.txt').write_text('x' * (2 * 1024**2 + 1))
            (doc / 'RESULT-link.md').symlink_to(doc / 'tmp/RESULT.md')
            memory = root / 'memory'; memory.mkdir()
            (memory / 'p.md').write_text('# Note\nPaper uses data.')
            (memory / 'other.md').write_text('Other project.')
            data['papers'][0].update(folder=str(doc), tex_files=[str(doc / 'main.tex')])
            save(root / 'portfolio/citations.json', data)
            a.memory_dir = memory; a.documents = True; helix_sync(a)
            rows = [json.loads(s) for s in read(root / 'helix/p/documents.jsonl').splitlines()]
            assert rows == document_rows(data['papers'][0] | {'title': 'Paper'}, [memory / 'p.md', memory / 'other.md'])
            assert len({r['path'] for r in rows}) == 6 and all(r['chars'] == len(r['text']) <= 4000 for r in rows)
            assert any(r'$x_{i}=\alpha$' in r['text'] and 'Hello world' in r['text'] for r in rows)
            assert not any('secret' in r['text'] or 'hidden' in r['text'] for r in rows)
            assert len({r['doc_id'] for r in rows}) == len(rows)
            entry = json.loads(read(root / 'helix/state.json'))['p']
            tables = {k: [json.loads(s) for s in read(root / 'helix/p' / (k + '.jsonl')).splitlines()]
                      for k in (*entry['counts'], 'documents')}
            proposal = json.loads(read(root / 'helix/p/proposal.json'))
            links = [tuple(r[k] for k in ('name', 'fromType', 'toType', 'fromColumn', 'toColumn'))
                     for r in proposal['data']['bundle']['relationships']]
            v = publish(API('http://invalid', root / 'helix'), entry, tables, links, lambda: None)
            assert v['object_counts']['Document'] == v['link_counts']['DOCUMENT_PAPER'] == len(rows)
            state = json.loads(read(root / 'helix/state.json')); state['p'] = entry
            save(root / 'helix/state.json', state)
            a.documents = False; a.concepts = True; a.paper = 'p'
            folder = root / 'helix/p'
            lines(folder / 'concepts.jsonl', [dict(concept_id='method:gmv', type='method', name='GMV', aliases=['GMV'], mention_count=1)])
            mention = dict(mention_id='m1', concept_id='method:gmv', paper_id='p', doc_id=rows[0]['doc_id'], quote='Hello')
            lines(folder / 'mentions.jsonl', [mention])
            save(root / 'portfolio/concepts.json', dict(papers={'p': dict(claims=[dict(doc_id=rows[0]['doc_id'], text='12 units', number='12', unit='units', quote='12 units')])}))
            old_counts = dict(entry['counts'])
            overview = dict(json.loads(read(root / 'helix/verification.json'))['overview'])
            helix_sync(a)
            v = json.loads(read(root / 'helix/verification.json'))
            assert v['overview'] == overview
            assert all(v['p']['counts'][k] == n for k, n in old_counts.items())
            assert all(v['p']['link_counts'][k] == 1 for k in ('MENTION_CONCEPT', 'MENTION_DOCUMENT', 'MENTION_PAPER', 'EXTRACTED_CLAIM_DOCUMENT'))
            assert v['p']['object_counts']['Concept'] == v['p']['object_counts']['Mention'] == v['p']['object_counts']['ExtractedClaim'] == 1
            helix_sync(a); a.concepts = False
            (root / 'doc/README.md').write_text('No tool.'); a.extract = True; helix_sync(a)
            assert any(r[2] and 'datasetId' in r[2] for r in requests)
        repo = root / 'Papers' / 'shared'; own = repo / 'papers/alpha-study'
        other = repo / 'papers/beta-study'; own.mkdir(parents=True); other.mkdir(); (repo / '.git').mkdir()
        p1 = dict(id='rq1', title='Alpha Study', path=str(own))
        p2 = dict(id='rq2', title='Beta Study', path=str(other))
        review = root / 'Reviews' / 'internal'
        contents = {'papers/alpha-study/paper/main.tex': r'\section{Main} English',
                    'papers/alpha-study/paper/korean/main_ko.tex': r'\section{Main} Korean',
                    'papers/alpha-study/talk/deck.tex': r'\section{Talk} Talk',
                    'records/reviews/rq1/deep/a/b/c/review.md': '# Review\nAccept',
                    'records/decisions/alpha-study.md': '# Decision\nKeep',
                    'papers/beta-study/private.md': 'Wrong paper', 'records/rq2.md': 'Wrong review',
                    'papers/alpha-study/build/made.md': 'Generated',
                    'papers/alpha-study/raw.md': 'prefix\x00binary',
                    'papers/alpha-study/note.txt': 'A note'}
        for name, body in contents.items():
            f = repo / name; f.parent.mkdir(parents=True, exist_ok=True); f.write_text(body)
        for name in ('rq1/nested/review.md', 'rq1/external/private.md', 'rq1/archive/private.md', 'rq2/review.md', 'archive/rq1/review.md'):
            f = review / name; f.parent.mkdir(parents=True, exist_ok=True); f.write_text('# External\n' + name)
        (own / 'copy.tex').write_bytes((own / 'paper/main.tex').read_bytes())
        files = list(paper_files(p1, [p1, p2], review)); stats = {}
        docs = document_rows(p1, [], files, stats)
        assert stats['duplicates'] == 1 and sum(r['duplicates'] for r in docs) == 1
        assert {'manuscript', 'manuscript_ko', 'talk', 'review', 'decision', 'note'} <= {r['doc_kind'] for r in docs}
        assert all(not any(w in r['text'] for w in ('Wrong', 'Generated', 'binary', 'private', 'archive')) for r in docs)
        assert any('/Reviews/internal/' in r['path'] for r in docs)
        (own / 'many.md').write_text('z' * 4000 * 6001)
        docs = document_rows(p1, [], list(paper_files(p1, [p1, p2], review)), stats)
        assert len(docs) == 6000 and stats['sections_cut'] > 0 and docs[0]['doc_kind'] in ('manuscript', 'manuscript_ko', 'review')  # equal priority: path order decides (symlinked tmp on macOS)
        assert any(f == (other / 'private.md').resolve() for f, _ in paper_files(p1, [p1], review))
        fs = [dict(relpath='d/' + str(i), role='data') for i in range(3000)] + [dict(relpath='paper.tex', role='manuscript')]
        cap = capped(fs); assert len(cap) <= 2000 and cap[0]['relpath'] == 'paper.tex'
        assert sum(r.get('count', 1) for r in cap) == len(fs)
        try: API('http://invalid', root).call('POST', '/workspaces/' + PROTECTED, {})
        except ValueError: pass
        else: raise AssertionError('protected workspace accepted')
    print('helix selftest: ok (fake provider/API, documents/TeX/math, citation/document/concept joins, missing-link/cache gates, cap, quote/ID gates, idempotency, protected workspace)')
