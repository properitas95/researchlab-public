"""Target-venue profile: recent papers from OpenAlex (no key), counted stats, one model summary, novelty overlap."""
import datetime as dt
import functools
import html
import json
import math
import os
import re
import statistics
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path

from lab import core, roles

API = "https://api.openalex.org"
STOP = set("the and for with from that this are was were using use used based via into over under our their its can not but "
           "has have between than also new study paper approach method methods results analysis show shows".split())
KEYS = ("form", "acceptance_bar", "reviewers", "common_methods", "saturated_topics")
EMPIRICAL = re.compile(r"empirical|experiment|dataset|evaluat|benchmark|real-world", re.I)
CODE = re.compile(r"\bcode\b|github|open[- ]source|implementation|reproduc", re.I)
METHODS = ("regression", "neural", "transformer", "bayesian", "simulation", "survey", "case study", "randomized",
           "reinforcement", "graph", "causal", "optimization", "theorem", "language model", "clustering", "panel data")


def slug(name):
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def vdir(name):
    d = core.ROOT / "venues" / slug(name)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _get(url, **params):
    if os.environ.get("LAB_MAILTO"):
        params["mailto"] = os.environ["LAB_MAILTO"]
    url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params, safe=":,*") if params else ""
    time.sleep(0.1)  # <= 10 requests/s
    req = urllib.request.Request(url, headers={"User-Agent": "ResearchLab/1.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read().decode("utf-8", "replace")


def _abstract(inv):
    if not inv:
        return ""
    pos = sorted((i, w) for w, ix in inv.items() for i in ix)
    return " ".join(w for _, w in pos)


def _work(w):
    b = w.get("biblio") or {}
    try:
        pages = int(b["last_page"]) - int(b["first_page"]) + 1
    except (KeyError, TypeError, ValueError):
        pages = None
    return dict(id=w.get("id"), title=w.get("title") or "", year=w.get("publication_year"),
                abstract=_abstract(w.get("abstract_inverted_index")),
                concepts=[c["display_name"] for c in w.get("concepts") or []][:8],
                topics=[t["display_name"] for t in w.get("topics") or []][:5],
                cited_by_count=w.get("cited_by_count", 0), type=w.get("type"), pages=pages if pages and pages > 0 else None)


def _source(source_id, issn, name):
    if source_id:
        return source_id.rstrip("/").rsplit("/", 1)[-1]
    if issn:
        return json.loads(_get(f"{API}/sources/issn:{issn}"))["id"].rsplit("/", 1)[-1]
    res = json.loads(_get(f"{API}/sources", search=name))["results"]
    if not res:
        raise ValueError(f"no OpenAlex source matches {name!r}; pass --source or --issn")
    return res[0]["id"].rsplit("/", 1)[-1]


def _strip(page):
    page = re.sub(r"(?is)<(script|style|noscript)\b.*?</\1>", " ", page)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"(?s)<[^>]+>", " ", page))).strip()


def collect(name, source_id=None, issn=None, years=3, site_urls=()):
    d = vdir(name)
    works, state = d / "works.jsonl", d / "collect.json"
    st = json.loads(state.read_text(encoding="utf-8", errors="replace")) if state.exists() else {}
    if not (works.exists() and (not st or st.get("done"))):  # saved list is complete: stay offline
        if "source" not in st:
            since = (dt.date.today() - dt.timedelta(days=365 * years)).isoformat()
            st = dict(source=_source(source_id, issn, name), since=since, cursor="*", done=False)
        while not st["done"]:
            page = json.loads(_get(f"{API}/works", filter=f"primary_location.source.id:{st['source']},from_publication_date:{st['since']}",
                                   **{"per-page": 200, "cursor": st["cursor"]}))
            with works.open("a", encoding="utf-8", errors="replace") as f:
                f.writelines(json.dumps(_work(w), ensure_ascii=False) + "\n" for w in page["results"])
            st["cursor"] = page["meta"].get("next_cursor")
            st["done"] = not st["cursor"] or not page["results"]
            state.write_text(json.dumps(st), encoding="utf-8", errors="replace")
    if site_urls:
        text = "".join(f"## {u}\n{_strip(_get(u))[:20000]}\n\n" for u in site_urls)
        (d / "site.txt").write_text(text, encoding="utf-8", errors="replace")
    return load(name)


