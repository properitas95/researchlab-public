TASK: novelty
You judge whether a candidate research hypothesis is already answered in the literature.

Judge ONLY from the search results below. Do not use memory of papers. If the results are thin, empty, or an
error message, the verdict is "unclear". Use "prior_work" only when a listed result tests essentially the same
claim; "novel" only when the results, including the refutation search, contain nothing close.

Candidate:
{{candidate}}

Search results (first: the title query; second: the refutation query):
{{results}}

Write one or two sentences of reasoning, then end with ONE JSON object and nothing after it:
{"verdict": "novel" | "prior_work" | "unclear", "closest": [{"title": "...", "id_or_url": "...", "why": "..."}], "note": "..."}
`closest` lists at most 3 entries taken from the results; use [] if none.
