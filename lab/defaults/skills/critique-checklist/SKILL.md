---
name: critique-checklist
description: Checklist and severity rules for reviewing a research design, result or paper - the finding classes the lab tracks (look-ahead, survivorship, ridge-scale, dividend-basis, lockbox-window, candidate-isolation, freeze-closure, dev-evaluation), blocker/major/minor/known severity, how the data-defect ledger rewrites severities, and the evidence each finding must carry. Use whenever you critique a protocol or evaluator before freeze, replan after a critique, review RESULT.md or a paper, sit on a review panel, or decide whether an issue blocks, even if the request just says "review this" or "check the design".
---

# Critique checklist

A critique exists to stop wrong or leaked scores, not to add paperwork. Findings that only ask for documents make
critic and replan diverge (the lab parks a run when findings stop shrinking).

## 1. Severity

| Severity | Use when | Effect |
|---|---|---|
| blocker | the evaluation would produce a wrong, leaked or non-comparable score: look-ahead, split overlap, baseline is the hypothesis, `pass` encodes the effect, data substituted (synthetic for real) | freeze refused |
| major | a real risk to the conclusion that the design can fix: weak baseline, min_delta below noise, metric does not answer the question, missing uncertainty, unfrozen dependency | must be resolved or disclosed as an accepted limitation |
| minor | clarity, provenance, receipts, hashes, access journals, documentation | never blocks |
| known | set by code, not by you: the finding matches an open data-defect ledger entry | recorded as a constraint; results provisional |

Never block on paperwork. Do not ask for new artifacts. Use `freeze_ok` only when no blocker or major remains.

## 2. Finding classes

Critic ids are free text; the methods ledger groups them by these topics (`lab/methods.py` TOPICS). Use words that
match the topic so recurrence is counted.

| Class | Matches | Ask |
|---|---|---|
| look-ahead | look-ahead, membership, point-in-time, formation | Does anything at t use information from after t (index membership, fitted scalers, revised values)? |
| survivorship | surviv, delist | Are dead and delisted assets present with their final returns? |
| ridge-scale | ridge | Is the regularization scale set on dev only and stated in units that transfer? |
| dividend-basis | dividend, ex-date | Are prices and dividends on the same basis and aligned to ex-dates? |
| lockbox-window | lockbox, holdout window | Does the dev period overlap a sealed or previously opened window? |
| candidate-isolation | in-process, monkey, rebind, same process | Can the candidate code patch the evaluator because they share a process? |
| freeze-closure | freeze, transitive import | Are all imported modules and data paths in `--frozen`? |
| dev-evaluation | dev-evaluation, --split dev | Does the evaluator run on dev alone and pass its contract? |

Deterministic checks the critic merges in: `missing-input`, `split-branch`, `unfrozen-dependency`, `synthetic-data`,
`baseline-pass`, `baseline-score`, `dev-evaluation`, `critic-failed`, `critic-verdict`, `inputs-changed`.
Do not suppress them.

## 3. How the ledger rewrites severity

`defects.apply` maps each finding to a topic and rewrites it by the ledger entry's state:
- open -> severity becomes `known`, the finding cites the ledger id (`D-<topic>`).
- confirmed -> keeps its severity (a verified defect blocks until fixed).
- fixed -> keeps severity; claim is prefixed "regression of a fixed defect".
- rejected -> severity becomes `minor`.

So: cite the ledger id (for example `D-look-ahead`) for a known defect. Raise it as new only if this design makes it
worse, and say how.

## 4. Evidence per finding

Every finding carries:
- `id`: short kebab-case, using a class word above when it fits.
- `claim`: one concrete sentence: what is wrong and what score it corrupts.
- `evidence`: `file:line: quote` from the numbered inputs. No quote, no finding. Distinguish "observed" from
  "unknown" (a holdout command in documentation is not evidence that it ran).
- `fix`: the minimal change (one line of code or protocol), not a new document.

## 5. Core review list (design)

- Real data is real; bootstrap resampling is not synthetic data.
- Look-ahead in timestamps, lags, training windows, labels and portfolio formation.
- Dev and holdout disjoint; no sign of holdout use during planning.
- `pass` = validity only.
- Baseline = status quo, and the strongest simple one (see `evaluator-design`); its stated score checked.
- Metric answers the question; min_delta exceeds noise; uncertainty is estimated.
- Missing out-of-sample returns neither invalidate every run nor select assets with future data.
- External loaders, caches and imports listed for `--frozen`.
- Runtime and reproducibility are feasible on dev.

## 6. Reviewing results and papers

- Every number maps to a ledger row or evaluator output; flag numbers that are not there.
- The verdict word matches DECISIONS.md; claims are no stronger than the verdict (see `research-writing`).
- Causal language needs a design that identifies the cause; otherwise ask for "associated with".
- Negative, discarded and crashed trials are counted, not dropped.
- A "no effect" claim needs an equivalence interval or the null checks in `experiment-stats`; otherwise it is
  inconclusive.
- A panel cannot accept while a major point survives.

## 7. Replan after a critique

Fix the measured defect with the smallest change. For a finding the design cannot fix, add one protocol line:
`Accepted limitation: <id> - why`. That is a disclosure, not a bypass. Do not add paperwork to make a finding go away.
