TASK: replan
Use the `critique-checklist` and `evaluator-design` skills.
Revise this experiment's PROTOCOL.md, evaluator, and mutable baseline in the current directory.
Resolve every blocker and major in the critique. For each finding that cannot be fixed within this design, add one protocol line: `Accepted limitation: <id> — why`.
An accepted limitation is a disclosure, not permission to bypass the critic or freeze gate.
Keep the chosen question, data scope, dev/holdout split, and status-quo baseline. Do not tune on holdout, weaken validity checks, or replace real data with synthetic data.
Never run holdout. You may test dev. Do not access the network. Write only in the current directory; read external dependencies only as needed.
Use the interpreter in the brief. Retain the evaluator filename and dev command so the critic can rerun the same check.
The dev command is `python3 <evaluator>.py --split dev` with no interpreter flags (`-I`, `-B`): the lab guards the evaluator itself.
The evaluator must print a last JSON object with numeric score and boolean pass (validity, not effect).
The primary score must be unclipped and signed (no `max(0, ·)`): a clipped score gives baseline and probe the same value and fails the critic.
Read data from `./data/<name>/` in this folder (placed and frozen by the lab); never delete or edit `./data/`, and do not use `../` or absolute data paths.
Keep or rewrite the probe `probe_<mutable filename>` (a deliberately different, still valid variant of the mutable file, e.g. uniform-random or always-first-arm); the lab swaps it in to check the dev score depends on the mutable file.
End with `FILES: <evaluator> <mutable file>`, `EVAL: <exact command ending in --split dev>` and `PROBE: <probe file>`.

## Design it right the first time
{{guard}}

## Brief
{{brief}}

## Critique
{{critique}}

## Previous execution error
{{last_error}}
Fix the design itself (evaluator, protocol lines, baseline). Do not add receipts, manifests, journals, hash
inventories or other new documentation files; an unfixable point becomes one 'Accepted limitation' line.
