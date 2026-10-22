# Does a decision-learned Gerber threshold beat fixed c=0.5 and Ledoit-Wolf for KOSPI200 GMV?

Question: for long-short global-minimum-variance (GMV) portfolios on KOSPI200 constituents, does a Gerber covariance whose threshold c is learned on a decision loss (next-21-day realized portfolio variance) give lower out-of-sample realized variance than (a) the Gerber covariance with fixed c=0.5 and (b) Ledoit-Wolf one-factor shrinkage?

Available inside this workspace:
- data/kr_a200_dev/returns.tsv.gz: daily log total returns of KOSPI200-era constituents, 2015-01-05..2020-12-30 (1474 dates x 351 six-digit KRX codes). Tab-separated, gzip, header `date` + codes, empty cell = missing. Dev snapshot: nothing after 2020-12-30 exists here.
- data/kr_a200_dev/universe.json: {"YYYY-MM-DD": [codes]}, point-in-time top-200 universes, one per 21 trading days after a 252-day window; a universe holds from its key date until the next key. Use only the codes listed for the date (no look-ahead membership).
- data/kr_a200_dev/MANIFEST.json: source and sha256 of both files.
- A paper skill and source for the learned Gerber threshold, if one has been attached (`researchlab attach`); otherwise implement Gerber and the threshold training yourself.
- Load the data with the standard library plus numpy:
  `import gzip, numpy as np; L = gzip.open(path, "rt").read().splitlines(); codes = L[0].split("\t")[1:]; dates = [r.split("\t", 1)[0] for r in L[1:]]; R = np.array([[float(x) if x else np.nan for x in r.split("\t")[1:]] for r in L[1:]])`
Use the interpreter {PYTHON} (numpy available; install nothing else without saying so). One evaluation must finish in a few minutes.

## Pre-registered design (implement this; do not redesign)

- Portfolio: long-short GMV, w = (S + rho*mean(diag S)*I)^-1 1 / 1'(...)^-1 1, ridge rho = 1e-3. Refit every 21 trading days on the trailing 252 days of the universe valid at the refit date; hold 21 days; score = realized variance of the held daily portfolio returns (lower is better). Missing returns: in the 252-day estimation window, drop a name with any missing return for that refit (no zero-fill of the window). In the 21-day holding period, keep the weights fixed: a held name's missing daily return counts as 0 (held as cash) — do not reselect names and do not invalidate the window.
- Estimators: (a) Gerber, hard threshold, gs2022 normalisation, c = 0.5; (b) Ledoit-Wolf one-factor shrinkage; (c) the mutable file: a function `estimate(window) -> covariance` (baseline = (a)). The decision-learned threshold is one candidate the search may try in the mutable file.
- Split by refit date: dev = refits from 2016-01-01 to 2018-12-31; holdout = refits from 2019-01-01 to 2020-12-30. The evaluator passes the mutable function only trailing windows that end before each dev refit date; holdout rows are never handed to the mutable code during search. The lab opens the holdout once.
- Primary score: mean realized variance over dev refits of (c), relative to (a); also report (b). min_delta: measure the noise floor first (block bootstrap of the dev refits) and set min_delta above it.

## Approved data limitations (record them in PROTOCOL.md under "Declared limitations"; they are not design defects)

- This snapshot has been explored before (earlier Gerber studies used the same 2015-2020 data); the 2019-2020 holdout is not a virgin sample.
- Delisted names and survivorship coverage of the 351-code list are not documented.
- No data after 2020-12-30 is included; the universe is the top-200 rule of the source, not the official index file.
