TASK: paper
Use the `research-writing` skill.
Write the file paper.tex in the current directory (LaTeX, article class, at most 4 pages, English). Write nothing else
to disk. Everything comes from the material below; if it is not there, it is not in the paper.

Structure: Abstract, Introduction, Method, Experiments, Results, Limitations, Reproducibility.
Add "Related work" only if it can be written purely from the protocol or picked-candidate text; otherwise omit it.

Rules:
- No claim beyond the ledger. The verdict must match DECISIONS.md. Verdict vocabulary: supported_within_scope = the
  holdout agrees with the dev gain inside the tested scope only; no_improvement = no kept trial beat the baseline by
  the frozen margin; inconclusive = the evidence cannot separate effect from noise; invalid = the evaluator or
  procedure failed its checks. Use the DECISIONS.md word and this meaning, nothing stronger.
- Negative results, discarded and crashed trials are reported, including how many there were.
- Limitations must restate what the holdout does and does not establish: it was opened once on the best kept code and
  the baseline only, it shows the dev gain did or did not carry over to that sample, and it says nothing about other
  data, other tasks, or untried variants.
- Cite every number through the macros defined in numbers.tex (for example \BestDev{}); never type a decimal number
  yourself. A trial number may be cited as plain text (trial 9).
- Start the preamble with \documentclass{article}, \usepackage{graphicx}, then \input{numbers.tex}.
- In Experiments, \input{trials.tex} for the trial table (generated from the ledger). Do not write your own trial table. Gate values (period ratios, bootstrap bounds) may be quoted from the ledger text verbatim.
- Include each listed figure with \includegraphics[width=0.8\linewidth]{<file>} and a caption that says what it shows.
  If no figures are listed, include none.
- No invented citations, datasets, or related work. Reproducibility: give the protocol hash and the commands found in
  PROTOCOL.md.

Picked candidate:
{{pick}}

PROTOCOL.md:
{{protocol}}

results.tsv (the ledger):
{{results}}

DECISIONS.md:
{{decisions}}

RESULT.md:
{{result}}

numbers.tex:
{{numbers}}

Figures available:
{{figures}}
