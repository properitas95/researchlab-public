---
name: research-writing
description: Rules for writing research results and papers from a ledger - claims capped by verdict strength, every number traceable to a trial row or evaluator output, negative and inconclusive results reported with their scope, venue-profile fit, and no unproven causal or quantitative claims. Use whenever you write or revise RESULT.md, a paper, an abstract, a rebuttal or a review response, summarize an experiment for a reader, or check a draft's claims, even if the task only says "write it up".
---

# Research writing

The reader is someone who has not seen the run. They should be able to check every number and see exactly how far
each claim reaches.

## 1. Claim strength follows the verdict

Copy the verdict word from DECISIONS.md / verdict.json. Never write a stronger claim than this table allows.

| Verdict (tag) | Allowed claim | Not allowed |
|---|---|---|
| supported_within_scope (confirmed) | "On this data and metric, X improved Y by e [a, b] over baseline B; the holdout agreed." | general, causal or other-market claims |
| supported_within_scope (dev) | "On the development sample X improved Y; not confirmed out of sample." | "X improves Y" |
| inconclusive | "The evidence cannot separate the effect from noise: [a, b] includes 0 and min_delta." | "no effect", "does not work" |
| no_improvement | "No tested variant beat the baseline by min_delta on this design." State what the design could not detect. | "X has no effect" (unless equivalence was shown) |
| invalid | "The evaluation failed its checks; no result." | any effect statement |

Fine verdicts add detail: large, supported, small, no_effect, worse. A win only against a weak baseline is stated as
exactly that, naming the baseline.

## 2. Every number is traceable

- Copy numbers exactly from the ledger (results.tsv, evaluator JSON, LEDGER.md); never round, derive or recompute in
  prose. Tag each: `0.812 (#9)`, `0.774 (holdout #12)`.
- In the paper, numbers come through the generated macros (`numbers.tex`, for example `\BestDev{}`) and the generated
  trial table (`trials.tex`). Do not type decimal numbers or write your own table.
- A number not in the ledger is not in the text. If it is needed, say it was not measured.
- Before naming a number, read its definition in the code. If it is called X but defined as Y, write Y first.

## 3. Negative and inconclusive results stay in

- Report how many trials were run, kept, discarded, invalid and crashed.
- Report worse results and rejected hypotheses with their scope (dev or confirmed, sample, metric).
- Limitations restate what the holdout does and does not establish: opened once on the best kept code and the
  baseline only; says nothing about other data, tasks or untried variants.
- Write the strongest limitation next to the claim it limits, not only in a closing section.

## 4. No unproven claims

- Causal words (because, drives, causes, explains) need a design that identifies the cause. Otherwise: "is
  associated with", or "consistent with H2, which survived the dev diagnostics; H1 and H3 were rejected".
- An explanation that was not tested is written as "untested", not as a finding.
- A proxy is named as a proxy every time it carries a claim.
- Do not call a bound, a ceiling or a cost "the reason" unless it was measured as such. Different metrics (an IC
  series Sharpe vs a portfolio Sharpe) are not compared as if one were the loss of the other.
- Sign of accounting terms: check the identity in the code before calling a term a loss or a gain.

## 5. Structure

- Lead with the answer: question, verdict, effect with interval, scope - in the first paragraph.
- RESULT.md: question, what was tried, result, verdict, limits; one page.
- Paper: abstract, introduction, method, experiments, results, limitations, reproducibility. Related work only if
  it can be written from the supplied material.
- Use plain sentences. Avoid hype adjectives (novel, remarkable, significant without a test).

## 6. Venue fit

If the run has a target venue (`run.json` "venue", profile from `lab.py venue-profile NAME`):
- Match its form (structure, length, contribution type) from `profile.json`.
- Read its `acceptance_bar` (reject / borderline / accept) and check the paper against the accept level: data,
  baselines, ablations, theory, scope. State honestly which level the evidence reaches.
- Anticipate the three reviewer personas in the profile and answer their checks in the text, from the ledger.
- A topic in `saturated_topics` needs its twist stated in the first paragraph.
Venue fit shapes framing, never the numbers or the verdict.

## 7. Revising and responding to reviews

- Address every surviving major point. Map each to the change made, or explain from the evidence why it cannot be
  addressed. Never claim missing evidence was collected.
- Preserve numbers and generated files; change prose only.

## 8. Final check

- [ ] Verdict word and tag match DECISIONS.md; no claim above its row in section 1.
- [ ] Every number tagged or macro-cited; none recomputed.
- [ ] Negative, discarded and inconclusive results counted with scope.
- [ ] No causal or "no effect" claim without the design that supports it; untested is labelled untested.
- [ ] Venue profile checked when one exists.
