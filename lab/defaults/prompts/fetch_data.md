TASK: fetch-data. Decide whether this research question needs external public data, and if so download it.

## Research brief
{{brief}}

## Chosen candidate
{{pick}}

## Papers read (may be empty)
{{papers}}

## Rules
- Already in the workspace (verified by the lab; do not download these again, they count as available):
{{present}}
- Work only in the current folder. Write the decision to `_plan.json`: `{"needed": true|false, "reason": "...", "datasets": [{"name": "...", "status": "fetched|refused|failed", "reason": "..."}]}`.
- `needed` is false when the brief is synthetic-only or names data that already exists on disk. Then download nothing.
- Download only public datasets whose license allows research use, without credentials: no API keys, no logins, no scraping behind authentication. If a source needs one, set status `refused` and say why. A dataset that the official source publishes openly for download, with no terms forbidding research use, is allowed even without a named license: write the license field as the source states it (for example "no explicit license; openly published by <owner>"). Refuse only when the terms forbid research use or redistribution-free use, or the data is not openly downloadable.
- Put each dataset in its own folder `./<name>/` (name: letters, digits, `_`, `.`, `-`). Keep raw files as published; do not filter, impute or select.
- Total size limit: {{max_mb}} MB. If the source is larger, download nothing for it and set status `refused` with the size.
- Write `./<name>/MANIFEST.json`: `{"name", "source_url", "license", "retrieved_utc", "files": [{"path": "<relative to ./<name>/>", "sha256", "bytes", "rows" (optional)}], "as_of" or "dev_end" (time series), "notes"}`. List every file in the folder; delete scratch files and scripts. Compute sha256 from the files on disk.
- Never invent data or substitute synthetic data for a missing source: report `failed` instead.
