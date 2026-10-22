"""Knowledge-graph seeds for ideate: method × dataset × metric combinations from paper concept graphs
(helix/*/concepts.jsonl, typed by lab/concepts.py; kg/kg.json from lab/kg.py) that the methods ledger has not tried yet. No model calls."""
import json
import math
import os
import re

from lab import core

NOISE = re.compile(r"\.(tex|png|pdf|md|csv|json|zip)\b|\bdpi\b|render|checklist|review|ledger|commit|receipt|lint|"
                   r"detector|folder|file|slide|deck|font|page", re.I)  # manuscript-process artefacts, not research objects
TYPES = ("method", "dataset", "metric")


def concepts(root=None):
    root = root or core.ROOT
    out = {t: [] for t in TYPES}
    for p in sorted((root / "helix").glob("*/concepts.jsonl")):
        for line in p.read_text().splitlines():
            c = json.loads(line)
            if c.get("type") in out and not NOISE.search(c.get("name", "")):
                out[c["type"]].append(dict(c, paper=p.parent.name))
    kg = root / "kg" / "kg.json"  # concepts of the papers this workspace read (lab/kg.py)
    for n in (json.loads(kg.read_text()).get("nodes", []) if kg.exists() else []):
        have = {c["name"].lower() for c in out.get(n["type"], [])}
        if n["type"] in out and n["label"].lower() not in have and not NOISE.search(n["label"]):
            ps = n["props"].get("papers") or ["kg"]
            out[n["type"]].append(dict(concept_id=n["id"], name=n["label"], paper=ps[0], mention_count=len(ps)))
    catalog = root / "memory" / "global" / "datasets.json"  # local data the lab can actually run on
    for name in (json.loads(catalog.read_text()) if catalog.exists() else []):
        out["dataset"].append(dict(concept_id=f"catalog:{name}", name=str(name), paper="catalog", mention_count=3))
    for t in out:
        out[t].sort(key=lambda c: -c.get("mention_count", 1))
    return out


def tried(root=None):
    p = (root or core.ROOT) / "methods" / "ledger.jsonl"
    return " ".join(json.loads(l).get("method", "") for l in p.read_text().splitlines()).lower() if p.exists() else ""


def build(top=12, root=None):
    cs, done = concepts(root), tried(root)
    metric = cs["metric"][0] if cs["metric"] else dict(name="(choose)", concept_id="", paper="")
    combos = []
    for m in cs["method"][:top]:
        if m["name"].lower() in done:
            continue
        for ds in cs["dataset"][:top]:
            bridge = m["paper"] != ds["paper"]  # a method from one paper on another paper's data is the new combination
            score = math.log1p(m.get("mention_count", 1)) + math.log1p(ds.get("mention_count", 1)) + (1 if bridge else 0)
            combos.append(dict(method=m["name"], method_paper=m["paper"], dataset=ds["name"], dataset_paper=ds["paper"],
                               metric=metric["name"], score=round(score, 3), bridge=bridge,
                               concept_ids=[m.get("concept_id"), ds.get("concept_id"), metric.get("concept_id")]))
    v = os.environ.get("LAB_VENUE")
    if v and (core.ROOT / "venues" / v / "works.jsonl").exists():  # novelty vs the target venue: drop too-close, favour adjacent
        from lab import venue
        for c in combos:
            c["overlap"] = venue.overlap(f"{c['method']} {c['dataset']}", v)
            c["adjacent"] = c["overlap"]["adjacent"]
            c["score"] += 0.5 * c["adjacent"]
        combos = [c for c in combos if not c["overlap"]["too_close"]]
    combos.sort(key=lambda c: -c["score"])
    for i, c in enumerate(combos[:top], 1):
        c["id"] = f"K{i}"
    return combos[:top]


def block(combos):
    if not combos:
        return "(no concept graph available)"
    return "\n".join(f"- {c['id']}: {c['method']} ({c['method_paper']}) × {c['dataset']} ({c['dataset_paper']}) × {c['metric']}"
                     for c in combos)


def combine(a):
    d = core.run_dir(a.run)
    combos = build()
    (d / "combos.json").write_text(json.dumps(combos, indent=1, ensure_ascii=False) + "\n")
    core.event(d, "combine", f"{len(combos)} untried method×dataset combinations from concept graphs")
    print(block(combos))


def selftest():
    import tempfile
    from pathlib import Path
    from unittest.mock import patch
    with tempfile.TemporaryDirectory() as t, patch.object(core, "ROOT", Path(t)):
        root = Path(t)
        for paper, rows in {"a": [("method", "rank correlation", 5), ("method", "120dpi render check", 9), ("dataset", "KOSPI200 daily", 4),
                                  ("metric", "GMV variance", 6)], "b": [("method", "Ledoit-Wolf shrinkage", 3), ("dataset", "S&P 500", 2)]}.items():
            (root / "helix" / paper).mkdir(parents=True)
            (root / "helix" / paper / "concepts.jsonl").write_text("".join(json.dumps(dict(concept_id=f"{paper}{i}", type=ty, name=n, mention_count=m)) + "\n"
                                                                           for i, (ty, n, m) in enumerate(rows)))
        (root / "methods").mkdir()
        (root / "methods" / "ledger.jsonl").write_text(json.dumps(dict(method="Apply Ledoit-Wolf shrinkage to Spearman")) + "\n")
        out = build()
        names = {c["method"] for c in out}
        assert "120dpi render check" not in names and "Ledoit-Wolf shrinkage" not in names and "rank correlation" in names
        assert out[0]["bridge"] and out[0]["id"] == "K1" and all(c["metric"] == "GMV variance" for c in out)
        assert "K1:" in block(out) and block([]).startswith("(no")
    print("combine selftest: ok (noise filter, tried methods skipped, cross-paper bridge ranked first)")