def load(name):
    p = vdir(name) / "works.jsonl"
    rows = {}
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines() if p.exists() else []:
        w = json.loads(line)
        rows[w["id"]] = w  # a resumed page may repeat rows
    return list(rows.values())


def _text(w):
    return f"{w['title']} {w['abstract']}"


def stats(ws):
    texts = [_text(w).lower() for w in ws]
    n = max(len(ws), 1)
    pages = [w["pages"] for w in ws if w.get("pages")]
    return dict(papers=len(ws), per_year=dict(sorted(Counter(w["year"] for w in ws).items())),
                top_topics=Counter(t for w in ws for t in w["topics"]).most_common(10),
                top_concepts=Counter(c for w in ws for c in w["concepts"]).most_common(10),
                median_pages=statistics.median(pages) if pages else None,
                share_empirical=round(sum(bool(EMPIRICAL.search(t)) for t in texts) / n, 2),
                share_code=round(sum(bool(CODE.search(t)) for t in texts) / n, 2),
                methods={m: c for m in METHODS if (c := sum(m in t for t in texts))})


def profile(name, provider=None, run=None):
    d = vdir(name)
    ws = load(name)
    if not ws:
        raise ValueError(f"no works for {name}: run `lab.py venue` first")
    site = (d / "site.txt").read_text(encoding="utf-8", errors="replace")[:12000] if (d / "site.txt").exists() else ""
    sample = "\n".join(f"- {w['title']} ({w['year']}, {w['cited_by_count']} cites): {w['abstract'][:300]}"
                       for w in sorted(ws, key=lambda w: -w["cited_by_count"])[:40])
    st = stats(ws)
    with tempfile.TemporaryDirectory(prefix="venue-") as t:  # stage "review" = reviewer role (harness.json has no venue stage)
        reply = roles.call("venue", core.prompt("venue_profile", venue=name, stats=json.dumps(st, ensure_ascii=False),
                                                  sample=sample, site=site or "(none)"), cwd=t, run=run, write=False, provider=provider)
    j = _valid(core.last_json(reply))
    j["stats"] = st
    (d / "profile.json").write_text(json.dumps(j, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", errors="replace")
    (d / "PROFILE.md").write_text(_md(name, j), encoding="utf-8", errors="replace")
    return j


def _valid(j):
    s = lambda x: isinstance(x, str) and x.strip()
    ok = isinstance(j, dict) and all(k in j for k in KEYS)
    ok = ok and isinstance(j["form"], dict) and all(j["form"].get(k) for k in ("structure", "length", "contribution_types"))
    ok = ok and isinstance(j["acceptance_bar"], dict) and all(s(j["acceptance_bar"].get(k)) for k in ("reject", "borderline", "accept"))
    ok = ok and isinstance(j["reviewers"], list) and len(j["reviewers"]) == 3
    ok = ok and all(isinstance(r, dict) and all(s(r.get(k)) for k in ("name", "expertise", "checks", "harshness")) for r in j["reviewers"])
    ok = ok and all(isinstance(j[k], list) and j[k] for k in ("common_methods", "saturated_topics"))
    if not ok:
        raise ValueError("venue profile JSON failed validation")
    return j


def _md(name, j):
    f, b = j["form"], j["acceptance_bar"]
    return "\n".join([f"# {name}", "", "## Form", f"- structure: {f['structure']}", f"- length: {f['length']}",
                      f"- contribution types: {f['contribution_types']}", "", "## Acceptance bar",
                      *(f"- {k}: {b[k]}" for k in ("reject", "borderline", "accept")), "", "## Reviewers",
                      *(f"- {r['name']}: {persona(r)}" for r in j["reviewers"]), "",
                      "## Common methods", *(f"- {m}" for m in j["common_methods"]), "",
                      "## Saturated topics", *(f"- {m}" for m in j["saturated_topics"]), ""])


def persona(r):
    return f"{r['name']}; expertise: {r['expertise']}; checks: {r['checks']}; harshness: {r['harshness']}"


def venue_profile_of(name):
    p = core.ROOT / "venues" / slug(name) / "profile.json"
    return json.loads(p.read_text(encoding="utf-8", errors="replace")) if p.exists() else None


def _toks(text):
    w = [x[:-1] if x.endswith("s") and len(x) > 4 else x for x in re.findall(r"[a-z0-9]{3,}", text.lower())]
    w = [x for x in w if x not in STOP]
    return w + [f"{a} {b}" for a, b in zip(w, w[1:])]


@functools.lru_cache(maxsize=8)
def _index(path, mtime):
    ws = [json.loads(l) for l in Path(path).read_text(encoding="utf-8", errors="replace").splitlines()]
    docs = [Counter(_toks(_text(w))) for w in ws]
    df = Counter(t for c in docs for t in c)
    idf = lambda t: math.log((1 + len(docs)) / (1 + df.get(t, 0))) + 1
    vecs = [{t: n * idf(t) for t, n in c.items()} for c in docs]
    return ws, [set(c) for c in docs], vecs, [math.sqrt(sum(v * v for v in x.values())) or 1 for x in vecs], idf


def overlap(topic_text, slug_, threshold=0.35):
    p = core.ROOT / "venues" / slug_ / "works.jsonl"
    ws, sets, vecs, norms, idf = _index(str(p), p.stat().st_mtime_ns)
    q = Counter(_toks(topic_text))
    qs, qv = set(q), {t: n * idf(t) for t, n in q.items()}
    qn = math.sqrt(sum(v * v for v in qv.values())) or 1
    scores = []
    for w, s, v, n in zip(ws, sets, vecs, norms):
        jac = len(qs & s) / len(qs | s) if qs | s else 0
        cos = sum(x * v.get(t, 0) for t, x in qv.items()) / (qn * n)
        scores.append((max(jac, cos), w["title"]))
    scores.sort(reverse=True)
    top = scores[0][0] if scores else 0.0
    return dict(max=round(top, 3), too_close=top >= threshold, adjacent=0.12 <= top < threshold,
                nearest=[dict(title=t, score=round(s, 3)) for s, t in scores[:3]])


def venue(a):
    ws = collect(a.name, getattr(a, "source", None), getattr(a, "issn", None), getattr(a, "years", 3) or 3, tuple(getattr(a, "site", None) or ()))
    print(f"{slug(a.name)}: {len(ws)} works -> {vdir(a.name) / 'works.jsonl'}")


def venue_profile(a):
    profile(a.name, getattr(a, "provider", None))
    print(f"wrote {vdir(a.name) / 'PROFILE.md'}")


def selftest():
    import http.server
    import threading
    from unittest.mock import patch
    global API
    inv = lambda s: {w: [i] for i, w in enumerate(s.split())}
    pages = {"*": dict(results=[dict(id="W1", title="Shrinkage covariance estimation for portfolio variance", publication_year=2025,
                                     abstract_inverted_index=inv("We study shrinkage covariance estimation for minimum variance portfolios"),
                                     concepts=[dict(display_name="Finance")], topics=[dict(display_name="Portfolio")], cited_by_count=9,
                                     type="article", biblio=dict(first_page="1", last_page="12")),
                       dict(id="W2", title="Graph neural networks for molecules", publication_year=2024, abstract_inverted_index=None,
                            cited_by_count=3, type="article")], meta=dict(next_cursor="c2")),
             "c2": dict(results=[dict(id="W3", title="Reinforcement learning for robot control", publication_year=2024,
                                      abstract_inverted_index=inv("code available on github"), cited_by_count=1, type="article")],
                        meta=dict(next_cursor=None))}
    hits = []

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            u = urllib.parse.urlparse(self.path)
            q = urllib.parse.parse_qs(u.query)
            hits.append(u.path)
            if u.path == "/sources":
                body = json.dumps(dict(results=[dict(id="https://openalex.org/S42")]))
            elif u.path == "/works":
                assert "S42" in q["filter"][0] and "from_publication_date" in q["filter"][0]
                body = json.dumps(pages[q["cursor"][0]])
            else:
                body = "<html><style>x{}</style><body><h1>Call for Papers</h1><p>Reviewers value <b>novelty</b> &amp; rigor</p></body></html>"
            self.send_response(200)
            self.end_headers()
            self.wfile.write(body.encode())

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_port}"
    fake_reply = ('x\n{"form":{"structure":"IMRaD","length":"10pp","contribution_types":["empirical"]},"acceptance_bar":'
                  '{"reject":"no baseline","borderline":"one dataset","accept":"3 datasets and ablation"},"reviewers":['
                  + ",".join('{"name":"R%d","expertise":"e","checks":"c","harshness":"harsh"}' % i for i in range(3))
                  + '],"common_methods":["shrinkage"],"saturated_topics":["GNN"]}')
    with tempfile.TemporaryDirectory() as t, patch.object(core, "ROOT", Path(t)), patch.object(roles, "ROOT", Path(t)), \
            patch.dict(os.environ, {"LAB_MAILTO": "a@b.c"}):
        root = Path(t)
        API = base
        ws = collect("Test Journal", site_urls=[base + "/cfp"])
        assert [w["id"] for w in ws] == ["W1", "W2", "W3"] and ws[0]["abstract"].startswith("We study shrinkage") and ws[0]["pages"] == 12
        assert "Call for Papers" in (root / "venues/test-journal/site.txt").read_text(encoding="utf-8", errors="replace") and "<" not in (root / "venues/test-journal/site.txt").read_text(encoding="utf-8", errors="replace")
        API = "http://127.0.0.1:1"  # offline: saved list is reused
        assert len(collect("Test Journal")) == 3
        API = base
        st = root / "venues/test-journal/collect.json"  # resume from the saved cursor
        st.write_text(json.dumps(dict(source="S42", since="2020-01-01", cursor="c2", done=False)), encoding="utf-8", errors="replace")
        w = root / "venues/test-journal/works.jsonl"
        w.write_text("".join(w.read_text(encoding="utf-8", errors="replace").splitlines(True)[:2]), encoding="utf-8", errors="replace")
        n = hits.count("/works")
        assert len(collect("Test Journal")) == 3 and hits.count("/works") == n + 1
        srv.shutdown()
        close = overlap("shrinkage covariance estimation portfolio variance", "test-journal")
        adj = overlap("covariance estimation for robust risk models", "test-journal")
        far = overlap("quantum chemistry on lattices", "test-journal")
        assert close["too_close"] and close["nearest"][0]["title"].startswith("Shrinkage"), close
        assert adj["adjacent"] and not adj["too_close"] and not far["adjacent"] and not far["too_close"], (adj, far)
        s = stats(load("Test Journal"))
        assert s["papers"] == 3 and s["per_year"] == {2024: 2, 2025: 1} and s["median_pages"] == 12 and s["methods"].get("graph") == 1
        (root / "fake.py").write_text(f"import sys\nsys.stdin.read()\nprint({fake_reply!r})\n")
        (root / "run.json").write_text("{}")
        j = profile("Test Journal", provider=f"cmd:python3 {root / 'fake.py'}")
        assert (root / "venues/test-journal/profile.json").exists() and "Acceptance bar" in (root / "venues/test-journal/PROFILE.md").read_text(encoding="utf-8", errors="replace")
        assert j["stats"]["papers"] == 3
        for bad in ({k: v for k, v in j.items() if k != "reviewers"}, dict(j, reviewers=j["reviewers"][:2]),
                    dict(j, acceptance_bar=dict(j["acceptance_bar"], accept=""))):
            try:
                _valid(bad)
                raise AssertionError("invalid profile accepted")
            except ValueError:
                pass
        from lab import combine, panel
        for p, rows in {"a": [("method", "shrinkage covariance", 5), ("dataset", "KOSPI200 daily", 4), ("metric", "GMV variance", 6)],
                        "b": [("method", "graph attention", 3), ("method", "shrinkage covariance estimation portfolio variance", 2)]}.items():
            (root / "helix" / p).mkdir(parents=True)
            (root / "helix" / p / "concepts.jsonl").write_text("".join(json.dumps(dict(concept_id=f"{p}{i}", type=ty, name=n, mention_count=m)) + "\n"
                                                                           for i, (ty, n, m) in enumerate(rows)))
        assert len(combine.build()) == 3
        with patch.dict(os.environ, {"LAB_VENUE": "test-journal"}):
            out = combine.build()
        assert {c["method"]: c["adjacent"] for c in out} == {"graph attention": False, "shrinkage covariance": True}, out
        c = core.DEFAULTS | {"venue": "test-journal"}
        specs = panel.specs(c)
        assert [s[2] for s in specs] == [persona(r) for r in j["reviewers"]] and specs[1][:2] == c["panel"][1][:2]
        assert "3 datasets and ablation" in panel.bar(c) and panel.bar(core.DEFAULTS) == "" and panel.specs(core.DEFAULTS) == core.DEFAULTS["panel"]
    print("venue selftest ok (paged fetch, resume, offline reuse, abstract rebuild, overlap, profile validation, combine filter, panel personas)")


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    selftest()
