TASK: posthoc
Use the `experiment-stats` skill.
Question: {{question}}
Winner snapshot (read-only): {{snapshot}}
Mutable files: {{mutable}}
Experiment (read-only): {{experiment}}
Dev evaluation command (reference only): {{evaluator}}
Write up to THREE standalone small Python analysis scripts in the current folder.
Explore dev sub-periods, an alternative dev universe, or parameter sensitivity.
Import winner Python modules directly from the snapshot path via importlib; never
import current mutable experiment code. Use DEV data only, never any other split.
You may read any file (cat, sed, ls) and run quick read-only checks, such as importing the snapshot or
loading the dev data once, to learn the interfaces. Read the evaluator script in the experiment folder and
reuse its data loading and dev split exactly. Do not run the full analyses; the caller executes them later.
Each script must print one JSON object line. Keep scripts independent: no local
helper modules. Do not change existing files, snapshots, evaluator, or protocol.
Do not write outside this folder; use literal paths for any output writes.
Scripts must not contain the word "holdout" in any casing, even in comments.
If the data interface is unclear, write fewer scripts rather than fabricate data.
