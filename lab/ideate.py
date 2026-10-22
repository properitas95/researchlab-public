"""ideate: brief -> scored candidates. novelty: literature search per top candidate. The user picks; nothing is dropped."""
import json
import re
import shlex
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace

from lab import core, director, roles, testkit

NUM = {"impact": (1, 5), "soundness": (1, 5), "p_success": (0, 1)}
SEARCH = "alpha search --json {q}"


def _clean(x):
    """One candidate dict with value fields added, or None if a field is missing or out of range."""
    try:
        o = {k: str(x[k]).strip() for k in ("id", "title", "hypothesis", "test")}
        if not all(o.values()):
            return None
        for k, (lo, hi) in NUM.items():
            v = x[k]
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not lo <= v <= hi:
                return None
            o[k] = v
    except (KeyError, TypeError):
        return None
    if str(x.get("seed", "")).strip():
        o["seed"] = str(x["seed"]).strip()  # knowledge-graph combination id (K1, ...) this candidate came from
    if isinstance(x.get("papers"), list):
        o["papers"] = [str(i).strip() for i in x["papers"] if str(i).strip()]  # paper ids from field_papers.md
    u = abs(2 * o["p_success"] - 1)
    o["value"] = round(o["impact"] * (1 - u) * o["soundness"], 3)
    o["predictable"] = u > 0.8
    return o


def ideate(a):
    d = core.run_dir(a.run)
    from lab import combine
    combos = combine.build()  # untried method × dataset × metric from paper concept graphs (KG seeds)
    (d / "combos.json").write_text(json.dumps(combos, indent=1, ensure_ascii=False) + "\n")
    reply = roles.call("ideate", core.prompt("ideate", brief=(d / "brief.md").read_text(), combos=combine.block(combos),
                        field=(d / "field.md").read_text() if (d / "field.md").exists() else "(no field survey)",
                        papers=(d / "field_papers.md").read_text() if (d / "field_papers.md").exists() else "(no papers read)"), cwd=d, run=d, write=False)
    raw = core.last_json(reply)
    if not isinstance(raw, list):
        raise ValueError("ideate reply must end with a JSON array")
    cands, seen = [], set()
    for x in raw:
        o = _clean(x) if isinstance(x, dict) else None
        if o and o["id"] not in seen:
            seen.add(o["id"])
            cands.append(o)
    if len(cands) < 3:
        raise ValueError(f"only {len(cands)} valid candidates of {len(raw)}")
    cands.sort(key=lambda o: -o["value"])
    (d / "candidates.json").write_text(json.dumps(cands, indent=1, ensure_ascii=False) + "\n")
    core.event(d, "ideate", f"{len(cands)} candidates ({len(raw) - len(cands)} invalid dropped)", top=cands[0]["id"])
    for o in cands[:3]:
        print(f"  {o['id']} {o['title']}  value {o['value']}{'  PREDICTABLE' if o['predictable'] else ''}")


