"""panel: blind isolated reviewers in parallel, then an area-chair meta-review checked against the ledger."""
import json
import shutil
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

from lab import core, roles, testkit, venue
from lab.util import read as _read

PANEL = [["codex", None, "methodologist"], ["claude", "sonnet", "skeptic"], ["codex", None, "reproducibility"]]
SCORES = {"soundness": (1, 4), "significance": (1, 4), "clarity": (1, 4), "overall": (1, 10), "confidence": (1, 5)}


def specs(c):
    """Panel specs; a run's target venue (run.json "venue", venues/<slug>/profile.json) supplies the reviewer personas."""
    prof = c.get("venue") and venue.venue_profile_of(c["venue"])
    base = c.get("panel", PANEL)
    return [[p, m, venue.persona(r)] for (p, m, _), r in zip(base, prof["reviewers"])] if prof else base


def bar(c):
    prof = c.get("venue") and venue.venue_profile_of(c["venue"])
    return "\n".join(f"{k}: {v}" for k, v in prof["acceptance_bar"].items()) if prof else ""


def _valid(j):
    ok = isinstance(j, dict) and j.get("decision") in ("accept", "revise", "reject")
    ok = ok and all(isinstance(j.get(k), int) and lo <= j[k] <= hi for k, (lo, hi) in SCORES.items())
    ok = ok and all(isinstance(j.get(k), list) for k in ("major", "minor"))
    if not ok:
        raise ValueError("review JSON failed validation")
    return j


def _review(spec, files, ev, run):
    provider, model, persona = spec
    with tempfile.TemporaryDirectory(prefix="panel-") as t:
        for f in files:
            shutil.copy2(f, Path(t) / f.name)
        text = roles.call("panel", core.prompt("panel_review", persona=persona, target=_read(files[0]),
                                                 ledger=ev["ledger"], decisions=ev["decisions"], protocol=ev["protocol"]), cwd=t, run=run, write=False, provider=provider, model=model or "")
    return text, _valid(core.last_json(text))


def _safe(spec, files, ev, run):
    try:
        return _review(spec, files, ev, run)
    except Exception as e:  # a failed reviewer is recorded, not fatal
        return f"FAILED: {e}", None


def panel(a):
    d = core.run_dir(a.run)
    c = core.cfg(d)
    exp = Path(c["exp_dir"])
    target = next((p for p in (d / "paper" / "paper.tex", d / "RESULT.md") if p.exists()), None)
    if target is None:
        sys.exit(f"no paper/paper.tex or RESULT.md in {d}")
    evid = [core.ledger(d, exp), *(exp / n for n in ("DECISIONS.md", "PROTOCOL.md") if (exp / n).exists())]
    ev = {"ledger": _read(d / "LEDGER.md"), "decisions": _read(exp / "DECISIONS.md"), "protocol": _read(exp / "PROTOCOL.md")}
    sp = specs(c)
    with ThreadPoolExecutor(max_workers=3) as ex:
        res = list(ex.map(lambda s: _safe(s, [target, *evid], ev, d), sp))
    out = d / "panel"
    out.mkdir(exist_ok=True)
    rows = []
    for i, (spec, (text, j)) in enumerate(zip(sp, res), 1):
        (out / f"R{i}.md").write_text(text.strip() + "\n")
        rows.append({"id": f"R{i}", "provider": spec[0], "model": spec[1], "persona": spec[2], "valid": j is not None,
                     **({k: j[k] for k in (*SCORES, "decision")} if j else {})})
    good = [(r, j) for r, (_, j) in zip(rows, res) if j]
    if len(good) < 2:
        (out / "scores.json").write_text(json.dumps({"reviewers": rows, "meta_decision": None}, indent=1) + "\n")
        sys.exit(f"only {len(good)} valid reviewer(s); need 2 (see {out})")
    reviews = "\n\n".join(f"--- {r['id']} ({r['persona']}) ---\n{j and json.dumps(j, ensure_ascii=False)}\n"
                          f"{(out / (r['id'] + '.md')).read_text()}" for r, j in good)
    if bar(c):
        reviews += f"\n\n--- TARGET VENUE ACCEPTANCE BAR ---\n{bar(c)}"
    with tempfile.TemporaryDirectory(prefix="panel-meta-") as t:
        mtext = roles.call("panel", core.prompt("panel_meta", reviews=reviews, ledger=ev["ledger"]), cwd=t, run=d, write=False)
    meta = core.last_json(mtext)
    if not isinstance(meta, dict) or meta.get("decision") not in ("accept", "revise", "reject"):
        raise ValueError("meta JSON has no valid decision")
    note = ""
    if meta["decision"] == "accept" and meta.get("surviving_major"):
        meta["decision"], note = "revise", "\n\nCODE RULE: meta said accept but listed surviving major points; downgraded to revise.\n"
    (out / "META.md").write_text(mtext.strip() + "\n" + note)
    mean = round(sum(j["overall"] for _, j in good) / len(good), 2)
    (out / "scores.json").write_text(json.dumps({"reviewers": rows, "mean_overall": mean, "meta_decision": meta["decision"],
                                                 "downgraded": bool(note), "surviving_major": meta.get("surviving_major", []),
                                                 "dismissed": meta.get("dismissed", [])}, indent=1, ensure_ascii=False) + "\n")
    core.event(d, "panel", f"{meta['decision']}; overall mean {mean}; {len(good)} valid reviewers", n_valid=len(good))


