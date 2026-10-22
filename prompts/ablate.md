TASK: ablate
Question: {{question}}
Propose at most four dev-only ablations. Each removes or reverts ONE component of
this winner, using the baseline for comparison. Do not run commands, edit files,
or access any holdout data. Return only a JSON array of objects with name, why,
and code (a map from EVERY mutable filename to its FULL new content).
Use unique names other than winner or baseline. An empty array is allowed.
Winner:
{{winner}}
Baseline:
{{baseline}}