def preregister(a):
    """Pre-registered brief: the design in the brief is the one candidate; no ideate/screen/novelty/pick."""
    d = core.run_dir(a.run)
    design = director.prereg_section(d)
    if design is None:
        raise ValueError('brief.md has no "## Pre-registered design" section')
    title = next((ln.lstrip("# ").strip() for ln in (d / "brief.md").read_text(encoding="utf-8").splitlines() if re.match(r"#[ \t]", ln)), d.name)
    fp = d / "field_papers.md"
    ids = list(dict.fromkeys(re.findall(r"^- (\S+) \|", fp.read_text(encoding="utf-8"), re.M))) if fp.exists() else []
    cand = dict(id="C1", title=title, hypothesis=design, test=design, value=1, predictable=False, papers=ids)
    for name, body in (("candidates.json", [cand]), ("novelty.json", {"C1": {"verdict": "preregistered"}}), ("pick.json", cand)):
        (d / name).write_text(json.dumps(body, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    core.event(d, "preregister", f"pre-registered design is candidate C1: {title}", papers=len(ids))


def _search(cmd, q):
    if cmd == SEARCH:
        from lab import arxiv
        if arxiv.use_arxiv():  # no alpha on PATH (or LAB_PAPERS=arxiv): public arXiv API
            return arxiv.search(q)[:6000]
    try:
        r = subprocess.run(cmd.replace("{q}", shlex.quote(q)), shell=True, capture_output=True, text=True, timeout=180)
    except subprocess.TimeoutExpired:
        return "ERROR: search timed out"
    out = r.stdout if not r.returncode else f"ERROR (exit {r.returncode}): {r.stderr.strip() or r.stdout.strip()}"
    if out.lstrip().startswith('{"error"'):  # alpha reports auth failures as exit 0 + {"error": ...}
        out = f"ERROR: {out.strip()}"
    return out[:6000]


def novelty(a):
    d = core.run_dir(a.run)
    c = core.cfg(d)
    cmd = c.get("search_cmd", SEARCH)
    out = {}
    for cand in json.loads((d / "candidates.json").read_text())[:getattr(a, "top", 3)]:
        qs = [cand["title"], cand["title"] + " limitation OR fails OR negative result"]
        res = [_search(cmd, q) for q in qs]
        text = "\n\n".join(f"--- query: {q}\n{r}" for q, r in zip(qs, res))
        if all(r.startswith("ERROR") for r in res):  # nothing to judge: skip the model call
            out[cand["id"]] = {"verdict": "unclear", "closest": [], "note": f"search failed: {res[0][:200]}", "queries": qs}
            print(f"  {cand['id']} unclear: search failed")
            continue
        try:
            j = core.last_json(roles.call("novelty", core.prompt(
                "novelty", candidate=json.dumps(cand, ensure_ascii=False), results=text), cwd=d, run=d, write=False))
            if not isinstance(j, dict):
                raise ValueError("reply is not a JSON object")
        except (ValueError, RuntimeError) as e:
            j = {"verdict": "unclear", "closest": [], "note": f"judge failed: {e}"}
        if j.get("verdict") not in ("novel", "prior_work", "unclear"):
            j["verdict"] = "unclear"
        out[cand["id"]] = j | {"queries": qs}
        print(f"  {cand['id']} {j['verdict']}: {str(j.get('note', ''))[:100]}")
    (d / "novelty.json").write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n")
    core.event(d, "novelty", f"{len(out)} candidates searched, none dropped", verdicts={k: v["verdict"] for k, v in out.items()})

FAKE = '''import json, sys
s = sys.stdin.read()
if "TASK: ideate" in s:
    # (id, impact, p, soundness): C1 value 20; C3 and C8 predictable; C5 invalid (impact 7)
    rows = [("C1", 5, .5, 4), ("C2", 5, .85, 5), ("C3", 5, .97, 5), ("C4", 5, .6, 3), ("C5", 7, .5, 3),
            ("C6", 4, .5, 3), ("C7", 2, .5, 2), ("C8", 3, .05, 4)]
    print("thinking...\\n" + json.dumps([dict(id=i, title="t " + i, hypothesis="h", test="x", impact=im, p_success=p,
          soundness=so) for i, im, p, so in rows]))
elif "TASK: novelty" in s:
    assert "fake-results" in s and "negative result" in s
    print("ok\\n" + json.dumps({"verdict": "prior_work" if "t C1" in s else "novel",
          "closest": [{"title": "P", "id_or_url": "u", "why": "w"}], "note": "n"}))
'''

def selftest():
    with tempfile.TemporaryDirectory() as t:
        d = Path(t) / "r"
        d.mkdir()
        testkit.fake(d, FAKE)
        (d / "brief.md").write_text("brief\n")
        core.save_cfg(d, {"id": "t", "exp_dir": str(d / "exp"), "provider": "cmd:python3 fake.py",
                          "reviewer": "cmd:python3 fake.py", "search_cmd": "echo fake-results {q}"})
        a = SimpleNamespace(run=str(d), top=2)
        ideate(a)
        cs = json.loads((d / "candidates.json").read_text())
        ids = [x["id"] for x in cs]
        assert "C5" not in ids and len(cs) == 7, ids
        assert [x["value"] for x in cs] == sorted((x["value"] for x in cs), reverse=True)
        by = {x["id"]: x for x in cs}
        assert by["C1"]["value"] == 20 and by["C3"]["predictable"] and by["C8"]["predictable"] and not by["C2"]["predictable"], by
        novelty(a)
        nv = json.loads((d / "novelty.json").read_text())
        assert list(nv) == ids[:2] and nv["C1"]["verdict"] == "prior_work" and len(nv["C1"]["queries"]) == 2, nv
        (d / "fake.py").write_text("print('[1,2]')")
        try:
            ideate(a)
            raise SystemExit("expected ValueError")
        except ValueError:
            pass
        # pre-registered brief: survey -> read-field -> preregister -> (fetch-data); an ordinary brief still goes through ideate
        (d / "fake.py").write_text("raise SystemExit('no model call')")
        for f in ("candidates.json", "novelty.json", "pick.json"):
            (d / f).unlink(missing_ok=True)
        brief = "# Gerber vs LW\n\nContext.\n\n## Pre-registered design\n\nH1: c=0.5 beats LW.\nMetric: GMV vol.\n\n## Data\n\nfoo\n"
        (d / "brief.md").write_text(brief)
        assert director.prereg_section(d) == "H1: c=0.5 beats LW.\nMetric: GMV vol."
        order = []
        for step in ("field.md", "field_papers.md", None):
            order.append(director.next_cmd(d).split()[1])
            if step:
                (d / step).write_text("# Papers read\n\n- 2601.00001 | T | deep | supports | c | papers/2601.00001/\n" if step == "field_papers.md" else "f")
        assert order == ["survey", "read-field", "preregister"], order
        preregister(a)
        cand = json.loads((d / "candidates.json").read_text())
        assert len(cand) == 1 and cand[0]["id"] == "C1" and cand[0]["title"] == "Gerber vs LW" and cand[0]["hypothesis"] == "H1: c=0.5 beats LW.\nMetric: GMV vol."
        assert cand[0]["papers"] == ["2601.00001"] and json.loads((d / "pick.json").read_text()) == cand[0]
        assert json.loads((d / "novelty.json").read_text()) == {"C1": {"verdict": "preregistered"}} and director._last_event(d, "preregister")
        assert director.next_cmd(d).split()[1] == "fetch-data"
        for f in ("candidates.json", "novelty.json", "pick.json"):
            (d / f).unlink(missing_ok=True)
        (d / "brief.md").write_text("# Plain\n\nNo design section.\n")
        assert director.next_cmd(d).split()[1] == "ideate" and director.prereg_section(d) is None
        try:
            preregister(a)
            raise SystemExit("expected ValueError")
        except ValueError:
            pass
    print("ideate selftest ok")