FAKE = '''import glob, json, sys
s = sys.stdin.read().split(chr(10)*2 + "---" + chr(10)*2, 1)[-1]
if s.startswith("TASK: panel meta"):
    assert "[methodologist]" not in s and "ledger-row" in s
    print('body\\n{"decision":"accept","surviving_major":["no control"],"dismissed":[{"point":"x","why":"row 3"}]}')
    sys.exit()
import os
assert os.path.exists("RESULT.md") or os.path.exists("paper.tex"), os.listdir(".")
assert not glob.glob("R[0-9]*.md") and not os.path.exists("META.md")
rev = {"soundness": 3, "significance": 2, "clarity": 3, "overall": 6, "confidence": 4, "major": ["no control"], "minor": [], "decision": "revise"}
if "reproducibility" in s:
    print("{broken json")
elif "skeptic" in s:
    print("본문\\n" + json.dumps(dict(rev, overall=4)))
else:
    print("본문\\n" + json.dumps(rev))
'''


def selftest():
    with tempfile.TemporaryDirectory() as t:
        d, exp = Path(t) / "r", Path(t) / "r" / "exp"
        exp.mkdir(parents=True)
        (d / "RESULT.md").write_text("결과 0.812 (#9)\n")
        (exp / "PROTOCOL.md").write_text("protocol\n")
        (exp / "results.tsv").write_text("trial\tscore\n9\t0.812\nledger-row\n")
        (exp / "DECISIONS.md").write_text("Q1 **keep**\n")
        fake = testkit.fake(d, FAKE)
        core.save_cfg(d, {"id": "t", "exp_dir": str(exp), "reviewer": fake, "reviewer_model": None,
                          "panel": [[fake, None, "methodologist"], [fake, None, "skeptic"], [fake, None, "reproducibility"]]})
        panel(SimpleNamespace(run=str(d)))
        s = json.loads((d / "panel" / "scores.json").read_text())
        assert s["meta_decision"] == "revise" and s["downgraded"], s
        assert [r["valid"] for r in s["reviewers"]] == [True, True, False] and s["mean_overall"] == 5.0, s
        assert (d / "panel" / "META.md").exists() and "FAILED" in (d / "panel" / "R3.md").read_text()
        assert "panel" in (d / "events.jsonl").read_text()
    print("panel selftest ok")


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    selftest()
