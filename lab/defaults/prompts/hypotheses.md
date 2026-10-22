TASK: pre-register discriminating mechanistic hypotheses
Use the `experiment-stats` skill.
Read the supplied evidence. Do not run any measurements, evaluator, scripts, or data queries. Do not write files.
Explain the observed phenomenon with 2–3 competing mechanisms, not candidate score improvements.
Return only JSON:
{"phenomenon":"...","hypotheses":[{"id":"H1","mechanism":"...","predictions":[{"diagnostic":"short_snake_name","metric":"json_key","op":">","value":0,"why":"mechanistic reason"}]}],"diagnostics":[{"name":"short_snake_name","what_to_compute":"precise dev-only computation, units, controls, aggregation, and metric keys"}]}
Each hypothesis needs at least two predictions. Operators are only >, <, >=, <=; thresholds are finite numbers.
At least one shared diagnostic AND metric must have mutually incompatible predictions from different hypotheses
(e.g. H1 > 0.1 versus H2 <= 0.1; > 0.1 versus < 0.2 overlaps and is insufficient).
Use unique hypothesis IDs and diagnostic names. Define reproducible interventions, controls, split boundaries,
and thresholds before measuring; do not infer a mechanism merely from the existing performance score.
For daily cross-sectional RMS normalization harming long-only GMV, possible competing mechanisms include
quiet-day overweighting, loss of common-factor information, and interaction with long-only constraints.
Design interventions that separate their predictions, such as capped inverse-RMS weighting, restoring
common-factor scale, and matched constrained/unconstrained allocations. Preserve raw volatility scaling,
universe, windows, and other evaluator settings unless the diagnostic explicitly intervenes on them.
Existing results are retrospective context, not new confirmation. Use only DEV data. Never open holdout.

{{inputs}}
