"""read-field: skim the papers the survey found, deep-read the best, write field_papers.md for ideate."""
import json
import os
import shutil
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

from lab import arxiv, core, read, testkit


def _int(name, default):
    try:
        return max(0, int(os.environ.get(name, default)))
    except ValueError:
        return default


def _line(card, sid):
    text = lambda x: " ".join(str(x).split())[:200]
    if card["mode"] == "deep":
        claims = [f"{text(x.get('text', ''))} [{sid}#q{i}]" for i, x in enumerate(card.get("claims") or [], 1) if isinstance(x, dict) and x.get("quote")][:3]
    else:
        nums = [x for x in card.get("numbers") or [] if isinstance(x, dict) and x.get("quote")][:2]
        claims = [f"{text(card.get('claim', ''))} (skim)"] + [f"{text(x.get('context', ''))}: {x.get('value')} {x.get('unit', '')} [{sid}#n{i}]" for i, x in enumerate(nums, 1)]
    return f"- {sid} | {text(card.get('title') or sid)} | {card['mode']} | {card.get('relation')} | {'; '.join(claims) or 'no verified claims'} | papers/{sid}/"


def read_field(a):
    d = core.run_dir(a.run)
    c = core.cfg(d)
    out = d / "field_papers.md"
    papers = json.loads((d / "search.json").read_text()) if (d / "search.json").exists() else []
    reason = "no search record (every search failed or none was saved)" if not papers else ""
    if reason:
        out.write_text(f"# Papers read (verified quotes)\n\nno papers read: {reason}\n")
        return core.event(d, "read-field", f"skipped: {reason}")
    thesis = (d / "brief.md").read_text()[:1500]
    top = papers[:_int("LAB_READ_MAX", 30)]
    deep_n, workers = _int("LAB_READ_DEEP", 5), max(1, _int("LAB_READ_WORKERS", 4))

    def one(p, mode):
        spec = dict(run=str(d), paper=p["id"], mode=mode, thesis=thesis, meta=dict(title=p["title"], url=p["url"], year=p["published"][:4]))
        if mode == "skim":  # cheap reviewer model; deep reads use the researcher provider
            spec.update(provider=c["reviewer"], model=c["reviewer_model"])
        try:
            return p["id"], read.read(SimpleNamespace(**spec)), None
        except Exception as e:
            return p["id"], None, f"{mode}: {e}"[:300]

    with ThreadPoolExecutor(max_workers=workers) as pool:
        skims = list(pool.map(lambda p: one(p, "skim"), top))
        cards = {sid: card for sid, card, _ in skims if card}
        failed = {sid: err for sid, _, err in skims if err}
        pick = [p for p in top if p["id"] in cards and cards[p["id"]].get("worth_deep")][:deep_n]
        for sid, card, err in pool.map(lambda p: one(p, "deep"), pick):
            if card:
                cards[sid] = card
            else:
                failed[sid] = err
    rows = [_line(cards[p["id"]], p["id"]) for p in top if p["id"] in cards]
    deep = sum(x["mode"] == "deep" for x in cards.values())
    out.write_text(f"# Papers read (verified quotes)\n\n{len(top)} papers tried, {len(cards)} read ({deep} deep), {len(failed)} failed. "
                   "Cards, full text and metadata sit in papers/<id>/ (card.json holds the quotes).\n\n" + "\n".join(rows)
                   + ("\n\nFailed: " + "; ".join(f"{k} ({v})" for k, v in failed.items()) if failed else "") + "\n")
    src = arxiv.source()
    core.event(d, "read-field", f"{len(cards)}/{len(top)} papers read, {deep} deep, {len(failed)} failed; source {src}", failed=failed, source=src)
    try:
        from lab import kg
    except ImportError:
        return
    try:
        kg.build()
    except Exception as e:
        core.event(d, "read-field", f"kg.build failed: {e}")


FAKE_ALPHA = '''#!/usr/bin/env python3
import sys
if sys.argv[1:3] == ["get", "--full-text"] and sys.argv[3] == "2601.00003":
    raise SystemExit("boom")
print("# Paper " + sys.argv[-1] + "\\nAbstract\\nRisk is 7 percent.\\n1 Introduction\\nIntro.\\n2 Conclusion\\nRisk is 7 percent.")
'''
FAKE = '''import json, sys
sys.stdin.read()
print(json.dumps(dict(title="T", worth_deep=True, claim="Risk claim", relation="supports", why="w",
      claims=[dict(text="real", quote="Risk is 7 percent.", page_or_section="A"), dict(text="bad", quote="made up")],
      numbers=[dict(value=7, unit="percent", context="risk", quote="Risk is 7 percent.")])))
'''


