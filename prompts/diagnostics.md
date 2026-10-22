TASK: implement sealed dev-only diagnostics
Use the `experiment-stats` skill.
Write exactly one Python script named <diagnostic.name>.py in the current directory for each sealed diagnostic.
You may run quick read-only checks (import the loader, load the data once, slice to DEV) to confirm interfaces;
do not run the full diagnostics. The caller executes them after checking them.
Each script must print a final JSON object line with its specified numeric metric keys. No invented metrics,
synthetic substitute data, threshold changes, or hypothesis judging. Missing data must fail explicitly.
Read the evaluator and reuse its data loading, membership, return conversion, and DEV split boundaries.
Load the cached panel with the evaluator's own loader, then slice to the DEV date range before computing anything.
Loading the cache is allowed; using non-DEV dates is not. Never expect a caller-provided data file. Use the experiment's
interpreter and available libraries. Import helpers only when their imports cannot execute evaluations.
Do not import an evaluator that executes at import time; reuse its loading logic instead.
Read source files outside this directory as needed, but never modify them, change the working directory,
launch subprocesses, access the network, or invoke an evaluator or experiment loop.
No script may contain the text "holdout" in any casing, including comments or strings.
All output writes must use literal paths inside the current directory; preferably only print JSON.
Do not use symlinks, indirect execution, helper scripts, or dynamic imports to bypass these restrictions.
Keep caches and generated files here. The source experiment and frozen files are read-only.
Implement the sealed computation exactly; document units and controls in script comments.

Experiment: {{experiment}}
Evaluator command (reference only; do not execute): {{evaluator}}
Brief:
{{brief}}
Protocol (read DEV boundaries; do not copy forbidden text into scripts):
{{protocol}}
Sealed specification:
{{hypotheses}}
