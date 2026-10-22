"""paper: figures and numbers.tex straight from the ledger, then the model writes paper.tex; numbers checked, TeX compiled."""
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

from lab import core, roles, testkit
from lab.report import _inputs
from lab.util import read as _read

FIGS = '''import csv, json, sys
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
tsv, q, dirn, out = sys.argv[1:5]
rows = [r for r in csv.DictReader(open(tsv), delimiter="\\t") if r["q"] == q and r["score"] != ""]
dev = [r for r in rows if r["status"] != "holdout"]
sign = 1 if dirn == "max" else -1
fig, ax = plt.subplots(figsize=(5, 3))
for st, mk in (("keep", "o"), ("discard", "x"), ("crash", "s"), ("invalid", "^")):
    pts = [(int(r["n"]), float(r["score"])) for r in dev if r["status"] == st]
    if pts:
        ax.scatter(*zip(*pts), marker=mk, label=st)
run, line = None, []
for r in dev:
    if r["status"] == "keep" and (run is None or sign * float(r["score"]) > sign * run):
        run = float(r["score"])
    if run is not None:
        line.append((int(r["n"]), run))
if line:
    ax.step(*zip(*line), where="post", color="k", lw=1, label="running best")
ax.set_xlabel("trial"); ax.set_ylabel("dev score"); ax.legend(fontsize=7)
fig.tight_layout(); fig.savefig(out + "/fig_trials.pdf")
hold = [r for r in rows if r["status"] == "holdout"]
if hold:
    base = next(r["n"] for r in dev if r["status"] == "keep")
    fig, ax = plt.subplots(figsize=(3, 3))
    ax.bar(["baseline" if r["desc"].split("#")[1].split(" ")[0] == base else "best" for r in hold],
           [float(r["score"]) for r in hold], color="0.5")
    ax.set_ylabel("holdout score"); fig.tight_layout(); fig.savefig(out + "/fig_holdout.pdf")
'''
NOMPL = "no python with matplotlib found: set figure_python in run.json; figures skipped"


def _rows(exp, q):
    f = exp / "results.tsv"
    if not f.exists():
        return []
    head, *lines = f.read_text().splitlines()
    return [r for r in (dict(zip(head.split("\t"), ln.split("\t"))) for ln in lines if ln) if r["q"] == q]


def _numbers(exp, q):
    rs = _rows(exp, q)
    lj = json.loads(_read(exp / "loop.json")) if (exp / "loop.json").exists() else {}
    sign = -1 if lj.get(q, {}).get("direction") == "min" else 1
    dev = [r for r in rs if r["status"] != "holdout"]
    keep = [r for r in dev if r["status"] == "keep" and r["score"]]
    base = keep[0] if keep else None
    best = max(keep, key=lambda r: sign * float(r["score"])) if keep else None
    hold = {("base" if base and re.search(rf"holdout of #{base['n']} ", r["desc"]) else "best"): r["score"]
            for r in rs if r["status"] == "holdout" and r["score"]}
    if best and base and best["n"] == base["n"] and "best" in hold:
        hold["base"] = hold["best"]
    v = {"BaselineDev": base and base["score"], "BestDev": best and best["score"], "BestTrial": best and best["n"],
         "HoldoutBest": hold.get("best"), "HoldoutBase": hold.get("base"), "NTrials": len(dev)}
    v |= {f"N{s.capitalize()}": sum(r["status"] == s for r in dev) for s in ("keep", "discard", "crash", "invalid")}
    return "".join(f"\\newcommand{{\\{k}}}{{{x}}}\n" for k, x in v.items() if x not in (None, ""))


def _python(c):
    for p in filter(None, [c.get("figure_python"), sys.executable, shutil.which("python3"), "/opt/homebrew/bin/python3"]):
        if subprocess.run([p, "-c", "import matplotlib"], capture_output=True).returncode == 0:
            return p