def selftest():
    from unittest.mock import patch
    from lab import director, ideate, survey
    with tempfile.TemporaryDirectory() as t:
        root = Path(t)
        d = root / "runs/r"
        d.mkdir(parents=True)
        (root / "alpha").write_text(FAKE_ALPHA)
        (root / "alpha").chmod(0o755)
        (root / "fake.py").write_text(FAKE)
        (d / "brief.md").write_text("brief")
        core.save_cfg(d, {"id": "r", "exp_dir": str(root / "exp"), "q": "Q1", "reviewer": f"cmd:python3 {root / 'fake.py'}",
                          "reviewer_model": "", "provider": f"cmd:python3 {root / 'fake.py'}"})
        (d / "field.md").write_text("field")
        assert director.next_cmd(d) == "./lab.py read-field r"
        rows = [dict(id=f"2601.0000{i}", title=f"Paper {i}", url=f"http://x/{i}", published="2026-01-01", q="q") for i in range(1, 8)]
        (d / "search.json").write_text(json.dumps(rows))
        env = dict(PATH=str(root) + os.pathsep + os.environ["PATH"], LAB_READ_MAX="5", LAB_READ_DEEP="2", LAB_READ_WORKERS="3")
        with patch.object(read, "ROOT", root), patch.object(read, "_fetch_pdf", return_value="offline"), patch.dict(os.environ, env), \
                patch.object(arxiv, "fulltext", side_effect=RuntimeError("offline")) as fb:
            read_field(SimpleNamespace(run=str(d)))
        assert fb.call_args_list[0].args == ("2601.00003",) and fb.call_count == 1  # the alpha failure was retried once via arXiv
        assert director.next_cmd(d) == "./lab.py ideate r"
        text = (d / "field_papers.md").read_text()
        ev = [json.loads(x) for x in (d / "events.jsonl").read_text().splitlines()]
        last = ev[-1]
        assert last["stage"] == "read-field" and "4/5 papers read, 2 deep, 1 failed" in last["msg"] and "2601.00003" in last["failed"], last
        assert sum(e["stage"] == "read" and e["msg"].endswith("skim") for e in ev) == 4
        assert sum(e["stage"] == "read" and e["msg"].endswith("deep") for e in ev) == 2
        assert "[2601.00001#q1]" in text and "bad" not in text and "2601.00006" not in text and "Failed: 2601.00003" in text
        assert (root / "papers/2601.00001/card.json").exists() and (root / "papers/2601.00003").exists() is False
        skim_only = [x for x in text.splitlines() if x.startswith("- 2601.00004")][0]
        assert "(skim)" in skim_only and "#n1" in skim_only
        # ideate sees the digest
        seen = []
        with patch.object(ideate.roles, "call", lambda stage, instr, **k: seen.append(instr) or "[]"), patch("lab.combine.build", return_value=[]):
            try:
                ideate.ideate(SimpleNamespace(run=str(d)))
            except ValueError:
                pass
        assert "2601.00001#q1" in seen[0] and "Papers read (verified quotes)" in seen[0]
        assert "source alpha" in last["msg"] and last["source"] == "alpha"
        # no alpha (or LAB_PAPERS=arxiv): the stdlib arXiv path, fake HTTP, meta.json says where the text came from
        (d / "field_papers.md").unlink()
        import io
        import urllib.request
        page = "<html><body><h1>Paper</h1><h2>Abstract</h2><p>Risk is 7 percent.</p><h2>Introduction</h2><p>" + "word " * 300 + "</p></body></html>"
        atom = ('<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>http://arxiv.org/abs/2601.00005v1</id><published>2026-01-01T00:00:00Z</published>'
                '<title>Abstract Only</title><summary>Risk is 7 percent.</summary></entry></feed>')
        urls = []

        def fake_open(req, timeout=0):
            urls.append(req.full_url)
            if "/html/2601.00002" in req.full_url:
                return io.BytesIO(page.encode())
            if "/html/" in req.full_url:
                raise OSError("404")
            return io.BytesIO(atom.encode())
        (d / "search.json").write_text(json.dumps(rows[1:2] + [dict(rows[4])]))  # 2601.00002 has html, 2601.00005 does not
        shutil.rmtree(root / "papers")
        env2 = dict(LAB_PAPERS="arxiv", LAB_READ_DEEP="0", LAB_READ_WORKERS="1")
        with patch.object(read, "ROOT", root), patch.object(read, "_fetch_pdf", return_value="offline"), patch.dict(os.environ, env2), \
                patch.object(urllib.request, "urlopen", fake_open), patch.object(arxiv, "GAP", 0):
            read_field(SimpleNamespace(run=str(d)))
        m1, m5 = (json.loads((root / f"papers/2601.0000{i}/meta.json").read_text()) for i in (2, 5))
        assert m1["source"] == "arxiv-api" and m1["full_text"] == "html" and m5["source"] == "arxiv-api" and m5["full_text"] == "abstract-only", (m1, m5)
        ev = [json.loads(x) for x in (d / "events.jsonl").read_text().splitlines()]
        assert ev[-1]["source"] == "arxiv-api" and "source arxiv-api" in ev[-1]["msg"] and not any("alpha" in u for u in urls)
        (d / "field_papers.md").unlink()
        (d / "search.json").write_text("[]")
        with patch.object(shutil, "which", return_value="alpha"):
            read_field(SimpleNamespace(run=str(d)))
        assert "no papers read: no search record" in (d / "field_papers.md").read_text()
    arxiv.selftest()
    print("readfield selftest ok (skim/deep split, failure, digest, ordering, arXiv fallback)")
