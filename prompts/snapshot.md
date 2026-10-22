TASK: snapshot

Write build.py and source.txt in the current directory. Do not execute build.py.
Use this interpreter: {{python}}
The harness runs build.py once, then splits the data by date without a model call.

build.py must read only the local sources named in the request (existing caches,
warehouse Python APIs, or parquet paths). Write exactly one full.pkl with
pandas.to_pickle: a DataFrame with DatetimeIndex, or a dict of those DataFrames.
Preserve date-keyed mappings such as uni: {date: [names]} alongside the frames.
Do not discard dates, manufacture data, fit models, evaluate strategies, or run
any holdout. Do not write split files or a manifest. Write only within cwd.
source.txt must identify the actual source paths, retrieval method, and any
transformations. No credentials. Missing sources are errors, never synthetic fallbacks.

Request:
{{request}}
