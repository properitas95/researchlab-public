"""Rule-based lab director: read the run folder, name the one next step. No model call."""
import json
import re
from pathlib import Path

from lab import core


def _trials(exp, q):
    f = exp / "results.tsv"
    if not f.exists():
        return []
    head, *lines = f.read_text().splitlines()
    cols = head.split("\t")
    return [r for r in (dict(zip(cols, ln.split("\t"))) for ln in lines if ln) if r["q"] == q]


def _last_event(d, stage):
    f = d / "events.jsonl"
    evs = [json.loads(x) for x in f.read_text().splitlines()] if f.exists() else []
    return next((e for e in reversed(evs) if e["stage"] == stage), None)


def _verdict(exp, q):
    f = exp / "DECISIONS.md"
    lines = [ln for ln in f.read_text().splitlines() if f" {q} **" in ln] if f.exists() else []
    return lines[-1].split("**")[1] if lines else None


def anchor_problem(c, trials):
    """None when no anchor is set or the baseline reproduces it (autoresearchlab reproduction anchor)."""
    if "anchor" not in c or not trials:
        return None
    s = float(trials[0]["score"] or "nan")
    if abs(s - c["anchor"]) <= c.get("anchor_tol", 0):
        return None
    return (f"baseline #{trials[0]['n']} scored {trials[0]['score']}, not the reproduction anchor "
            f"{c['anchor']}±{c.get('anchor_tol', 0)}")


def prereg_section(d):
    """Text under the brief's "## Pre-registered design" heading (to the next ## heading), or None for an ordinary brief."""
    f = d / "brief.md"
    m = re.search(r"^##[ \t]+Pre-registered design\b[^\n]*$(.*?)(?=^##[ \t]|\Z)", f.read_text(encoding="utf-8") if f.exists() else "", re.M | re.I | re.S)
    return m[1].strip() if m else None


def next_cmd(d):
    c, rid = core.cfg(d), d.name
    exp, q = Path(c["exp_dir"]), c["q"]
    lab = "./lab.py"
    if not (d / "candidates.json").exists():
        if not (d / "field.md").exists():
            return f"{lab} survey {rid}"
        if not (d / "field_papers.md").exists():
            return f"{lab} read-field {rid}"
        return f"{lab} {'preregister' if prereg_section(d) is not None else 'ideate'} {rid}"
    if not (d / "pick.json").exists():
        ev = _last_event(d, "screen")
        sealed = len(list((d / "screen").glob("S*.json"))) if (d / "screen").is_dir() else 0
        if (not ev or ev.get("n_valid", 0) < 2) and sealed < 2:
            return f"{lab} screen {rid}"
    if not (d / "novelty.json").exists():
        return f"{lab} novelty {rid}"
    if not (d / "pick.json").exists():
        cands = json.loads((d / "candidates.json").read_text())[:3]
        top = "; ".join(f"{x['id']} {x['title']} (value {x.get('value', '?')})" for x in cands)
        return f"USER: choose a candidate, then `{lab} pick {rid} <id>`. Top: {top}"
    if not (exp / "loop.json").exists() or q not in json.loads((exp / "loop.json").read_text()):
        if not (exp / "PROTOCOL.md").exists():
            return f"{lab} {'plan' if (d / 'data_plan.json').exists() else 'fetch-data'} {rid}"
        from lab import critic
        gate = critic.freeze_gate(d)
        if gate and gate.startswith(("critique missing", "critique stale")):
            return f"{lab} critic {rid}"
        if gate:
            return f"USER: fix the design first ({gate}; see runs/{rid}/CRITIQUE.md), then `{lab} critic {rid}` again"
        return (f"USER: read {exp}/PROTOCOL.md and the evaluator, then `{lab} freeze {rid} --eval CMD "
                f"--holdout CMD --frozen FILES --mutable FILES --min-delta X`")
    trials = _trials(exp, q)
    if anchor_problem(c, trials):
        return f"USER: {anchor_problem(c, trials)}. Fix the setup before any search"
    locked = (exp / ".rloop" / f"holdout_{q}.json").exists()
    if not locked and not _verdict(exp, q):
        ev = _last_event(d, "experiment")
        if not trials or ev is None:
            return f"{lab} explore {rid}   (tree search; `{lab} experiment {rid}` = linear loop)"
        return f"{lab} analyze {rid}   (or `{lab} explore {rid}` for more trials; last stop: {ev['msg']})"
    if not _verdict(exp, q):
        return f"{lab} analyze {rid}"
    for stage, artifact in (("ablate", "ablations.md"), ("final", "final_evaluation.json"),
                            ("posthoc", "posthoc/manifest.json"), ("hypotheses", "hypotheses.json"),
                            ("discriminate", "discrimination.json")):
        if not (d / artifact).exists():
            return f"{lab} {stage} {rid}"
    if not (d / "RESULT.md").exists():
        return f"{lab} write {rid}"
    if not (d / "REVIEW.md").exists():
        return f"{lab} review {rid}"
    if not (d / "paper" / "paper.tex").exists():
        return f"{lab} paper {rid}"
    if not (d / "panel" / "scores.json").exists():
        return f"{lab} panel {rid}"
    scores = json.loads((d / "panel" / "scores.json").read_text())
    hist = d / "panel" / "history.jsonl"
    if (scores.get("meta_decision") != "accept" or scores.get("surviving_major")) and \
            (len(hist.read_text().splitlines()) if hist.exists() else 0) < 2:
        return f"{lab} revise {rid}"
    if not (d / "index.html").exists():
        return f"{lab} inspect {rid}"
    v = _verdict(exp, q)
    if v in ("no_improvement", "invalid", "inconclusive"):
        return f"DONE ({v}). Pivot: `{lab} new {rid}-b --brief-file runs/{rid}/brief.md` and pick another candidate"
    return f"DONE ({v}). Deliver through `harness preflight` if this becomes a report"


def status(d):
    c = core.cfg(d)
    exp = Path(c["exp_dir"])
    print(f"run {d.name}: {(d / 'brief.md').read_text().splitlines()[0][:100]}")
    if (d / "pick.json").exists():
        print(f"pick: {json.loads((d / 'pick.json').read_text())['title']}")
    if (exp / "loop.json").exists():
        print(core.rloop("status", exp).rstrip())
    print(f"next: {next_cmd(d)}")
