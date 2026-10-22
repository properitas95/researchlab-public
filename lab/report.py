"""report: RESULT.md written from the ledger, numbers checked by code; REVIEW.md by a second model."""
import re
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

from lab import core, roles, testkit
from lab.util import read as _read


def _inputs(d, c):
    exp = Path(c["exp_dir"])
    return {"protocol": _read(exp / "PROTOCOL.md"), "results": core.ledger(d, exp).read_text(),
            "decisions": _read(exp / "DECISIONS.md")}


def write(a):
    d = core.run_dir(a.run)
    c = core.cfg(d)
    src = _inputs(d, c)
    text = roles.call("write", core.prompt("write", pick=_read(d / "pick.json"), **src), cwd=d, run=d, write=False).strip() + "\n"
    known = "\n".join(src.values())
    miss = sorted({n for n in re.findall(r"\d+\.\d+", text) if n not in known})
    tags = c.get("tagged") and c.get("tags")
    if tags:  # frozen with unresolved findings: the reader must see them next to the numbers
        text += "\n## Caveats (unresolved at freeze)\n\n" + "\n".join(f"- `{t['id']}` ({t['severity']}): {t['claim']}" for t in tags) + "\n"
    (d / "RESULT.md").write_text(text)
    for n in miss:
        print(f"WARN: {n} in RESULT.md is not in results.tsv, DECISIONS.md or PROTOCOL.md")
    core.event(d, "write", f"RESULT.md {len(text)} chars, {len(miss)} unmatched numbers", unmatched=miss)


def review(a):
    d = core.run_dir(a.run)
    c = core.cfg(d)
    src = _inputs(d, c)
    text = roles.call("review", core.prompt("review", result=_read(d / "RESULT.md"), ledger=src["results"],
                                                  decisions=src["decisions"], protocol=src["protocol"]), cwd=d, run=d, write=False).strip() + "\n"
    (d / "REVIEW.md").write_text(text)
    v = re.findall(r"^\s*VERDICT:\s*(holds|narrow|withdraw)\b", text, re.M | re.I)
    core.event(d, "review", f"reviewer {c['reviewer']}: {v[-1].lower() if v else 'unparsed'}",
               verdict=v[-1].lower() if v else None)


FAKE = '''import sys
s = sys.stdin.read()
if "TASK: write" in s:
    assert "0.812" in s
    print("질문. 성능 0.812 (#9), 홀드아웃 0.774 (holdout #12). 잘못된 수 9.99 (#3).")
elif "TASK: review" in s:
    assert "9.99" in s
    print("| claim | supported by (trial #) | problem | severity |\\n| 9.99 | none | not in ledger | major |\\n\\nVERDICT: narrow")
'''


def selftest():
    with tempfile.TemporaryDirectory() as t:
        d, exp = Path(t) / "r", Path(t) / "r" / "exp"
        exp.mkdir(parents=True)
        testkit.fake(d, FAKE)
        (d / "pick.json").write_text('{"id": "C1"}')
        (exp / "PROTOCOL.md").write_text("protocol\n")
        (exp / "results.tsv").write_text("trial\tscore\n9\t0.812\n12\t0.774\n")
        (exp / "DECISIONS.md").write_text("Q1 **keep**\n")
        core.save_cfg(d, {"id": "t", "exp_dir": str(exp), "provider": "cmd:python3 fake.py",
                          "reviewer": "cmd:python3 fake.py", "search_cmd": "echo fake-results {q}"})
        a = SimpleNamespace(run=str(d))
        write(a)
        ev = (d / "events.jsonl").read_text()
        assert "0.812" in (d / "RESULT.md").read_text() and '"unmatched": ["9.99"]' in ev, ev
        assert "Caveats" not in (d / "RESULT.md").read_text()
        core.save_cfg(d, core.cfg(d) | dict(tagged=True, tags=[dict(id="min-delta-unset", severity="major", claim="MIN_DELTA is None.")]))
        write(a)
        assert "## Caveats (unresolved at freeze)\n\n- `min-delta-unset` (major): MIN_DELTA is None." in (d / "RESULT.md").read_text()
        review(a)
        assert (d / "REVIEW.md").exists() and '"verdict": "narrow"' in (d / "events.jsonl").read_text()
    print("report selftest ok")


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    selftest()
