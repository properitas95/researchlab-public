"""Field survey and sealed candidate screening, before the user picks a hypothesis."""
import json
import re
import shutil
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from statistics import median
from types import SimpleNamespace

from lab import core, roles, testkit
from lab.ideate import _search, SEARCH

PANEL = [["codex", None], ["claude", "sonnet"], ["codex", None]]
NUM = {"impact": (1, 5), "p_success": (0, 1), "soundness": (1, 5), "feasibility": (1, 5)}


def _papers(qs, res):
    """Unique papers from alpha result text, best rank first (interleaved across queries, first query wins ties)."""
    rows = []
    for qi, (q, r) in enumerate(zip(qs, res)):
        if r.startswith("ERROR"):
            continue
        for rank, m in enumerate(re.finditer(r"\[ID=([^\]\s]+)\] \*\*(.+?)\*\* \((\S+?)\)\.(?: Published (\d{4}-\d\d-\d\d))?", r)):
            rows.append((rank, qi, dict(id=m[1], title=m[2], url=m[3], published=m[4] or "", q=q)))
    seen, out = set(), []
    for _, _, x in sorted(rows, key=lambda t: t[:2]):
        if x["id"] not in seen:
            seen.add(x["id"])
            out.append(x)
    return out


def survey(a):
    d = core.run_dir(a.run)
    c = core.cfg(d)
    brief = (d / "brief.md").read_text()
    ask = lambda phase, results: roles.call("survey", core.prompt("survey", phase=phase, brief=brief,
                                           results=results), cwd=d, run=d, write=False)
    qs = core.last_json(ask("queries", "(not searched yet)"))
    if not isinstance(qs, list) or len(qs) != 6 or not all(isinstance(q, str) and q.strip() for q in qs):
        raise ValueError("survey needs six nonempty search queries")
    res = [_search(c.get("search_cmd", SEARCH), q) for q in qs]
    failed = [q for q, r in zip(qs, res) if r.startswith("ERROR")]
    results = "\n\n".join(f"Query: {q}\n{r}" for q, r in zip(qs, res))
    text = "# Field survey\n\nAll searches failed; no papers or field claims can be established."
    if len(failed) < len(qs):
        text = ask("field", results).strip()
        if not text:
            raise ValueError("empty field survey")
    text += "\n\n## Search record\n\n" + results
    (d / "field.md").write_text(text + "\n")
    (d / "search.json").write_text(json.dumps(_papers(qs, res), indent=1, ensure_ascii=False) + "\n")
    from lab import arxiv
    src = arxiv.source() if c.get("search_cmd", SEARCH) == SEARCH else "search_cmd"
    core.event(d, "survey", f"field.md written; {len(failed)}/6 searches failed; source {src}", failed=failed, source=src)


def _score(spec, files, candidates, run):
    try:
        with tempfile.TemporaryDirectory(prefix="screen-") as t:
            for f in files:
                shutil.copy2(f, Path(t) / f.name)
            (Path(t) / "candidates.json").write_text(json.dumps(candidates, ensure_ascii=False))
            return roles.call("screen", core.prompt("screen"), cwd=t, run=run, write=False, provider=spec[0], model=spec[1] or "")
    except Exception as e:
        return json.dumps({"error": str(e)})


def _sealed(paths, ids):
    good, failures = [], {}
    for p in paths:
        try:
            rows = core.last_json(p.read_text())
            if not isinstance(rows, list) or len(rows) != len(ids):
                raise ValueError("expected one score per candidate")
            by = {}
            for r in rows:
                if not isinstance(r, dict) or r.get("id") not in ids or r["id"] in by:
                    raise ValueError("unknown or duplicate candidate")
                if not isinstance(r.get("why"), str) or not r["why"].strip():
                    raise ValueError("missing why")
                for k, (lo, hi) in NUM.items():
                    v = r.get(k)
                    if isinstance(v, bool) or not isinstance(v, (int, float)) or not lo <= v <= hi:
                        raise ValueError(f"invalid {k}")
                by[r["id"]] = r
            good.append(by)
        except (ValueError, TypeError) as e:
            failures[p.name] = str(e)
    return good, failures


