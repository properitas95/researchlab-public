---
name: experiment-stats
description: Statistics for judging experiment results - bootstrap confidence intervals, effect size with an interval, multiple testing (Holm), the harness verdict ladder (large/supported/small/inconclusive/no_effect/worse and supported_within_scope/inconclusive/no_improvement/invalid), and the rule against premature "no effect" (conditional, nonlinear and event-window variants, self-imposed sample limits, a higher-power parallel spec). Use whenever you analyse trial or holdout results, write post-hoc or diagnostic scripts, pre-register hypotheses, compare methods, read a p-value or interval, or are about to write that something "has no effect", "does not work" or "is not significant".
---

# Experiment statistics

## 1. Report an effect, not a p-value

- Effect = best minus baseline in the score's direction (for a ratio score, 1 - ratio). Report it with a 95% interval.
- Default interval: block bootstrap over time blocks (keeps autocorrelation), 1,000+ resamples, fixed seed. Use
  paired resampling: the same resampled blocks for candidate and baseline.
- Also report per-period effects (`period_ratios`) - a gain that holds in every period is stronger than a pooled one.
- Give n (rows, periods, assets) next to every interval.

## 2. The verdict ladder (computed by code, not by you)

`analyze` applies this rule to the evaluator output (`lab/experiment.py::_effect`). Use these words exactly.

| Fine | Condition (low/high = interval of the effect) | Coarse (DECISIONS.md) |
|---|---|---|
| large | low > 0, effect >= 2 x min_delta, every period improves | supported_within_scope |
| supported | low > 0, effect >= min_delta | supported_within_scope |
| small | low > 0, effect < min_delta (eligible to open the holdout) | inconclusive |
| inconclusive | interval contains both 0 and min_delta, or decides nothing | inconclusive |
| no_effect | interval inside +-min_delta (equivalence shown) | no_improvement |
| worse | high < 0, or every period worse | no_improvement |
| no_improvement | no interval and effect <= min_delta, or no valid candidate | no_improvement |
| invalid | no valid baseline or the procedure failed | invalid |

Tags: `dev` = development sample only; `confirmed` = the once-opened holdout of best vs baseline agrees.
A holdout score missing or for the wrong trial makes the result inconclusive; the holdout is never reopened.

## 3. Inconclusive is not "no effect"

- An interval that spans 0 means the design could not tell. Write "inconclusive: interval [a, b] includes 0 and
  min_delta", not "no effect".
- "No effect" needs equivalence: the whole interval inside +-min_delta. Even then it is "no effect larger than
  min_delta on this sample, metric and design".
- Always state what the design cannot detect (minimum detectable effect, sample size, the functional form tested).

## 4. Before declaring a null

A pooled linear test is not evidence of absence. Before any "does not work":
1. Conditional / regime: split by states a practitioner would use (high vs low volatility, limits and extremes,
   up vs down markets, size buckets). Count non-empty cells first - an empty condition is not a test.
2. Nonlinear: deciles or quantile buckets, thresholds, interactions, a tree model - not only one linear coefficient.
3. Event windows: event studies around discrete actions (announcements, rebalances, limit hits).
4. Sample limits: is a restriction self-imposed (an anchor, a minimum history, a filter)? Can the quantity be
   reconstructed exactly another way to recover sample?
5. Reference deviations: before calling a reference method a failure, list where your implementation differs from
   its specification (fitted vs fixed coefficients, missing terms, horizon).
6. Higher-power parallel spec: run a pre-registered spec with more power alongside the strict one (longer window,
   aggregated unit, common component as the outcome) and report both.

## 5. Do not tune toward the null

- Explore representation widely in dev (window, normalization, residualization, unit) and report the grid.
- Do not pick the variant, window or shrinkage that makes the signal vanish, and do not hide computed values.
- Size control (false discovery) is kept, but never as the only lens: report the power-side spec next to it.

## 6. Multiple testing

- Count every test that was run, including discarded variants. Report the count.
- Holm step-down for a family of k tests: sort p-values ascending; compare p_(i) with alpha / (k - i + 1); stop at the
  first failure; all later ones fail too. Report raw and adjusted values.
- For a search over many variants, compare the winner with a max-statistic null (the best of k under resampling or a
  circular shift), not with a single-test threshold.
- Post-hoc and discriminating diagnostics are dev-only and provisional; label them so.

## 7. Hypotheses and diagnostics

- Pre-register 2-3 competing mechanisms, each with machine-checkable predictions (diagnostic, metric, operator,
  value) before measuring. At least one diagnostic must separate the hypotheses.
- The code decides: any failed prediction rejects a hypothesis; all tested predictions passing means it survives
  (provisionally, on dev).
- A diagnostic prints a final JSON line with exactly its metric keys. Missing data fails explicitly; no invented
  metrics, no threshold changes after seeing data.

## 8. Checklist before writing a statistical claim

- [ ] Effect with interval, n and resampling scheme.
- [ ] Verdict word copied from DECISIONS.md / verdict.json with its tag (dev or confirmed).
- [ ] Number of tests stated; Holm or max-statistic adjustment where a family was searched.
- [ ] For a negative: section 4 variants run or listed as untested; the detection limit stated.
- [ ] Untested explanations written as "untested".
