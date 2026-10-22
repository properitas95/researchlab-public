"""Data-defect ledger (PROTOCOL §4.1): critic findings that recur across runs become ledger entries.
open = recurring claim, unverified: recorded as a known constraint, not a blocker (results stay provisional).
confirmed = verified by the data engineer: blocks until fixed. fixed = recurrence is a regression. rejected = minor."""
import json

from lab import core

STATES = ("open", "confirmed", "fixed", "rejected")
MIN_RUNS = 3  # P21: the same finding in three runs becomes a rule


def _path():
    return core.ROOT / "memory" / "global" / "data_defects.json"


def load():
    p = _path()
    return json.loads(p.read_text()) if p.exists() else []


def _save(rows):
    _path().parent.mkdir(parents=True, exist_ok=True)
    _path().write_text(json.dumps(rows, indent=1, ensure_ascii=False) + "\n")


def sync():
    """Add topic classes seen in >= MIN_RUNS runs (methods ledger) as open entries; refresh run lists."""
    from lab import methods
    methods.scan()
    seen = {}
    for line in (core.ROOT / "methods" / "ledger.jsonl").read_text().splitlines():
        r = json.loads(line)
        if r.get("kind") == "finding" and r["class"] in dict(methods.TOPICS):  # free-text ids never become entries
            seen.setdefault(r["class"], {}).setdefault(r["run"], r.get("claim", ""))
    rows, added = load(), 0
    by = {r["topic"]: r for r in rows}
    for topic, runs in sorted(seen.items()):
        if topic in by:
            by[topic]["runs"] = sorted(set(by[topic]["runs"]) | set(runs))
        elif len(runs) >= MIN_RUNS:
            rows.append(dict(id=f"D-{topic}", topic=topic, status="open", runs=sorted(runs), first_seen=core.now(),
                             claim=next(iter(runs.values()))[:300], note=""))
            added += 1
    _save(rows)
    return rows, added


def apply(findings):
    """Rewrite critic findings in place by ledger status; returns the ledger ids cited."""
    from lab import methods
    by, cited = {r["topic"]: r for r in load()}, []
    for f in findings:
        r = by.get(methods._klass(f.get("id", ""), f.get("claim") or f.get("evidence") or ""))
        if not r or f.get("severity") not in ("blocker", "major", "minor"):
            continue
        if r["status"] == "open":
            f["severity"], f["defect"] = "known", r["id"]
        elif r["status"] == "rejected":
            f["severity"], f["defect"] = "minor", r["id"]
        elif r["status"] == "fixed":
            f["defect"], f["claim"] = r["id"], "regression of a fixed defect: " + str(f.get("claim", ""))
        else:
            f["defect"] = r["id"]  # confirmed: keeps its severity until fixed
        cited.append(r["id"])
    return sorted(set(cited))


def prompt_block():
    rows = [r for r in load() if r["status"] in ("open", "confirmed", "fixed")]
    if not rows:
        return "(none)"
    return "\n".join(f"- {r['id']} [{r['status']}]: {r['claim'][:160]}" for r in rows)


def defects(a):
    rows, added = sync()
    for r in rows:
        print(f"{r['id']:28s} {r['status']:9s} runs={len(r['runs'])} {', '.join(r['runs'])}")
    print(f"data defects: {len(rows)} ({added} new)")


def defect_set(a):
    rows = load()
    r = next((r for r in rows if r["id"] == a.id), None)
    if not r or a.status not in STATES:
        raise SystemExit(f"unknown id or status: {a.id} {a.status}")
    r["status"], r["note"] = a.status, f"{core.now()} {a.note}"
    _save(rows)
    print(f"{r['id']} → {a.status}")


def selftest():
    import tempfile
    from pathlib import Path
    from unittest.mock import patch
    with tempfile.TemporaryDirectory() as t, patch.object(core, "ROOT", Path(t)):
        (Path(t) / "methods").mkdir()
        rows = [dict(kind="finding", run=f"r{i}", id=f"membership-lag-{i}", claim="look-ahead in membership", **{"class": "look-ahead"}) for i in range(3)]
        rows += [dict(kind="finding", run="r0", id="odd-thing", claim="x", **{"class": "odd-thing"})]
        (Path(t) / "methods" / "ledger.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
        with patch("lab.methods.scan", lambda: None):
            led, added = sync()
            assert added == 1 and led[0]["id"] == "D-look-ahead" and len(led[0]["runs"]) == 3
            assert sync()[1] == 0  # idempotent
        f = [dict(id="pit-membership", severity="blocker", claim="universe uses future membership"), dict(id="x", severity="major", claim="unrelated")]
        assert apply(f) == ["D-look-ahead"] and f[0]["severity"] == "known" and f[1]["severity"] == "major"
        led[0]["status"] = "confirmed"; _save(led)
        f = [dict(id="pit", severity="blocker", claim="look-ahead")]
        apply(f); assert f[0]["severity"] == "blocker"  # a verified defect keeps blocking
        led[0]["status"] = "rejected"; _save(led)
        f = [dict(id="pit", severity="blocker", claim="look-ahead")]
        apply(f); assert f[0]["severity"] == "minor"
        assert "D-look-ahead" in prompt_block() or prompt_block() == "(none)"
    print("defects selftest: ok (sync idempotent, open→known, confirmed blocks, rejected→minor)")
