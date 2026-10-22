"""Revise a manuscript against surviving panel concerns, then review it again."""
import json
import re
import tempfile
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

from lab import core, roles, panel, paper, testkit
from lab.report import _inputs
from lab.util import read as _read


def revise(a):
    d = core.run_dir(a.run)
    c, p, out = core.cfg(d), d / "paper", d / "panel"
    if not (p / "paper.tex").exists() or not (out / "scores.json").exists():
        raise ValueError("revise requires paper/paper.tex and panel/scores.json")
    history = out / "history.jsonl"
    past = [json.loads(x) for x in history.read_text().splitlines()] if history.exists() else []
    start = max((x["round"] for x in past), default=0)
    for n in range(start + 1, start + 1 + getattr(a, "rounds", 2)):
        scores = json.loads((out / "scores.json").read_text())
        if scores.get("meta_decision") == "accept" and not scores.get("surviving_major"):
            break
        src = _inputs(d, c)
        fixed = {name: (p / name).read_bytes() for name in ("numbers.tex", "trials.tex")}
        instr = core.prompt("revise", paper=(p / "paper.tex").read_text(), meta=_read(out / "META.md"),
                            surviving=json.dumps(scores.get("surviving_major", []), ensure_ascii=False),
                            reviews="\n\n".join(f"## {f.name}\n{f.read_text()}" for f in sorted(out.glob("R*.md"))),
                            ledger=src["results"], numbers=fixed["numbers.tex"].decode(),
                            trials=fixed["trials.tex"].decode())
        try:
            roles.call("revise", instr, cwd=p, run=d, write=True)
        finally:
            changed = [name for name, data in fixed.items() if not (p / name).exists() or (p / name).read_bytes() != data]
            for name in changed:
                (p / name).write_bytes(fixed[name])
        if changed:
            raise ValueError(f"revision changed generated files: {', '.join(changed)} (restored)")
        tex = (p / "paper.tex").read_text()
        body = re.sub(r"(?m)(?<!\\)%.*$|\\input\{[^}]*\}|\\includegraphics\[[^\]]*\]|\\usepackage\[[^\]]*\]", "", tex)
        known = "\n".join([*src.values(), *(v.decode() for v in fixed.values())])
        miss = sorted({v for v in re.findall(r"\d+\.\d+", body) if v not in known})
        for v in miss:
            print(f"WARN: {v} in paper.tex is not in the known evidence")
        ok, how = paper._compile(p)
        panel.panel(a)
        scores = json.loads((out / "scores.json").read_text())
        row = dict(round=n, mean_overall=scores.get("mean_overall"), decision=scores["meta_decision"],
                   surviving=len(scores.get("surviving_major", [])))
        with history.open("a") as fh:
            fh.write(json.dumps(row) + "\n")
        core.event(d, "revise", f"round {n}: {row['decision']}; compile {ok}: {how}",
                   **row, unmatched=miss, compiled=ok)


FAKE = r'''import json, pathlib, sys
s = sys.stdin.read().split(chr(10)*2 + "---" + chr(10)*2, 1)[-1]
if s.startswith('TASK: revise'):
    assert all(x in s for x in ('missing control', 'R1.md', 'LEDGER.md', '0.812'))
    p = pathlib.Path('paper.tex')
    p.write_text(p.read_text().replace('unaddressed', 'addressed') + '\nResponse to reviewers: no control available.\n')
elif s.startswith('TASK: panel meta'):
    print(json.dumps(dict(decision='accept', surviving_major=[], dismissed=[])))
else:
    print(json.dumps(dict(soundness=3, significance=3, clarity=3, overall=6, confidence=4,
                         major=[], minor=[], decision='accept')))
'''


def selftest():
    with tempfile.TemporaryDirectory() as t:
        d = Path(t)
        for name in ("exp", "paper", "panel"):
            (d / name).mkdir()
        fake = testkit.fake(d, FAKE)
        core.save_cfg(d, dict(exp_dir=str(d / "exp"), provider=fake, reviewer=fake,
                              panel=[[fake, None, "methodologist"], [fake, None, "skeptic"]]))
        (d / "exp/results.tsv").write_text("score\n0.812\n")
        (d / "paper/paper.tex").write_text("unaddressed 0.812; unsupported 9.99")
        (d / "paper/numbers.tex").write_text("0.812")
        (d / "paper/trials.tex").write_text("0.812")
        (d / "panel/scores.json").write_text('{"meta_decision":"revise","surviving_major":["missing control"]}')
        for name in ("META.md", "R1.md", "R2.md"):
            (d / "panel" / name).write_text("missing control")
        a = Namespace(run=t, rounds=2)
        with patch.object(paper, "_compile", return_value=(None, "offline test")) as compile_:
            revise(a)
            revise(a)
            assert compile_.call_count == 1
        rows = [json.loads(x) for x in (d / "panel/history.jsonl").read_text().splitlines()]
        assert rows == [dict(round=1, mean_overall=6.0, decision="accept", surviving=0)], rows
        ev = json.loads((d / "events.jsonl").read_text().splitlines()[-1])
        assert ev["unmatched"] == ["9.99"] and ev["stage"] == "revise", ev
        assert (d / "paper/numbers.tex").read_text() == "0.812"
        assert "Response to reviewers" in (d / "paper/paper.tex").read_text()
        (d / "panel/scores.json").write_text('{"meta_decision":"revise","surviving_major":["missing control"]}')
        (d / "fake.py").write_text(FAKE.replace("surviving_major=[]", "surviving_major=['missing control']"))
        with patch.object(paper, "_compile", return_value=(False, "offline failure")) as compile_:
            revise(a)
            assert compile_.call_count == 2
        rows = [json.loads(x) for x in (d / "panel/history.jsonl").read_text().splitlines()]
        assert [r["round"] for r in rows] == [1, 2, 3] and rows[-1]["surviving"] == 1
        (d / "fake.py").write_text("open('numbers.tex', 'w').write('tampered')")
        try:
            revise(a)
            raise AssertionError("expected generated-file protection")
        except ValueError as e:
            assert "restored" in str(e)
        assert (d / "paper/numbers.tex").read_text() == "0.812"
    print("revise selftest ok")
