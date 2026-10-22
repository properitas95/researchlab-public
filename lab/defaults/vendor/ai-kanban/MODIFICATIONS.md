# Modifications to vendored ai-kanban (MIT)

2026-10-22, ResearchLab:

- Lineage and Knowledge graph are now in-board views (`data-view="lineage"|"kg"`, routes `#/KEY/lineage`, `#/KEY/kg`): `public/app.js` (`PAGE_VIEWS`, `showPage`) shows the page in an iframe reloaded on each selection, hides filters and the activity panel like Operations, links "Open in new tab", and shows an empty state on 404; `public/index.html` swaps the new-tab anchors for buttons; `public/styles.css` `.page*`.

- Knowledge graph: the same allowlist also serves `/lab/kg/kg.html` and `/lab/kg/kg.json` (from `<workspace>/kg/`, written by `lab.py kg`); `public/index.html` gets a sidebar link "지식 그래프" under "연구 계보"; `test/lab-pages.test.js` covers it and its traversal cases.

- `src/server.js`: when env `LAB_PAGES` names a workspace folder, GET/HEAD `/lab/lineage.html` and `/lab/lineage.json` are served read-only from it (exact allowlist, no listing; 404 if unset or missing). Lets the board link to the workspace research pages.
- `public/index.html`: sidebar link "연구 계보" opening `/lab/lineage.html` in a new tab.
- `test/lab-pages.test.js`: route test.

Upstream license: see `LICENSE`.

- 2026-10-22: Lineage and Knowledge graph links moved under the project views (Board, Plan, Review); the Ops (운영) nav item removed: ResearchLab runs one board per workspace, not a multi-agent fleet.
- 2026-10-22: /lab/replay/index.html and /lab/replay/<snapshot>/<file> served read-only (per-round snapshots); .md MIME.
- 2026-10-22: Replay in-board view (iframe of /lab/replay/index.html).
