# Third-party skills

| name | upstream URL + path | commit sha | license | modifications | why chosen |
|---|---|---|---|---|---|
| backtesting-frameworks | https://github.com/wshobson/agents `plugins/quantitative-trading/skills/backtesting-frameworks/` (SKILL.md, references/details.md) | be57c0b2e3c05c528ca6132b87410b385718775f (last commit touching the path; repo main 0874a27b17a22e2b7c2e2bcd725768e0803cad06) | MIT (c) 2024 Seth Hobson | none | look-ahead, survivorship, and transaction-cost checks for any backtest evaluator |
| statistical-analysis | https://github.com/anthropics/knowledge-work-plugins `data/skills/statistical-analysis/SKILL.md` | 2d6f7e22dd25593f0f748010430ef86f19659735 (last commit touching data/skills; repo main 33d3dea41a341b341d4f5da2e8801ae0c89be2b8) | Apache-2.0 | none (renamed from local copy `kwp-statistical-analysis` back to upstream name) | test choice, outlier, multiple-comparison cautions for result claims |
| validate-data | https://github.com/anthropics/knowledge-work-plugins `data/skills/validate-data/SKILL.md` | 2d6f7e22dd25593f0f748010430ef86f19659735 (same as above) | Apache-2.0 | removed one line pointing to ../../CONNECTORS.md (see validate-data/NOTICE) | pre-share QA checklist for methodology, aggregation, and bias |

Upstream LICENSE text is copied unchanged into each skill folder. The Apache-2.0 LICENSE is the stock text with the template line `Copyright [yyyy] [name of copyright owner]`; upstream ships no NOTICE file.
The MIT LICENSE contains a copyright holder name (Seth Hobson) but no email address.
