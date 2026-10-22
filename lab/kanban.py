"""Read-only artifact mirror to the research board; no models or evaluators are run.
Layout: one board project per run (key from the run id, kept in run.json "board_key"; name = run id; description = brief H1) holding an epic plus
one card per stage the run reached (done / current / the next two as todo; critic, replan, panel, revise get one card per round).
Workspace-wide cards (programs, methods ledger, data defects, seeds, leads, experiments) stay in project LAB. The stage group is a label
"group:<name>" and a milestone. Every card ends with links to the KG view (#run=<id>, #node=<id>) and the lineage page (#<run id>).
Card type by nature:
  spike    survey, read-field, ideate, preregister, screen, novelty, pick, candidate cards, combination seeds
  chore    fetch-data
  feature  plan, autofreeze, explore, analyze, final
  task     critic round, replan, ablate, posthoc, hypotheses, discriminate, write, review, paper, panel round, revise round, inspect
  bug      critic findings (blocker/major), data defects
  epic     the run itself"""
import hashlib
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from lab import core, director


def _url():
    """LAB_KANBAN_URL, else the board this workspace started (kanban/pid + kanban/port), else 8790."""
    k = core.ROOT / "kanban"
    port = (k / "port").read_text().strip() if (k / "pid").is_file() and (k / "port").is_file() else "8790"
    return os.environ.get("LAB_KANBAN_URL", f"http://127.0.0.1:{port}")


GROUPS = (("Ideas", "survey read-field ideate preregister screen novelty pick"), ("Design", "fetch-data plan critic replan freeze autofreeze"),
          ("Experiments", "explore experiment analyze"), ("Interpretation", "ablate final posthoc hypotheses discriminate"),
          ("Publication", "write review paper panel revise inspect"))
GROUP = {s: g for g, ss in GROUPS for s in ss.split()}
KIND = {**dict.fromkeys("survey read-field ideate preregister screen novelty pick".split(), "spike"), "fetch-data": "chore",
        **dict.fromkeys("plan autofreeze explore analyze final".split(), "feature")}  # everything else: task
ROUNDS = ("critic", "panel", "revise")  # titled "<Stage> round N"; replan is "Replan N"


def _json(p):
    return json.loads(p.read_text()) if p.exists() else {}


def _board_key(name, taken):
    """Uppercase A-Z0-9 key, 2-8 chars, letter first, from the run id; a hash suffix resolves clashes (stable: depends only on the id)."""
    toks = re.findall(r"[A-Z0-9]+", name.upper())
    if len(toks) > 1 and toks[0] in ("DEMO", "TEST", "RUN"):
        toks = toks[1:]
    base = "".join(toks) or "RUN"
    base = ("R" + base if not base[0].isalpha() else base).ljust(2, "X")
    sha = hashlib.sha1(name.encode()).hexdigest().upper()
    for k in [base[:8], *(base[:6] + sha[i:i + 2] for i in range(0, 30, 2))]:
        if k not in taken:
            return k
    raise ValueError(f"no free board key for {name}")


def _seq(d, nxt):
    """(stage ids in order, index of the current stage; len(seq) = finished). Stage ids: survey ... critic-1 replan-1 critic-2 ... panel-1 revise-1 panel-2."""
    events = [json.loads(x) for x in (d / "events.jsonl").read_text().splitlines()] if (d / "events.jsonl").exists() else []
    n_c, n_r = (sum(e["stage"] == s for e in events) for s in ("critic", "replan"))
    hist = d / "panel" / "history.jsonl"
    h = len(hist.read_text().splitlines()) if hist.exists() else int((d / "panel" / "scores.json").exists())
    user = nxt.startswith("USER:")
    stage = "pick" if "choose a candidate" in nxt else "autofreeze" if user and ("fix the design" in nxt or " freeze " in nxt) else "explore" if user else \
        (re.search(r"lab\.py ([\w-]+)", nxt) or [0, "explore"])[1]
    prereg = director.prereg_section(d) is not None
    rounds = max(n_r + (stage == "replan"), n_c - 1, n_c if stage == "critic" else 0)
    seq = ["survey", "read-field", *(["preregister"] if prereg else ["ideate", "screen", "novelty", "pick"]), "fetch-data", "plan", "critic-1"]
    for k in range(1, rounds + 1):
        seq += [f"replan-{k}", f"critic-{k + 1}"]
    seq += ["autofreeze", "explore", "analyze", "ablate", "final", "posthoc", "hypotheses", "discriminate", "write", "review", "paper", "panel-1"]
    for k in range(1, max(h - 1, 0) + (stage == "revise") + 1):
        seq += [f"revise-{k}", f"panel-{k + 1}"]
    seq.append("inspect")
    key = {"critic": f"critic-{n_c + 1}", "replan": f"replan-{n_r + 1}", "experiment": "explore", "freeze": "autofreeze", "revise": f"revise-{max(h, 1)}",
           "panel": "panel-1"}.get(stage, stage)
    return seq, len(seq) if nxt.startswith("DONE") else seq.index(key) if key in seq else 0


