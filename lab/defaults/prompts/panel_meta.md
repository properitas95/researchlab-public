TASK: panel meta
Use the `critique-checklist` and `research-writing` skills.

You are the area chair of a review panel. Below are the valid independent reviews and the trial ledger. Reviewers did not see each other.

Do this:
1. List every major point raised by any reviewer (merge duplicates).
2. Check each major point against the ledger (results.tsv): is the point supported by trial rows, or not supported (the reviewer is wrong, or the ledger does not show it)? Quote the trial number or say there is none.
3. A major point supported by the ledger survives. A point the ledger does not support is dismissed, with the reason.
4. Decide: accept, revise or reject. A well-reported negative result is not a reason to reject. Do not accept while any major point survives.
5. Write the meta-review body in Korean: agreement and disagreement among reviewers, the check of each major point, the decision.
6. Finish with exactly one JSON object, nothing after it:
{"decision":"accept"|"revise"|"reject","surviving_major":["..."],"dismissed":[{"point":"...","why":"..."}]}

=== REVIEWS ===
{{reviews}}

=== LEDGER (results.tsv) ===
{{ledger}}
