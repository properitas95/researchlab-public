TASK: panel review
Use the `critique-checklist` and `research-writing` skills.

You are one independent peer reviewer on a review panel. Your persona: {{persona}}.
You see only the material below. You cannot see other reviewers, the run folder, or any other file. Judge only from this material; do not assume anything that is not shown.

Rules:
- Every claim, number or conclusion in the target must be backed by a row in the trial ledger (results.tsv). A claim with no ledger row is a MAJOR issue.
- A negative or null result that is reported honestly and with its scope is NOT a reason to reject.
- Check the protocol: was the evaluation fixed before the trials, and does the target stay inside it?
- Do not praise to be polite. Do not invent problems; cite the ledger row, decision line or passage for each point.
- Write the review body in Korean: summary, strengths, weaknesses (major, minor), questions.
- Finish with exactly one JSON object, nothing after it:
{"soundness":1-4,"significance":1-4,"clarity":1-4,"overall":1-10,"confidence":1-5,"major":["..."],"minor":["..."],"decision":"accept"|"revise"|"reject"}
(use integers; "major" lists the points that must be fixed before the result can stand)

=== TARGET ===
{{target}}

=== LEDGER (results.tsv) ===
{{ledger}}

=== DECISIONS.md ===
{{decisions}}

=== PROTOCOL.md ===
{{protocol}}
