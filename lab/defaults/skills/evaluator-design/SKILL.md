---
name: evaluator-design
description: How to design a frozen experiment evaluator and protocol that a search loop cannot game - JSON score contract, dev/holdout split, frozen vs mutable files and hashes, min_delta above noise, the strongest simple baseline (not only the naive one), reproduction anchor of a published number, and opening the holdout exactly once. Use whenever you plan, draft, revise or freeze an experiment, write evaluate.py or PROTOCOL.md, choose a baseline or metric, set min_delta, or respond to a design critique, even if the user only says "set up the experiment".
---

# Evaluator design

The evaluator is the referee. Once frozen, the search may change only the mutable files, and every trial is scored
by the same bytes. A weak referee lets a loop "win" by exploiting the scorer, a weak baseline or the holdout.

## 1. Contract

- The evaluator prints one JSON object as the last stdout line: `{"score": float, "pass": bool, ...}`.
- `pass` means validity only (data loaded, no NaN, constraints met). Never encode the effect, a threshold or a
  hypothesis in `pass`; a baseline with `pass=false` is a blocker.
- Add uncertainty so the verdict rule can read it: `bootstrap_lower95` / `bootstrap_upper95` (for a ratio score),
  or `se`, and `period_ratios` (one value per sub-period) when time matters. Without an interval the verdict can only
  compare the point estimate with min_delta.
- `--split dev|holdout` selects the sample. The code must branch on the split; dev and holdout are disjoint samples
  of the same task and share no fitted state.
- Load data only through the snapshot (`LAB_DATA_DIR`). No absolute paths, caches or warehouse imports.
- Deterministic: fixed seeds, single-threaded BLAS, no network. A rerun of the same code gives the same score.

## 2. Frozen and mutable files

- `--frozen`: the evaluator, every local module it imports, loaders, constants and data manifests. List transitive
  imports and any path assembled from a root constant; an unlisted dependency is an unfrozen referee.
- `--mutable`: the one or two files the search may change (the method). Nothing else.
- The harness hashes frozen files and refuses a trial when the hash changes; it also compares hashes before and after
  each proposer call and stops if a file outside the mutable set changed. Changing the referee means a new question
  id, not an edit.

## 3. Baselines: avoid the weak-baseline trap

A gain over a naive baseline is often a gain any simple method would get.
- The baseline implements the status quo, never the proposed idea.
- Compare against the strongest simple baseline a practitioner would use, not only the naive one. Examples:
  covariance -> 1-factor or constant-correlation shrinkage (Ledoit-Wolf), not only the sample covariance;
  forecasting -> random walk and a tuned AR or seasonal naive; classification -> a tuned logistic regression;
  thresholds -> a common fixed threshold and a per-task fitted one.
- When several baselines exist, report the gain against each, and let the headline use the strongest.
  A win only against a weak comparator is reported as exactly that.
- State the baseline's own score in the protocol and check that the first trial reproduces it.

## 4. Reproduction anchor

Before searching, reproduce a known number: a published table value, the original code's output, or last run's
baseline. Freeze with `--anchor V --anchor-tol T`; the director refuses to search until the baseline scores
`V +- T`. If it does not reproduce, fix the setup or explain the gap first - a search on a broken setup measures the bug.

## 5. min_delta

- min_delta is the smallest gain that matters and must exceed plausible noise. Estimate noise first: rerun the
  baseline under bootstrap or seeds, or take the spread of period ratios. Set min_delta above that floor.
- Fix min_delta before seeing candidate scores. A trial is kept only if it beats the best kept score by more than
  min_delta.

## 6. Holdout: once, best vs baseline

- The holdout opens once per question, at the end of the search, on the best kept code and the baseline together.
  The harness locks it (`.rloop/holdout_Q.json`) and refuses a second opening.
- Never run, read or summarize holdout data during planning, search, post-hoc or diagnostics.
- A dev-only result carries the `dev` tag; only the holdout comparison earns `confirmed`.

## 7. Search freely inside dev

Exploration inside the dev sample is unrestricted: try many methods, variants and parameters. The guard is at the
boundary - evaluator, baseline, min_delta and holdout are fixed before results are seen. Do not shrink the search to
avoid multiple comparisons; report how many trials were run instead.

## 8. Freeze checklist

- [ ] JSON contract with `pass` = validity only and an uncertainty field.
- [ ] Split branch present; dev and holdout disjoint.
- [ ] Frozen list covers every import and data path; mutable list is minimal.
- [ ] Strongest simple baseline implemented and its score stated; naive baseline reported too if cheap.
- [ ] Anchor reproduced (or the gap explained).
- [ ] min_delta above the measured noise floor, fixed in advance.
- [ ] Holdout command exists, has never run, and will be opened once on best + baseline.
- [ ] Known data defects cited by ledger id (see the `research-data` skill).
