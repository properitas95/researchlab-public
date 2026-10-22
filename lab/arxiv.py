"""Stdlib paper search and full text through the public arXiv API and HTML; used when `alpha` is not on PATH (or LAB_PAPERS=arxiv)."""
import os
import re
import shutil
import threading
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from html.parser import HTMLParser

UA = "ResearchLab/0.1 (research harness)"
GAP = 3.0  # seconds between requests (arXiv API etiquette); one request at a time
_LOCK = threading.Lock()
_last = [0.0]
ATOM = "{http://www.w3.org/2005/Atom}"
STOP = {"the", "and", "for", "with", "from", "that", "this", "are", "vs", "versus", "of", "in", "on", "to", "a", "an", "or", "not", "is"}
ID = r"(?:\d{4}\.\d{4,5}|[a-z-]+/\d{7})"


def use_arxiv():
    return os.environ.get("LAB_PAPERS") == "arxiv" or not shutil.which("alpha")


def source():
    return "arxiv-api" if use_arxiv() else "alpha"


def _get(url, cap=6_000_000):
    with _LOCK:
        wait = _last[0] + GAP - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read(cap)
        finally:
            _last[0] = time.monotonic()


def _entries(xml):
    out = []
    for e in ET.fromstring(xml).iter(ATOM + "entry"):
        sid = re.sub(r"v\d+$", "", e.findtext(ATOM + "id", "").rsplit("/abs/", 1)[-1].strip())
        if not re.fullmatch(ID, sid):
            continue  # the API reports errors as an entry too
        clean = lambda s: " ".join((s or "").split())
        out.append(dict(id=sid, title=clean(e.findtext(ATOM + "title")), url=f"https://arxiv.org/abs/{sid}",
                        date=clean(e.findtext(ATOM + "published"))[:10], abstract=clean(e.findtext(ATOM + "summary"))))
    return out


def search(q, n=8):
    """Result text in the shape survey._papers parses: `[ID=..] **title** (url). Published date` plus the abstract."""
    words = [w for w in re.findall(r"[\w-]+", q) if w.lower() not in STOP][:8]  # OR + relevance: an AND of 5 terms returned nothing
    if not words:
        return "ERROR: empty query"
    url = "http://export.arxiv.org/api/query?" + urllib.parse.urlencode(
        {"search_query": " OR ".join("all:" + w for w in words), "max_results": n, "sortBy": "relevance"})
    try:
        rows = _entries(_get(url))
    except Exception as e:
        return f"ERROR: arXiv search failed: {type(e).__name__}: {e}"[:300]
    return "\n\n".join(f"[ID={r['id']}] **{r['title']}** ({r['url']}). Published {r['date']}\n{r['abstract']}" for r in rows) or "No results."


