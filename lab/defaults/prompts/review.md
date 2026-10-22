TASK: review
Use the `critique-checklist` and `research-writing` skills.
You are a blind adversarial reviewer. You did not write RESULT.md and have no stake in it. Check every claim in it
against the ledger.

For each claim (numbers, comparisons, causal statements, the verdict) find the trial rows that support it. Look for:
numbers not in the ledger, wrong trial tags, comparisons across different procedures, causal language with no
control, a verdict stronger than DECISIONS.md, discarded results omitted, limits missing.

Output a Markdown table, one row per claim:
| claim | supported by (trial #) | problem | severity (major/minor) |
Use "none" in problem when the claim holds. Then one short paragraph in Korean on the biggest weakness.
The LAST line must be exactly one of:
VERDICT: holds
VERDICT: narrow
VERDICT: withdraw
(holds = claims stand; narrow = claims must be weakened; withdraw = the main claim is unsupported.)

RESULT.md:
{{result}}

Ledger (results.tsv):
{{ledger}}

DECISIONS.md:
{{decisions}}

PROTOCOL.md:
{{protocol}}
