---
name: research-data
description: Point-in-time data handling for research experiments - dated immutable snapshots with a dev/holdout cut, look-ahead and leakage checks (index membership as of date, survivorship, restated or revised values), the data-defect ledger, and honest proxy labelling. Use whenever you build, load, split, audit or describe a dataset for an experiment, write a snapshot build script, review an evaluator's data path, or see words like snapshot, universe, membership, delisting, vintage, restatement, look-ahead, leakage or proxy - even if the task does not say "data".
---

# Research data

A score is only as honest as the data under it. Most failed designs in this lab failed in the data layer
(future index membership, survivors only, revised values), not in the method. Check the data before the idea.

## 1. Snapshot first, then experiment

Every experiment reads one immutable, dated snapshot, never a live cache or warehouse query.

The harness recipe (`lab.py snapshot RUN --name N --dev-end YYYY-MM-DD`):
1. The data engineer writes `build.py` and `source.txt` only. `build.py` writes `full.pkl`: a DataFrame with a
   non-null DatetimeIndex, or a dict keyed by date (nested dicts of those are fine).
2. The harness runs `build.py` once, then splits by date without a model: rows dated `<= dev-end` go to `dev.pkl`,
   later rows to `holdout.pkl`. `full.pkl` is deleted.
3. `manifest.json` records rows per split, columns, date range, holdout start, sha256 of every file, source and
   build time. The catalog `memory/global/datasets.json` gets the same entry.
4. Rebuilding with different bytes is refused unless `--force`. A changed snapshot is a new question, not a fix.

Rules for `build.py`:
- Copy the named source; do not filter, impute, winsorize or select assets there. Selection belongs to the evaluator,
  where it is frozen and visible.
- Keep every date key that the split must see. A value without a date cannot be split and fails the build.
- Write `source.txt`: exact source (file path or query), vintage or download date, and any known gaps.
- Never open, print or summarize holdout rows. Report unavailable data instead of synthesizing a substitute.
- Evaluators load data only through `LAB_DATA_DIR` (`dev.pkl` / `holdout.pkl`). Hard-coded paths, `.pkl/.parquet/.csv`
  literals or imports of loader/cache/warehouse modules are flagged by `data.check_evaluator`.

## 2. Look-ahead and leakage checklist

Run this list on every design. Each item names what to check and the evidence that settles it.

| Risk | Check | Evidence |
|---|---|---|
| Index membership | Is the universe at date t the membership known at t, not today's list? | membership table keyed by date; a delisted name appears before its exit |
| Survivorship | Are dead, merged and delisted assets present with their last returns? | count of assets that leave the sample; returns up to the exit date |
| Restated / revised values | Is each value the vintage available at t (first release, not revised)? | release date or vintage column; PIT join (ASOF on release time) |
| Publication lag | Is a fundamental used only after its filing or release date plus a lag? | the lag constant in the frozen code |
| Formation window | Do weights, ranks, scalers or hyperparameters at t use only data `< t`? | rolling or expanding fits; no full-sample `fit` before the split |
| Labels | Is the target strictly after the features in time, with no overlap? | label horizon vs feature timestamp |
| Corporate events | Are dividends, splits and ex-dates aligned to the trading calendar? | ex-date column; adjusted vs raw price stated |
| Missing returns | Do missing out-of-sample returns neither drop the run nor pick assets by future survival? | explicit rule in the evaluator |
| Split overlap | Do dev and holdout share no rows and no fitted state? | manifest row counts; `holdout_start > dev date_max` |

If an item cannot be checked from the files, say "unknown" and mark the result provisional. Unknown is not clean.

## 3. Data-defect ledger

Recurring data findings live in `memory/global/data_defects.json` (`lab.py defects`, `lab.py defect-set ID STATE --note`).
A critic topic seen in three or more runs becomes an entry automatically (rule: the same finding three times becomes a rule).

| State | Meaning | Effect on a design |
|---|---|---|
| open | recurring claim, not yet verified | reported as a known constraint; results stay provisional; not a blocker |
| confirmed | the data engineer verified it | blocks until fixed |
| fixed | corrected in the data layer | a recurrence is a regression |
| rejected | checked and not a defect | downgraded to minor |

As data engineer: verify an open entry with a concrete test (for example, list names in the universe at date t that
joined the index after t). Set `confirmed` or `rejected` with the test as the note. Fix confirmed defects once in the
snapshot layer, then set `fixed`.
As researcher or critic: cite the ledger id (for example `D-look-ahead`) instead of re-raising it as new.

## 4. Proxies stay proxies

- A proxy (a substitute variable, a synthetic panel, a sample, a related market) is labelled as a proxy in the
  protocol, the result and the paper. Write "X, a proxy for Y" every time it carries a claim.
- Never swap a proxy in for direct evidence silently, and never swap real data for synthetic data. If the real data is
  missing, stop and report the gap.
- Before you name a computed number, read its definition in the code. Write "called X, defined as Y" when they differ.
- Self-imposed restrictions (an anchor requirement, a minimum history, a filter) shrink samples. Before accepting a
  small sample, check whether the restriction is needed and whether the quantity can be reconstructed exactly
  another way.

## 5. Before you hand over a snapshot or a data claim

- [ ] Source, vintage and date range are written in `source.txt` and match the manifest.
- [ ] Every row of section 2 is answered with evidence or marked unknown.
- [ ] No holdout row was read, printed or summarized.
- [ ] Ledger ids are cited for known defects; new recurring issues are named so they can be counted.
- [ ] Every proxy is labelled.
