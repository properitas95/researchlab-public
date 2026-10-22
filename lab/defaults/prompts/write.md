TASK: write
Use the `research-writing` and `experiment-stats` skills.
Write RESULT.md for a finished experiment, from the ledger only. Output the Markdown text of RESULT.md and nothing else.

Rules:
- Korean prose, at most one page. Sections: question, what was tried, result, verdict, limits.
- Every number is followed by its trial tag, e.g. 0.812 (#9) or 0.774 (holdout #12). Copy numbers exactly from
  the ledger; never round, derive, or recompute.
- No claim beyond what the ledger and decisions support. Negative and discarded results stay in.
- State limits plainly: what the design could not show, sample or holdout size, what was not tried.
- The verdict must match DECISIONS.md.

Picked candidate:
{{pick}}

PROTOCOL.md:
{{protocol}}

results.tsv (the ledger):
{{results}}

DECISIONS.md:
{{decisions}}