class _Text(HTMLParser):
    SKIP = {"script", "style", "nav", "header", "footer", "noscript", "svg", "button", "head"}
    SKIP_CLASS = ("ltx_bibliography", "ltx_page_footer", "ltx_page_header", "ltx_role_navigation", "ltx_ref_tag", "ltx_tag_")
    VOID = {"br", "img", "meta", "link", "input", "hr", "col", "source", "wbr"}
    BLOCK = {"p", "div", "section", "article", "li", "tr", "figcaption", "table", "ul", "ol", "blockquote", "caption", "figure"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self.stack, self.skip = [], [], 0

    def handle_starttag(self, tag, attrs):
        if tag in self.VOID:
            if tag == "br" and not self.skip:
                self.out.append("\n")
            return
        a = dict(attrs)
        skip = tag in self.SKIP or any(c in (a.get("class") or "") for c in self.SKIP_CLASS)
        self.stack.append((tag, skip))
        self.skip += skip
        if self.skip:
            return
        if tag == "math" and a.get("alttext"):
            self.out.append(" " + a["alttext"] + " ")
            self.stack[-1] = (tag, True)
            self.skip += 1
        elif re.fullmatch(r"h[1-6]", tag):
            self.out.append("\n\n" + "#" * int(tag[1]) + " ")
        elif tag in self.BLOCK:
            self.out.append("\n\n")

    def handle_endtag(self, tag):
        if tag in self.VOID:
            return
        while self.stack:
            t, skip = self.stack.pop()
            self.skip -= skip
            if t == tag:
                break
        if not self.skip and (tag in self.BLOCK or re.fullmatch(r"h[1-6]", tag)):
            self.out.append("\n\n")

    def handle_data(self, data):
        if not self.skip:
            self.out.append(data)


def html_to_text(html):
    p = _Text()
    p.feed(html)
    text = re.sub(r"[ \t\r\f\v]+", " ", "".join(p.out))
    text = re.sub(r" ?\n ?", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    m = re.search(r"(?mi)^#{1,6}\s+(?:\d+(?:\.\d+)*\.?\s+)?(?:References|Bibliography)\s*$", text)
    return (text[:m.start()] if m else text).strip() + "\n"


def fulltext(sid):
    """(text, 'html' | 'abstract-only'). HTML first; the abstract (one API request) when no usable HTML exists."""
    sid = re.sub(r"v\d+$", "", sid)
    try:
        text = html_to_text(_get(f"https://arxiv.org/html/{sid}").decode("utf-8", "replace"))
        if len(text.split()) >= 300:
            return text, "html"
    except Exception:
        pass
    rows = _entries(_get("http://export.arxiv.org/api/query?" + urllib.parse.urlencode({"id_list": sid})))
    if not rows:
        raise RuntimeError(f"arXiv has no record for {sid}")
    return f"# {rows[0]['title']}\n\n## Abstract\n\n{rows[0]['abstract']}\n", "abstract-only"


def selftest():
    import io
    from unittest.mock import patch
    atom = """<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
<entry><id>http://arxiv.org/abs/2601.00001v2</id><published>2026-01-02T00:00:00Z</published><title>Gerber
 statistic</title><summary> Robust
 covariance. </summary></entry>
<entry><id>http://arxiv.org/api/errors#x</id><title>Error</title><summary>bad</summary></entry></feed>"""
    page = ("<html><head><title>t</title><style>x{}</style></head><body><nav>MENU</nav><h1>Title</h1>"
            "<div class='ltx_abstract'><h6>Abstract</h6><p>Risk is 7 percent.</p></div><h2><span class='ltx_tag_section'>1</span>Introduction</h2>"
            "<p>We use <math alttext='x_i'><mi>x</mi></math> now. " + "word " * 300 + "</p><script>evil()</script>"
            "<section class='ltx_bibliography'><h2>References</h2><p>Smith 2020</p></section><footer>FOOT</footer></body></html>")
    seen = []

    def fake(req, timeout=0):
        seen.append(req.full_url)
        if "/html/2601.00001" in req.full_url:
            return io.BytesIO(page.encode())
        if "/html/" in req.full_url:
            raise OSError("404")
        return io.BytesIO(atom.encode())

    with patch.object(urllib.request, "urlopen", fake), patch.object(time, "sleep", lambda s: None), patch(__name__ + ".GAP", 0):
        text = search("Gerber statistic: covariance!")
        assert text.startswith("[ID=2601.00001] **Gerber statistic** (https://arxiv.org/abs/2601.00001). Published 2026-01-02\nRobust covariance.") and "errors" not in text, text
        assert "all%3AGerber+OR+all%3Astatistic" in seen[0] and "max_results=8" in seen[0]
        from lab import survey
        papers = survey._papers(["q"], [text])
        assert papers[0]["id"] == "2601.00001" and papers[0]["published"] == "2026-01-02"
        from lab import ideate
        with patch.dict(os.environ, {"LAB_PAPERS": "arxiv"}):
            assert ideate._search(ideate.SEARCH, "Gerber statistic").startswith("[ID=2601.00001]")
            assert ideate._search("echo custom {q}", "x").strip() == "custom x"  # an explicit search_cmd wins
        doc, kind = fulltext("2601.00001v1")
        assert kind == "html" and "# Title" in doc and "Risk is 7 percent." in doc and "x_i" in doc, doc
        assert not any(w in doc for w in ("MENU", "evil", "FOOT", "Smith", "References"))
        assert "## Introduction" in doc
        doc, kind = fulltext("2601.00002")  # no HTML -> abstract only
        assert kind == "abstract-only" and "Robust covariance." in doc and doc.startswith("# Gerber statistic")
    with patch.dict(os.environ, {"LAB_PAPERS": ""}), patch.object(shutil, "which", return_value="/bin/alpha"):
        assert not use_arxiv() and source() == "alpha"
        with patch.dict(os.environ, {"LAB_PAPERS": "arxiv"}):
            assert use_arxiv() and source() == "arxiv-api"
    with patch.dict(os.environ, {"LAB_PAPERS": ""}), patch.object(shutil, "which", return_value=None):
        assert use_arxiv() and source() == "arxiv-api"
    print("arxiv selftest ok (atom parse, html to text, abstract-only, source selection)")