def _run(d):
    c, nxt = core.cfg(d), director.next_cmd(d)
    exp, q = Path(c["exp_dir"]), c["q"]
    seq, current = _seq(d, nxt)
    park = director._last_event(d, "park")
    reason = (park.get("reason") or park.get("msg") or "parked") if park else ""
    rows = [r for r in director._trials(exp, q) if r["status"] == "keep" and r["score"]]
    sign = -1 if _json(exp / "loop.json").get(q, {}).get("direction") == "min" else 1
    best = max(rows, key=lambda r: sign * float(r["score"])) if rows else {}
    critic = _json(d / "critique.json")
    facts = (f"- verdict: {director._verdict(exp, q) or 'pending'}\n"
             f"- dev best / baseline: {best.get('score', '—')} / {rows[0]['score'] if rows else '—'}\n"
             f"- critic blockers / majors: {critic.get('blockers', '—')} / {critic.get('majors', '—')}\n")
    return c, nxt, seq, current, facts, reason  # the director also handles waived stages (old runs, no-winner ablation, revision limit)


def _rel(p):
    try:
        return p.relative_to(core.ROOT).as_posix()  # same evidence text on Windows
    except ValueError:
        return str(p)


def _gates(d, c):
    """Per stage: [(criterion, artifact path, met)] from the same files director.next_cmd checks."""
    exp, q = Path(c["exp_dir"]), c["q"]
    crit = _json(d / "critique.json")
    clean = bool(crit) and not crit.get("blockers") and not any(x.get("severity") == "blocker" for x in crit.get("findings", []))
    f = lambda base, n: [(f"{n} exists", base / n, (base / n).exists())]
    return {"survey": f(d, "field.md"), "read-field": f(d, "field_papers.md"), "ideate": f(d, "candidates.json"), "preregister": f(d, "candidates.json"),
            "novelty": f(d, "novelty.json"), "pick": f(d, "pick.json"), "fetch-data": f(d, "data_plan.json"), "plan": f(exp, "PROTOCOL.md"),
            "critic": [("critique.json has 0 blockers", d / "critique.json", clean)],
            "autofreeze": [(f"loop.json has {q} (frozen)", exp / "loop.json", q in _json(exp / "loop.json"))],
            "explore": [("trials exist", exp / "results.tsv", bool(director._trials(exp, q)))],
            "analyze": [(f"holdout lock holdout_{q}.json exists", exp / ".rloop" / f"holdout_{q}.json", (exp / ".rloop" / f"holdout_{q}.json").exists())],
            "ablate": f(d, "ablations.md"), "final": f(d, "final_evaluation.json"), "posthoc": f(d, "posthoc/manifest.json"),
            "hypotheses": f(d, "hypotheses.json"), "discriminate": f(d, "discrimination.json"), "write": f(d, "RESULT.md"), "review": f(d, "REVIEW.md"),
            "paper": f(d, "paper/paper.tex"), "panel": f(d, "panel/scores.json"), "inspect": f(d, "index.html")}


def _links(board, rid, node=None):
    return f"\n\nKG: {board}/lab/kg/kg.html#{'node=' + urllib.parse.quote(node, safe=':') if node else 'run=' + urllib.parse.quote(rid)}\nLineage: {board}/lab/lineage.html#{urllib.parse.quote(rid)}"


def _cards():
    """Read-only extra cards: (external_id, title, description, status, labels)."""
    cards = []
    from lab import defects
    for r in defects.load():
        status = {"open": "review", "confirmed": "in_progress"}.get(r["status"], "done")
        cards.append((("defect:" + r["id"])[:64], f"Data defect {r['id']} [{r['status']}]",
                      f"{r.get('claim', '')}\n\nruns: {', '.join(r.get('runs', []))}\n{r.get('note', '')}", status, ["defect"], {"kind": "bug"}))
    text = (core.ROOT / "METHODS.md").read_text() if (core.ROOT / "METHODS.md").exists() else ""
    if text:
        sec = {k: [x for x in v.splitlines() if x.startswith("- ") and x != "- none"] for k, v in
               zip(("works", "dev", "fails"), re.split(r"\n(?:Dev only|Fails) \(", text.partition("## 다시 볼 단서")[0].partition("## Works / fails")[2]))}
        cand = text.count("| rule candidate |")
        cards.append(("methods:summary", "Methods ledger", f"{len(sec['works'])} works, {len(sec['dev'])} dev-only, {len(sec['fails'])} fails, {cand} rule candidates\n\n"
                      + "\n".join(sec["works"][:20]), "todo", ["methods"]))
    rv = [x for x in text.partition("## 다시 볼 단서")[2].splitlines() if x.startswith("- ") and x != "- none"]
    if rv:
        cards.append(("revisit:summary", "Leads to revisit", f"{len(rv)} clues, top 10\n\n" + "\n".join(rv[:10]), "review", ["revisit"]))
    combos = sorted((core.ROOT / "runs").glob("*/combos.json"), key=lambda p: p.stat().st_mtime)
    if combos:
        rows = json.loads(combos[-1].read_text())
        cards.append(("seeds:latest", f"Combination seeds: {combos[-1].parent.name} ({len(rows)})",
                      "\n".join(f"- {c['id']}: {c['method']} × {c['dataset']} × {c['metric']}" for c in rows[:30]), "todo", ["seeds"]))
    listing = core.ROOT / "memory" / "global" / "experiments.json"
    for folder in map(Path, _json(listing) if listing.exists() else []):
        if (folder / "RESULT.md").exists():
            verdict = next((x.strip() for x in (folder / "RESULT.md").read_text().splitlines() if x.strip()), "")
            flags = " | ".join(f"{n}: {'yes' if (folder / (n + '.md')).exists() else 'no'}" for n in ("PREREG", "NEEDS_DATA"))
            cards.append((f"experiment:{folder.name[:40]}:{hashlib.sha1(str(folder).encode()).hexdigest()[:8]}", folder.name,
                          f"{verdict}\n\n{flags}\n{folder}", "done", ["experiment"]))
        elif (folder / "NEEDS_DATA.md").exists():
            need = next((x.strip() for x in (folder / "NEEDS_DATA.md").read_text().splitlines() if x.strip()), "")
            cards.append((f"experiment:{folder.name[:40]}:{hashlib.sha1(str(folder).encode()).hexdigest()[:8]}", folder.name,
                          f"Data needed: {need}\n\n{folder}", "review", ["experiment"]))
    return cards


