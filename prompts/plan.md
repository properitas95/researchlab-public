You are designing one small, falsifiable computational experiment. Write files only inside the current directory. You may read and import the project code and data the brief names (absolute paths); use real data when the brief asks for it — do not substitute a synthetic task.
Use the `evaluator-design` and `research-data` skills.

## Design it right the first time
{{guard}}

## Research brief
{{brief}}

## Chosen candidate
{{pick}}

## Fetched public data
{{data}}

## Produce exactly these files
1. `PROTOCOL.md` (at most 15 lines) with: the question; the data or data-generating process (synthetic data must use fixed seeds); the fixed evaluator; the score and its direction (higher or lower is better); the dev vs holdout split; the one mutable file; `min_delta` (smallest score gain that counts, above noise); what is out of scope.
2. An evaluator script (for example `evaluate.py`). `--split dev|holdout` selects the data. It must be deterministic, finish in a few minutes, never import the mutable file's internals beyond its public function, and print as its LAST stdout line one JSON object: `{"score": <float>, "pass": <bool>}`. `pass` is false when the output breaks a stated constraint.
3. The mutable file with a simple, working baseline implementation (the thing later trials will improve).

## Rules
- Dev and holdout data must be disjoint draws; holdout must differ only by sample, not by task.
- Do not tune anything yet. Run the evaluator once on `--split dev` to check it works, then stop. Never run `--split holdout`: it is opened exactly once, later, by the lab.
- `pass` means the run is valid (data complete, constraints met). It must not encode whether the hypothesis succeeded; report effect gates as extra JSON keys.
- The mutable file's baseline must be the status quo the hypothesis is compared against (e.g. the plain estimator), so the hypothesis itself is the first trial, not the baseline.
- Use only datasets in `data/` listed in `data_plan.json` with status fetched or present (the lab has already placed their files inside this folder under `./data/<name>/`; read them there by the `./data/...` paths given above, never by `../` or absolute paths, and do not copy, edit or delete them: they are frozen inputs). Keep every file small. No network access. Use the Python interpreter the brief names if it gives one.
- The primary score must be unclipped and signed (no `max(0, ·)`): a clipped score gives baseline and probe the same value and fails the critic.
- Do not create other files except what the evaluator needs to run.
- The dev command is exactly `python3 <evaluator>.py --split dev`: no interpreter flags such as `-I` or `-B` (the lab runs the evaluator in its own guarded process; `-I` would disable that guard).
- Also write a probe: a deliberately different but still valid variant of the mutable file (e.g. a uniform-random or always-first-arm policy) named `probe_<mutable filename>`; the lab swaps it in to check the dev score depends on the mutable file.
- End your reply with three lines: `FILES: <evaluator> <mutable file>`, `EVAL: <exact shell command for --split dev, run from this directory>` and `PROBE: <probe file>`.