def screen(a):
    d = core.run_dir(a.run)
    c = core.cfg(d)
    candidates = json.loads((d / "candidates.json").read_text())
    top = sorted(candidates, key=lambda x: -(x.get("value") or 0))[:getattr(a, "top", 6)]
    ids = {x["id"] for x in top}
    if not ids or len(ids) != len(top):
        raise ValueError("screen needs unique candidates")
    specs = c.get("screen_panel", PANEL)
    files = [d / n for n in ("brief.md", "field.md") if (d / n).exists()]
    out = d / "screen"
    out.mkdir(exist_ok=True)
    with ThreadPoolExecutor(max_workers=3) as pool:
        replies = list(pool.map(lambda s: _score(s, files, top, d), specs))
    paths = [out / f"S{i}.json" for i in range(1, len(specs) + 1)]
    for p, text in zip(paths, replies):
        p.write_text(text)
    good, failures = _sealed(paths, ids)
    (out / "scores.json").write_text(json.dumps({"n_valid": len(good), "failed": failures}, indent=1) + "\n")
    core.event(d, "screen", f"{len(good)} valid scorers; {len(failures)} failed", failed=failures, n_valid=len(good))
    if len(good) < 2:
        raise ValueError("screen needs at least 2 valid scorers")
    for x in candidates:
        x.setdefault("self", {k: x[k] for k in (*NUM, "value", "predictable") if k in x})
        if x["id"] not in ids:
            x["value"] = None  # Unscreened self-values are not comparable to panel values.
            continue
        scores = {k: [r[x["id"]][k] for r in good] for k in NUM}
        x.update({k: median(v) for k, v in scores.items()})
        u = abs(2 * x["p_success"] - 1)
        x["value"] = x["impact"] * (1 - u) * x["soundness"] * x["feasibility"] / 5
        x["contested"] = max(scores["p_success"]) - min(scores["p_success"]) > .4 or max(scores["impact"]) - min(scores["impact"]) >= 3
        x["predictable"] = u > .8
    candidates.sort(key=lambda x: (x["value"] is not None, x["value"] or 0), reverse=True)
    (d / "candidates.json").write_text(json.dumps(candidates, indent=1, ensure_ascii=False) + "\n")
    for x in candidates[:3]:
        flags = " ".join(k.upper() for k in ("contested", "predictable") if x.get(k))
        print(f"  {x['id']} {x['title']} value {x['value']} {flags}")


FAKE = '''import json, pathlib, sys
s = sys.stdin.read()
if "Phase: queries" in s:
    print(json.dumps(["q" + str(i) for i in range(6)]))
elif "Phase: field" in s:
    assert "paper-123" in s
    print("# Field map\\nSubarea A\\n## SOTA cards\\npaper-123: claim\\n## Field beliefs\\nB\\n## Open gaps\\nG")
else:
    assert set(p.name for p in pathlib.Path('.').iterdir()) <= {'brief.md', 'field.md', 'candidates.json', '.claude', '.agents'}  # + bundled skills
    rows = json.loads(pathlib.Path('candidates.json').read_text())
    assert len(rows) == 6 and rows[0]['id'] == 'C7'
    k = sys.argv[1]
    if k == 'bad': print('{broken')
    else: print(json.dumps([dict(id=r['id'], impact=1 if k=='a' else 5, p_success=.99 if r['id']=='C7' else (.1 if k=='a' else .9),
                                soundness=4, feasibility=5, why='reason') for r in rows]))
'''


def selftest():
    from unittest.mock import patch
    with tempfile.TemporaryDirectory() as t:
        d = Path(t)
        fake = testkit.fake(d, FAKE)
        (d / "brief.md").write_text("brief")
        core.save_cfg(d, {"provider": "cmd:python3 fake.py", "search_cmd": "echo paper-123 {q}",
                          "screen_panel": [[fake + " " + k, None] for k in ("a", "b", "bad")]})
        a = SimpleNamespace(run=str(d))
        survey(a)
        assert "## Open gaps" in (d / "field.md").read_text()
        assert json.loads((d / "search.json").read_text()) == []  # no [ID=...] rows in the fake output
        rows = _papers(["a", "b"], ["1. [ID=2601.00001] **A** (http://x/1). Published 2026-01-02 by U: s\n2. [ID=2601.00002] **B** (http://x/2). s",
                                    "1. [ID=2601.00002] **B** (http://x/2). s\n2. [ID=2601.00003] **C** (http://x/3). s"])
        assert [r["id"] for r in rows] == ["2601.00001", "2601.00002", "2601.00003"] and rows[0]["published"] == "2026-01-02"
        (d / "offline.py").write_text("print('{\"error\":\"offline\"}')")
        core.save_cfg(d, core.cfg(d) | {"search_cmd": f'"{sys.executable}" "{d / "offline.py"}"'})
        with patch.object(core, "invoke", wraps=core.invoke) as calls:
            survey(a)
            assert calls.call_count == 1
        assert "All searches failed" in (d / "field.md").read_text()
        cs = [dict(id=f"C{i}", title="t", impact=3, p_success=.5, soundness=4, value=i) for i in range(8)]
        (d / "candidates.json").write_text(json.dumps(cs))
        original = _sealed
        def checked(paths, ids):
            assert all(p.exists() for p in paths) and len(paths) == 3
            assert json.loads((d / "candidates.json").read_text()) == cs
            return original(paths, ids)
        with patch(__name__ + "._sealed", checked):
            screen(a)
        rows = json.loads((d / "candidates.json").read_text())
        assert rows[0]["contested"] and rows[0]["value"] == 12 and rows[0]["self"]["impact"] == 3
        assert next(r for r in rows if r["id"] == "C7")["predictable"]
        assert all(r["value"] is None for r in rows[-2:])
        assert (d / "screen/S3.json").read_text() == '{broken\n'
        assert json.loads((d / "screen/scores.json").read_text())["n_valid"] == 2
        core.save_cfg(d, core.cfg(d) | {"screen_panel": [[fake + " bad", None]] * 3})
        before = (d / "candidates.json").read_bytes()
        try:
            screen(a)
            raise AssertionError("expected invalid panel")
        except ValueError:
            pass
        assert (d / "candidates.json").read_bytes() == before
    print("survey selftest ok (survey, all-fail, sealed screen, contested, quorum)")