def _compile(p):
    nm = ["-interaction=nonstopmode", "paper.tex"]
    cmds = ([["tectonic", "paper.tex"]] if shutil.which("tectonic") else [["latexmk", "-pdf", *nm]]
            if shutil.which("latexmk") else [["pdflatex", *nm]] * 2 if shutil.which("pdflatex") else [])
    if not cmds:
        return None, "no TeX engine (tectonic, latexmk, pdflatex)"
    for cmd in cmds:
        try:
            r = subprocess.run(cmd, cwd=p, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
        except subprocess.TimeoutExpired:
            return False, f"{cmds[0][0]} timeout 300s"
        if r.returncode:
            return False, f"{cmd[0]} failed: {(r.stdout + r.stderr).strip()[-300:]}"
    return (p / "paper.pdf").exists(), cmds[0][0]


def _table(exp, q):
    """Every trial with its ledger score, generated from results.tsv so the paper never retypes numbers."""
    esc = lambda t: re.sub(r"([&%$#_{}])", r"\\\1", t).replace("~", "-").replace("^", "")  # noqa: E731
    rows = "".join(f"{r['n']} & {r['status']} & {r['score'] or '--'} & {esc(r['desc'][:70])} \\\\\n" for r in _rows(exp, q))
    return ("\\begin{table}[h]\\centering\\small\n\\begin{tabular}{rlrp{0.6\\linewidth}}\n"
            "\\# & status & score & trial \\\\\n\\hline\n" + rows +
            "\\end{tabular}\n\\caption{All trials from the ledger (results.tsv).}\\label{tab:trials}\n\\end{table}\n")


def paper(a):
    d = core.run_dir(a.run)
    c = core.cfg(d)
    exp, p = Path(c["exp_dir"]), d / "paper"
    p.mkdir(exist_ok=True)
    src, nums, py, figs = _inputs(d, c), _numbers(exp, c["q"]), _python(c), []
    (p / "numbers.tex").write_text(nums)
    (p / "trials.tex").write_text(_table(exp, c["q"]))
    (p / "figs.py").write_text(FIGS)
    lj = json.loads(_read(exp / "loop.json")) if (exp / "loop.json").exists() else {}
    direction = lj.get(c["q"], {}).get("direction", "max")
    if py:
        r = subprocess.run([py, "figs.py", str(exp / "results.tsv"), c["q"], direction, "."], cwd=p,
                           capture_output=True, text=True)
        print("WARN: figures failed:", r.stderr.strip()[-300:]) if r.returncode else None
        figs = [f.name for f in sorted(p.glob("fig_*.pdf"))]
    else:
        print("WARN:", NOMPL)
    core.event(d, "paper", f"figures {figs or 'none'}" + ("" if py else f" ({NOMPL})"))
    roles.call("paper", core.prompt("paper", result=_read(d / "RESULT.md"), pick=_read(d / "pick.json"),
                                                  numbers=nums + "\n(trials.tex holds the full trial table; \\input it, do not retype it)",
                                                  figures="\n".join(figs) or "(none)", **src), cwd=p, run=d, write=True)
    tex = _read(p / "paper.tex")
    if tex == "(missing)":
        sys.exit("model did not write paper.tex")
    known_tab = (p / "trials.tex").read_text()
    body = re.sub(r"(?m)(?<!\\)%.*$|\\input\{[^}]*\}|\\includegraphics\[[^\]]*\]|\\usepackage\[[^\]]*\]", "", tex)
    known = "\n".join([*src.values(), nums, known_tab])
    miss = sorted({n for n in re.findall(r"\d+\.\d+", body) if n not in known})
    print(*[f"WARN: {n} in paper.tex is not in results.tsv, DECISIONS.md, PROTOCOL.md or numbers.tex" for n in miss], sep="\n") if miss else None
    ok, how = _compile(p)
    msg = {None: "paper.tex delivered, not compiled: ", True: "paper.pdf compiled with ", False: "compile failed: "}[ok]
    core.event(d, "paper", f"{msg}{how}; {len(miss)} unmatched numbers", unmatched=miss, compiled=ok, figures=figs)


FAKE = r'''import os
tex = [r"\documentclass{article}", r"\usepackage{graphicx}", r"\input{numbers.tex}", r"\begin{document}",
       r"Baseline \BaselineDev{}, best \BestDev{}, holdout \HoldoutBest{} vs \HoldoutBase{}; bogus 7.77."]
tex += [r"\includegraphics[width=0.8\linewidth]{%s}" % f for f in ("fig_trials.pdf", "fig_holdout.pdf") if os.path.exists(f)]
open("paper.tex", "w").write("\n".join(tex + [r"\end{document}"]))
'''
TSV = ("n\tutc\tq\tprotocol\tprocedure\tcode\tscore\tstatus\tseconds\tdesc\n"
       "1\tt\tQ1\tp\tv0\tc1\t0.4638\tkeep\t1\tbaseline\n2\tt\tQ1\tp\tv0\tc2\t0.3523\tdiscard\t1\tx\n"
       "3\tt\tQ1\tp\tv0\tc3\t0.5120\tkeep\t1\ty\n4\tt\tQ1\tp\tv0\tc4\t\tcrash\t1\tz\n"
       "5\tt\tQ1\tp\tv0\tc3\t0.4900\tholdout\t1\tholdout of #3 (dev 0.5120): y\n"
       "6\tt\tQ1\tp\tv0\tc1\t0.4411\tholdout\t1\tholdout of #1 (dev 0.4638): baseline\n")


def selftest():
    from unittest.mock import patch
    with tempfile.TemporaryDirectory() as t:
        d, exp = Path(t) / "r", Path(t) / "r" / "exp"
        exp.mkdir(parents=True)
        testkit.fake(d, FAKE)
        (d / "pick.json").write_text('{"id": "C1"}')
        (exp / "PROTOCOL.md").write_text("protocol abc123\n")
        (exp / "results.tsv").write_text(TSV)
        (exp / "DECISIONS.md").write_text("Q1 **supported_within_scope**\n")
        core.save_cfg(d, {"id": "t", "exp_dir": str(exp), "provider": "cmd:python3 ../fake.py"})
        with patch(__name__ + "._python", return_value=None), \
                patch(__name__ + "._compile", return_value=(None, "offline selftest")):
            paper(SimpleNamespace(run=str(d)))
        p, n = d / "paper", (d / "paper" / "numbers.tex").read_text()
        ev = [json.loads(x) for x in (d / "events.jsonl").read_text().splitlines()]
        for k, v in (("BaselineDev", "0.4638"), ("BestDev", "0.5120"), ("HoldoutBest", "0.4900"),
                     ("HoldoutBase", "0.4411"), ("NTrials", "4"), ("NCrash", "1")):
            assert f"\\newcommand{{\\{k}}}{{{v}}}" in n, (k, n)
        assert NOMPL in ev[0]["msg"]
        assert ev[-1]["unmatched"] == ["7.77"], ev[-1]
        assert ev[-1]["compiled"] is None, ev[-1]
    print("paper selftest ok")


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    selftest()
