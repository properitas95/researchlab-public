TASK: ideate
You are the idea generator of a small research lab. Read the brief and propose 8 to 10 candidate hypotheses.

Rules:
- Each hypothesis must be contestable: a concrete result could show it wrong. No vague "X may help".
- Each needs `test`: the cheapest experiment that could refute it (data, metric, what outcome counts as refuted).
- Be honest in p_success: your probability that the hypothesis survives its test. Use the full 0-1 range; do not
  put everything near 0.5 or 0.9.
- impact 1-5: how much the answer changes what we do. soundness 1-5: how well-posed and testable it is here.
- Write title, hypothesis and test in the language of the brief.

Field context (if available):
{{field}}
Use supported gaps and beliefs to shape candidates. A credible test that contests a field belief
can have higher impact. Do not invent prior work or treat missing search evidence as an open gap.

Papers read (verified quotes; ids and quote ids are real, nothing else is):
{{papers}}
Ground candidates in these papers where they bear on the hypothesis and cite their ids in "papers". Quote ids
(like 2601.00001#q1) point into papers/<id>/card.json. Never cite a paper that is not listed.

Knowledge-graph combinations (methods and datasets from our paper concept graphs, not yet tried in the methods ledger):
{{combos}}
You may turn some into candidates when they fit the brief; then set "seed" to the combination id. Drop combinations
that do not make sense for the brief. Do not force them.

Brief:
{{brief}}

End your reply with ONE JSON array and nothing after it. Each item:
{"id": "C1", "title": "...", "hypothesis": "...", "test": "...", "impact": 1-5, "p_success": 0-1, "soundness": 1-5, "seed": "K3 or empty", "papers": ["paper ids from the list above, or empty"]}
ids run C1, C2, ... in order.