def kanban_sync(a):
    created = 0
    base = (getattr(a, "url", None) or _url()).rstrip("/")
    def api(method, path, body=None):
        request = urllib.request.Request(base + "/api/" + path,
                  data=None if body is None else json.dumps(body).encode(), method=method,
                  headers={"X-Agent": "researchlab", "Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=5) as response:
            return json.load(response)

    def soft(call):
        """Optional board features: a refusal (unsupported field/route, Director-only) skips the feature, not the sync."""
        try:
            return call()
        except urllib.error.HTTPError as exc:
            if exc.code not in (400, 403, 404, 405, 409):
                raise

    miles = {}
    def milestones(project):
        """Group milestones of a project (created once); empty when the board has none."""
        if project not in miles:
            have = soft(lambda: api("GET", f"projects/{project}/milestones"))
            if isinstance(have, dict):
                have = have.get("milestones", have.get("items"))
            miles[project] = set()
            if have is not None:
                miles[project] = {m["name"] for m in have}
                for g, stages in GROUPS:
                    if g not in miles[project] and soft(lambda: api("POST", f"projects/{project}/milestones", {"name": g, "description": stages.replace(" ", ", ")})):
                        miles[project].add(g)
        return miles[project]

    def sync(project, external, title, description, status, labels, parent=None, reason="", kind=None, criteria=None, review=None, after=None, group=None):
        nonlocal created
        external = re.sub(r"[^\w.:-]", "_", external)[:64]
        found = api("GET", "tasks?" + urllib.parse.urlencode({"project": project, "external_id": external}))
        found = [t for t in found if t["project"] == project and t.get("external_id") == external]
        fields = dict(title=title[:500], description=description, labels=labels, parent=parent)
        if kind:
            fields["type"] = kind
        if parent:
            fields["epic"] = parent
        if after is not None:
            fields["depends_on"] = after
        if group and group in milestones(project):
            fields["milestone"] = group
        if found:
            task = found[0]
            if task.get("assignee") not in (None, "", "researchlab"):
                raise ValueError(f"{task['key']} is held by another agent; stopped")
        else:
            task = api("POST", "tasks", dict(project=project, external_id=external, status="backlog" if status == "backlog" else "todo", priority="medium", **fields))
            created += 1
        key = task["key"]
        if task["project"] != project or not re.fullmatch(re.escape(project) + r"-\d+", key):
            raise ValueError(f"refusing a task outside {project}")
        path, old = "tasks/" + key, task["status"]
        if any(task.get(k) != v for k, v in fields.items()):
            task = api("PATCH", path, fields)
        if criteria is not None:  # [(text, met, evidence)]: ticked with the artifact as evidence
            texts = [t for t, _, _ in criteria]
            if [x["text"] for x in task.get("criteria", [])] != texts:
                task = soft(lambda: api("PATCH", path, {"criteria": texts})) or task
            have = {x["text"]: x for x in task.get("criteria", [])}
            for text, met, proof in criteria:
                if met != bool(have.get(text, {}).get("done")) or (met and have[text].get("evidence") != proof[:2000]):
                    soft(lambda: api("POST", path + "/criteria", {"items": [{"item": text, "evidence": proof[:2000]}] if met else [text], "done": met}))
        if review is not None and bool(task.get("needs_review")) != review:  # Director-only on a real board: skipped there
            soft(lambda: api("PATCH", path, {"needs_review": review}))
        if old != status and kind != "epic":  # epics are never claimed; the board derives their progress from children
            try:
                if status in ("todo", "backlog"):
                    task = api("POST", path + ("/release" if task.get("assignee") else "/move"), {"status": status})
                else:
                    if task.get("assignee") != "researchlab" or status == "in_progress":
                        task = api("POST", path + "/claim", {})
                    task = (api("POST", path + "/submit", {"summary": description, "verify": "Read ResearchLab artifacts; mirror only."})
                            if status == "review" else api("POST", path + "/move", {"status": "done"}) if status == "done" else task)
            finally:
                actual = api("GET", path)["status"]
                if actual != status:
                    print(f"{key}: requested {status}, kept {actual}")
                if old != actual:
                    api("POST", path + "/comments", {"body": f"ResearchLab: {old} → {actual}" + (f"\nParked: {reason}" if reason else "")})
        return key

    try:
        projects = api("GET", "projects")
        if not any(p["key"] == "LAB" for p in projects):
            projects.append(api("POST", "projects", dict(key="LAB", name="ResearchLab", description="Workspace-wide research state (programs, methods, data defects); each run has its own project")))
        raw = {p.parent.name: _json(p) for p in (core.ROOT / "runs").glob("*/run.json")}
        claimed = {r["board_key"] for r in raw.values() if r.get("board_key")}
        programs = [_json(p) for p in sorted((core.ROOT / "programs").glob("*/program.json"))]
        for p in programs:
            cell = lambda x: str(x).replace("|", "\\|").replace("\n", " ")
            board = ["| Thesis | Statement | Status | Supports | Refutes |", "|---|---|---|---|---|"]
            for t in p["theses"]:
                counts = [sum(e["direction"] == direction for e in t["evidence"]) for direction in ("supports", "refutes")]
                board.append("| " + " | ".join(map(cell, [t["id"], t["statement"], t["status"], *counts])) + " |")
            sync("LAB", "program:" + p["name"], p["name"], p["question"] + "\n\n" + "\n".join(board), "todo", ["program"])
        for config in sorted((core.ROOT / "runs").glob("*/run.json")):
            d = config.parent
            c, nxt, seq, current, facts, reason = _run(d)
            heading = next((line.lstrip("# ").strip() for line in (d / "brief.md").read_text().splitlines()
                            if re.match(r"^#{1,6}\s", line)), d.name)
            pkey = raw[d.name].get("board_key") or next((p["key"] for p in projects if p.get("name") == d.name and p["key"] != "LAB"), None)
            if not pkey:
                pkey = _board_key(d.name, claimed | {p["key"] for p in projects} | {"LAB"})
            exists = next((p for p in projects if p["key"] == pkey), None)
            if exists and exists.get("name") != d.name:
                raise ValueError(f"board key {pkey} belongs to {exists.get('name')}, not run {d.name}; stopped")
            if not exists:
                projects.append(api("POST", "projects", dict(key=pkey, name=d.name, description=heading)))
            if raw[d.name].get("board_key") != pkey:  # the key stays fixed once chosen
                claimed.add(pkey)
                config.write_text(json.dumps(raw[d.name] | {"board_key": pkey}, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
            names = sorted({p["name"] for p in programs if d.name in p.get("runs", [])} | ({c["program"]} if c.get("program") else set()))
            user = nxt.startswith("USER:")
            status = "done" if current >= len(seq) else "review" if reason or user else "in_progress"
            verdict = director._verdict(Path(c["exp_dir"]), c["q"])
            labels = ["run", *names, c["q"], *([f"verdict:{verdict}"] if verdict else []), "provider:" + str(c.get("provider", "")).replace(",", " ")[:40],
                      *(["tagged"] if c.get("tagged") else [])]  # frozen with unresolved findings
            parent = sync(pkey, "run:" + d.name, f"{d.name}: {heading}", facts + "\nNext: " + nxt + _links(base, d.name), status, labels, kind="epic")
            gates, prev = _gates(d, c), None
            last = lambda name: max((i for i, s in enumerate(seq[:current + 1]) if s.startswith(name + "-")), default=-1)  # criteria sit on the latest round
            for i, sid in enumerate(seq[:current + 3]):
                stage = re.sub(r"-\d+$", "", sid)
                n = sid[len(stage) + 1:]
                state = "done" if i < current else ("review" if reason or user else "in_progress") if i == current else "todo"
                parked = reason if i == current else ""
                wanted = gates.get(stage, []) if stage not in ROUNDS or i == last(stage) else []
                crit = [(t, met or state == "done", _rel(path) if met else f"waived (stage passed by the director): {_rel(path)}") for t, path, met in wanted]
                title = stage.replace("-", " ").capitalize() + (f" round {n}" if stage in ROUNDS else f" {n}" if n else "")
                text = f"Stage {sid} of run {d.name}." + (f"\nNext: {nxt}" if i == current else "") + (f"\nParked: {parked}" if parked else "")
                prev = sync(pkey, f"run:{d.name}:{sid}", title, text + _links(base, d.name), state, [f"group:{GROUP[stage].lower()}", *(["parked"] if parked else [])],
                            parent, parked, kind=KIND.get(stage, "task"), criteria=crit or None, review=i == current and user, after=None if prev is None else [prev],
                            group=GROUP[stage])
            seen_file = d / "kanban_seen.json"
            seen = _json(seen_file)
            picked = _json(d / "pick.json")
            for x in _json(d / "candidates.json"):
                won = bool(picked) and (picked.get("id") == x["id"] or picked.get("title") == x.get("title"))
                sync(pkey, f"run:{d.name}:cand:{x['id']}", f"{x['id']}: {x.get('title', '')}",
                     f"value {x.get('value', '?')}\n\n{x.get('hypothesis') or x.get('description') or ''}" + _links(base, d.name, f"cand:{d.name}:{x['id']}"),
                     "done" if won else "backlog", ["candidate", "group:ideas"], parent, kind="spike", group="Ideas")
            for x in _json(d / "combos.json"):
                sync(pkey, f"run:{d.name}:combo:{x['id']}", f"{x['id']}: {x['method']} × {x['dataset']} × {x['metric']}",
                     "KG combination seed (lab/combine.py)" + _links(base, d.name), "backlog", ["combine", "group:ideas"], parent, kind="spike", group="Ideas")
            bugs = {x.get("id", "?"): [f"{x.get('id', '?')}: {(x.get('claim') or x.get('evidence') or '')[:100]}", x.get("claim") or x.get("evidence") or "",
                                       [x["severity"], *([x["defect"]] if x.get("defect") else [])]]
                    for x in _json(d / "critique.json").get("findings", []) if x.get("severity") in ("blocker", "major")}
            for fid in {*bugs, *seen.get("bugs", {})}:
                title, text, tags = bugs.get(fid) or seen["bugs"][fid]
                sync(pkey, f"run:{d.name}:bug:{fid}", title, (text if fid in bugs else text + "\n\nResolved: no longer in critique.json") + _links(base, d.name, f"finding:{d.name}:{fid}"),
                     "todo" if fid in bugs else "done", [*tags, "group:design"], parent, kind="bug", group="Design")
            lines = (d / "events.jsonl").read_text().splitlines() if (d / "events.jsonl").exists() else []
            for line in lines[seen.get("events", 0):][-20:]:  # first sync of an old run: latest 20 only
                e = json.loads(line)
                soft(lambda: api("POST", f"tasks/{parent}/comments", {"body": f"{e.get('utc', '')} [{e['stage']}] {e['msg']}"[:2000]}))
            state = {"events": len(lines), "bugs": bugs}
            if state != seen:
                seen_file.write_text(json.dumps(state, ensure_ascii=False) + "\n")
        for card in _cards():
            sync("LAB", *card[:5], **(card[5] if len(card) > 5 else {}))
    except urllib.error.HTTPError as exc:
        print(f"Kanban blocked (HTTP {exc.code}): {exc.read().decode()}; stopped without bypassing API restrictions.")
    except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
        print(f"Kanban unavailable: {exc}; sync stopped without error.")
    except ValueError as exc:
        print(f"Kanban stopped: {exc}")
    print(f"Board tasks created: {created}")
    return created


def mirror():
    """Quiet sync after a state change; LAB_KANBAN_AUTO=0 turns it off (selftests, offline runs)."""
    if os.environ.get("LAB_KANBAN_AUTO", "1") != "1":
        return
    import contextlib, io
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            kanban_sync(None)
    except Exception as e:  # the board is a mirror: a sync bug must never stop a campaign
        print(f"[kanban] sync skipped: {type(e).__name__}: {e}")
        return
    lines = [ln for ln in out.getvalue().splitlines() if not ln.startswith("Board tasks created: 0")]
    if lines:
        print("[kanban] " + "; ".join(lines))


def refresh():
    """After a state change: board, method ledger and lineage page follow (LAB_KANBAN_AUTO=0 turns all off)."""
    if os.environ.get("LAB_KANBAN_AUTO", "1") != "1":
        return
    import contextlib, io
    from lab import defects, lineage, methods
    mirror()
    with contextlib.redirect_stdout(io.StringIO()):
        methods.scan(); methods.review(); defects.sync(); lineage.lineage(None)
        from lab import director, predict
        for f in (core.ROOT / "runs").glob("*/pick.json"):  # close the loop: verdicts resolve recorded predictions
            c = core.cfg(f.parent)
            v = director._verdict(Path(c["exp_dir"]), c["q"]) if c.get("exp_dir") else None
            if v:
                predict.resolve(f.parent, v)


def selftest():
    import tempfile
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from types import SimpleNamespace
    from unittest.mock import patch
    tasks, comments, projects, miles = {}, [], [], {}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def handle_api(self):
            assert self.headers["X-Agent"] == "researchlab" and not self.headers.get("X-Role")
            path = urllib.parse.urlsplit(self.path)
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            ms = re.fullmatch(r"/api/projects/([A-Z0-9]+)/milestones", path.path)
            if path.path == "/api/projects":
                if self.command == "POST":
                    assert re.fullmatch(r"[A-Z][A-Z0-9]{1,9}", body["key"]) and body["name"] and not any(p["key"] == body["key"] for p in projects)
                    projects.append(body); miles[body["key"]] = []
                result = body if self.command == "POST" else projects
            elif ms:
                if self.command == "POST":
                    miles[ms[1]].append(dict(body, id=len(miles[ms[1]]) + 1))
                result = miles[ms[1]]
            elif path.path == "/api/tasks":
                if self.command == "GET":
                    query = urllib.parse.parse_qs(path.query)
                    assert query["project"][0] in miles
                    result = [t for t in tasks.values() if t["project"] == query["project"][0] and t["external_id"] in query["external_id"]]
                else:
                    assert body["project"] in miles and re.fullmatch(r"[A-Za-z0-9._:-]{1,64}", body["external_id"])
                    if "milestone" in body:
                        assert body["milestone"] in [m["name"] for m in miles[body["project"]]]
                    result = dict(body, key=f"{body['project']}-{sum(t['project'] == body['project'] for t in tasks.values()) + 1}", assignee=None)
                    tasks[result["key"]] = result
            else:
                key, *action = path.path.removeprefix("/api/tasks/").split("/")
                result = tasks[key]
                assert result["project"] in miles and key.startswith(result["project"] + "-")
                if action == ["claim"]:
                    assert result["status"] != "done"
                    result.update(status="in_progress", assignee="researchlab")
                elif action == ["submit"]:
                    assert result["assignee"] == "researchlab"
                    result["status"] = "review"
                elif action == ["comments"]:
                    comments.append(dict(body, key=key))
                elif action == ["criteria"]:
                    for item in body["items"]:
                        crit = next(x for x in result["criteria"] if x["text"] == (item["item"] if isinstance(item, dict) else item))
                        crit.update(done=body.get("done", True), **({"evidence": item["evidence"]} if isinstance(item, dict) else {}))
                elif self.command in ("POST", "PATCH"):
                    result.update(dict(body, criteria=[dict(text=t, done=False, owner="agent") for t in body["criteria"]]) if "criteria" in body else body)
            self.send_response(200); self.end_headers()
            self.wfile.write(json.dumps(result).encode())
        do_GET = do_POST = do_PATCH = handle_api
    with tempfile.TemporaryDirectory() as tmp, HTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            root = Path(tmp); d = root / "runs" / "test"; d.mkdir(parents=True)
            core.save_cfg(d, dict(exp_dir=str(d / "exp"), provider="cmd:python3 fake.py"))
            (d / "brief.md").write_text("preface\n# Test question\n")
            a = SimpleNamespace(url=f"http://127.0.0.1:{server.server_port}")
            with patch.object(core, "ROOT", root), patch.object(core, "invoke", side_effect=AssertionError("no model calls")):
                assert kanban_sync(a) == 4  # epic + survey, read-field, ideate (current + next two)
                count = len(comments)
                assert kanban_sync(a) == 0 and len(comments) == count
                core.event(d, "park", "design failed", reason="design failed")
                assert kanban_sync(a) == 0
                child = next(t for t in tasks.values() if t["external_id"] == "run:test:survey")
                assert child["status"] == "review" and child["labels"] == ["group:ideas", "parked"] and child["project"] == "TEST" and child["milestone"] == "Ideas"
                assert sum("Parked: design failed" in c["body"] for c in comments) == 1
                assert {p["key"]: p["name"] for p in projects} == {"LAB": "ResearchLab", "TEST": "test"} and projects[1]["description"] == "Test question"
                assert [m["name"] for m in miles["TEST"]] == [g for g, _ in GROUPS] and json.loads((d / "run.json").read_text())["board_key"] == "TEST" and len(tasks) == 4
                (d / "events.jsonl").unlink()
                with patch.object(director, "next_cmd", return_value="DONE (test)"):
                    assert kanban_sync(a) == 19  # all 22 stages of a plain run, 3 existed
                    assert kanban_sync(a) == 0 and all(t["status"] == "done" for t in tasks.values() if t.get("type") != "epic")
                    kinds = {t["external_id"].split(":")[-1]: t["type"] for t in tasks.values() if t["project"] == "TEST"}
                    assert [kinds[s] for s in ("survey", "fetch-data", "plan", "critic-1", "explore", "write", "panel-1")] == ["spike", "chore", "feature", "task", "feature", "task", "task"]
                    assert {t["project"] for t in tasks.values()} == {"TEST"}
                with patch.object(director, "next_cmd", return_value="DONE (test)"):
                    exp = root / "exp_a"; exp.mkdir()
                    (exp / "RESULT.md").write_text("\nverdict: negative\nbody\n"); (exp / "PREREG.md").write_text("x")
                    need = root / "exp_b"; need.mkdir(); (need / "NEEDS_DATA.md").write_text("\nmonthly panel missing\n")
                    (root / "memory" / "global").mkdir(parents=True)
                    (root / "memory" / "global" / "experiments.json").write_text(json.dumps([str(exp), str(need), str(root / "missing")]))
                    (root / "memory" / "global" / "data_defects.json").write_text(json.dumps(
                        [dict(id=f"D-{s}", status=s, claim="c " + s, runs=["r"]) for s in ("open", "confirmed", "fixed", "rejected")]))
                    (root / "METHODS.md").write_text("## Works / fails\n\nWorks (x):\n- w1\n\nDev only (kept):\n- none\n\nFails (y):\n- f1\n- f2\n\n## 다시 볼 단서\n\n- [small] g6: +16.67 [3.98, 32.06] -> conditional\n")
                    (d / "combos.json").write_text(json.dumps([dict(id="C1", method="m", dataset="d", metric="x")]))
                    before = len(tasks)
                    assert kanban_sync(a) == 10 and len(tasks) == before + 10  # 9 summary cards + the run's combine spike
                    by = {t["external_id"]: t for t in tasks.values()}
                    assert [by["defect:D-" + s]["status"] for s in ("open", "confirmed", "fixed", "rejected")] == ["review", "in_progress", "done", "done"]
                    assert "1 works, 0 dev-only, 2 fails, 0 rule" in by["methods:summary"]["description"] and by["methods:summary"]["labels"] == ["methods"]
                    assert by["revisit:summary"]["labels"] == ["revisit"] and by["revisit:summary"]["status"] == "review" and "conditional" in by["revisit:summary"]["description"]
                    assert by["seeds:latest"]["labels"] == ["seeds"] and "C1: m × d × x" in by["seeds:latest"]["description"]
                    ex = next(t for t in tasks.values() if t["labels"] == ["experiment"])
                    assert ex["title"] == "exp_a" and ex["description"].startswith("verdict: negative") and "PREREG: yes | NEEDS_DATA: no" in ex["description"]
                    nd = by[next(k for k in by if k.startswith("experiment:exp_b"))]
                    assert nd["description"].startswith("Data needed: monthly panel missing") and nd["status"] == "review"
                    assert kanban_sync(a) == 0
                d2, exp2 = root / "runs" / "test2", root / "exp2"
                d2.mkdir(); exp2.mkdir(); (exp2 / "PROTOCOL.md").write_text("p")
                core.save_cfg(d2, dict(exp_dir=str(exp2), provider="cmd:python3 fake.py")); (d2 / "brief.md").write_text("# Second\n")
                (d2 / "candidates.json").write_text(json.dumps([dict(id="C1", title="a"), dict(id="C2", title="b")]))
                (d2 / "pick.json").write_text(json.dumps(dict(id="C1", title="a")))
                (d2 / "combos.json").write_text(json.dumps([dict(id="K1", method="m", dataset="d", metric="x")]))
                (d2 / "critique.json").write_text(json.dumps(dict(blockers=1, findings=[dict(id="F1", severity="blocker", evidence="bad", defect="D-1"), dict(id="F2", severity="minor")])))
                core.event(d2, "novelty", "checked"); core.event(d2, "pick", "chosen")
                with patch.object(director, "next_cmd", side_effect=lambda x: "USER: choose a candidate, then `./lab.py pick test2 C1`" if x.name == "test2" else "DONE (test)"):
                    assert kanban_sync(a) == 13  # epic + 8 stage cards (survey .. plan) + 2 candidates + 1 seed + 1 bug
                    by = {t["external_id"]: t for t in tasks.values()}
                    ep, pick, fetch = by["run:test2"], by["run:test2:pick"], by["run:test2:fetch-data"]
                    assert ep["description"].endswith(f"\nLineage: {a.url}/lab/lineage.html#test2") and f"{a.url}/lab/kg/kg.html#run=test2\n" in ep["description"]
                    assert ep["type"] == "epic" and ep["project"] == "TEST2" and ep["labels"][:2] == ["run", "Q1"] and ep["labels"][2].startswith("provider:cmd:python3")
                    assert pick["needs_review"] is True and pick["parent"] == pick["epic"] == ep["key"] and fetch["depends_on"] == [pick["key"]] and pick["status"] == "review"
                    assert (pick["type"], pick["milestone"], pick["labels"], fetch["type"], fetch["milestone"], fetch["labels"]) == ("spike", "Ideas", ["group:ideas"], "chore", "Design", ["group:design"])
                    assert [by["run:test2:" + s]["status"] for s in ("survey", "ideate", "pick", "fetch-data", "plan")] == ["done", "done", "review", "todo", "todo"] and "run:test2:critic-1" not in by
                    assert [(x["text"], x["done"], x.get("evidence")) for x in by["run:test2:ideate"]["criteria"]] == [("candidates.json exists", True, "runs/test2/candidates.json")]
                    assert [(x["text"], x["done"], x.get("evidence")) for x in pick["criteria"]] == [("pick.json exists", True, "runs/test2/pick.json")]
                    nov = by["run:test2:novelty"]["criteria"][0]
                    assert nov["done"] and nov["evidence"].startswith("waived") and [x["done"] for x in fetch["criteria"]] == [False] and not fetch.get("needs_review")
                    assert by["run:test2:plan"]["type"] == "feature" and by["run:test2:plan"]["criteria"][0]["text"] == "PROTOCOL.md exists" and by["run:test2:plan"]["criteria"][0]["done"]
                    assert json.loads((d2 / "run.json").read_text())["board_key"] == "TEST2" and {p["key"] for p in projects} == {"LAB", "TEST", "TEST2"}
                    assert [(by["run:test2:cand:" + c]["type"], by["run:test2:cand:" + c]["status"]) for c in ("C1", "C2")] == [("spike", "done"), ("spike", "backlog")]
                    assert "kg.html#node=cand:test2:C1\n" in by["run:test2:cand:C1"]["description"] and "kg.html#node=finding:test2:F1\n" in by["run:test2:bug:F1"]["description"]
                    assert by["run:test2:combo:K1"]["labels"] == ["combine", "group:ideas"] and by["run:test2:combo:K1"]["status"] == "backlog"
                    core.save_cfg(d2, core.cfg(d2) | dict(tagged=True)); kanban_sync(a)  # frozen with unresolved findings: label `tagged`
                    assert {x["external_id"]: x for x in tasks.values()}["run:test2"]["labels"][-1] == "tagged"
                    assert by["run:test2:bug:F1"]["type"] == "bug" and by["run:test2:bug:F1"]["labels"] == ["blocker", "D-1", "group:design"] and "run:test2:bug:F2" not in by
                    assert by["defect:D-open"]["type"] == "bug"
                    posted = lambda text: sum(c["key"] == ep["key"] and text in c["body"] for c in comments)
                    assert posted("[novelty] checked") == posted("[pick] chosen") == 1
                    count = len(comments)
                    assert kanban_sync(a) == 0 and len(comments) == count
                    (d2 / "critique.json").write_text(json.dumps(dict(blockers=0, findings=[])))
                    core.event(d2, "x", "later")
                    assert kanban_sync(a) == 0 and by["run:test2:bug:F1"]["status"] == "done"
                    assert posted("[novelty] checked") == posted("[x] later") == 1
                held = by["run:test2"]; held["assignee"] = "someone"
                with patch("builtins.print") as pr, patch.object(director, "next_cmd", return_value="DONE (test)"):
                    assert kanban_sync(a) == 0
                    assert any("held by another agent" in str(c.args[0]) for c in pr.call_args_list)
                held["assignee"] = None
                dd = root / "runs" / "seq"; dd.mkdir(); core.save_cfg(dd, dict(exp_dir=str(dd / "exp"))); (dd / "brief.md").write_text("# S\n")
                for stage in ("critic", "replan", "critic"):
                    core.event(dd, stage, "x")
                seq, cur = _seq(dd, "./lab.py replan seq")
                assert seq[cur] == "replan-2" and seq[cur + 1] == "critic-3" and seq.index("critic-2") < cur
                seq, cur = _seq(dd, "./lab.py critic seq")
                assert seq[cur] == "critic-3" and seq.count("critic-3") == 1
                (dd / "panel").mkdir(); (dd / "panel" / "history.jsonl").write_text("{}\n")
                seq, cur = _seq(dd, "./lab.py revise seq")
                assert seq[cur] == "revise-1" and seq[cur + 1] == "panel-2" and seq[-1] == "inspect"
                (dd / "brief.md").write_text("# S\n## Pre-registered design\nx\n")
                assert "preregister" in _seq(dd, "./lab.py plan seq")[0] and "ideate" not in _seq(dd, "./lab.py plan seq")[0]
                assert _board_key("demo-gerber", set()) == "GERBER" and _board_key("common-scale-04", set()) == "COMMONSC" and _board_key("2024-x", set()) == "R2024X"
                alt = _board_key("common-scale-04", {"COMMONSC"})
                assert alt != "COMMONSC" and alt.startswith("COMMON") and len(alt) == 8 and alt == _board_key("common-scale-04", {"COMMONSC"}) and _board_key("a", set()) == "AX"
                with patch.object(urllib.request, "urlopen", side_effect=urllib.error.URLError("offline")):
                    assert kanban_sync(a) == 0
                (root / "kanban").mkdir(); (root / "kanban" / "pid").write_text("1"); (root / "kanban" / "port").write_text("8791")
                seen = []
                with patch.dict(os.environ, {k: v for k, v in os.environ.items() if k != "LAB_KANBAN_URL"}, clear=True), \
                        patch.object(urllib.request, "urlopen", side_effect=lambda r, **kw: seen.append(r.full_url) or (_ for _ in ()).throw(urllib.error.URLError("x"))):
                    assert kanban_sync(SimpleNamespace()) == 0 and seen and all(u.startswith("http://127.0.0.1:8791/") for u in seen)
        finally:
            server.shutdown(); thread.join()
    print("kanban selftest ok")
    from lab import kanban_up
    kanban_up.selftest()
