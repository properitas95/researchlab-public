TASK: venue profile

You profile a publication venue so that a research lab can aim a paper at it. Use only the material below: counted statistics from the venue's last years of papers, a sample of its most cited titles with abstracts, and the venue's own guidance text if any. Do not invent facts about the venue that this material does not support; when the material is silent, say so in the field.

Venue: {{venue}}

=== STATISTICS (counted, deterministic) ===
{{stats}}

=== SAMPLE PAPERS (title, year, citations, abstract start) ===
{{sample}}

=== VENUE GUIDANCE TEXT (call for papers, review criteria; may be empty) ===
{{site}}

Do this:
1. form: how a typical accepted paper is built: structure, length, contribution types.
2. acceptance_bar: what a paper shows at three levels: reject, borderline, accept. Give concrete, checkable criteria (data, baselines, ablations, theory, scope), not adjectives.
3. reviewers: exactly 3 reviewer personas this venue would likely assign: name, expertise, what this reviewer checks, harshness (lenient|moderate|harsh and why).
4. common_methods: the methods that recur in the sample.
5. saturated_topics: topics so frequent in the sample that a new paper on them needs a clear twist.

Finish with exactly one JSON object, nothing after it:
{"form":{"structure":"...","length":"...","contribution_types":["..."]},"acceptance_bar":{"reject":"...","borderline":"...","accept":"..."},"reviewers":[{"name":"...","expertise":"...","checks":"...","harshness":"..."}],"common_methods":["..."],"saturated_topics":["..."]}
