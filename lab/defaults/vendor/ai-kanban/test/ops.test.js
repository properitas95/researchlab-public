import { test } from 'node:test';
import assert from 'node:assert/strict';
import { initOps } from '../public/ops.js';
import { createEpicsRenderer } from '../public/epics.js';
import { layoutWorkerRuns, frequentBottlenecks } from '../public/epics-layout.js';

// No HTML parser: assigning feed data to innerHTML fails this DOM fixture immediately.
class Element {
  constructor(tag) { this.tag = tag; this.children = []; this.style = {}; this.dataset = {}; this.attrs = {}; this.events = {}; }
  set textContent(value) { this.text = String(value); this.children = []; }
  get textContent() { return (this.text ?? '') + this.children.map((c) => c.textContent).join(''); }
  set innerHTML(_) { throw new Error('HTML is forbidden for command feeds'); }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.text = ''; this.children = children; }
  setAttribute(key, value) { this.attrs[key] = value; }
  addEventListener(event, fn) { this.events[event] = fn; }
  createTHead() { const el = new Element('thead'); this.append(el); return el; }
  createTBody() { const el = new Element('tbody'); this.append(el); return el; }
  insertRow() { const el = new Element('tr'); this.append(el); return el; }
  insertCell() { const el = new Element('td'); this.append(el); return el; }
  get rows() { return this.children; }
  querySelectorAll(tag) { return this.children.flatMap((c) => [...(c.tag === tag ? [c] : []), ...c.querySelectorAll(tag)]); }
  querySelector(tag) { return this.querySelectorAll(tag)[0]; }
}
const settle = async () => { for (let i = 0; i < 5; i++) await Promise.resolve(); };

test('operations renders safe inline details, refreshes independently and stops when hidden or off route', async (t) => {
  const doc = new Element('document'), root = new Element('div');
  const previous = { document: globalThis.document, Node: globalThis.Node };
  Object.assign(globalThis, { document: doc, Node: Element });
  t.after(() => { for (const [key, value] of Object.entries(previous)) { if (value === undefined) delete globalThis[key]; else globalThis[key] = value; } });
  doc.createElement = doc.createElementNS = (...args) => new Element(args.at(-1));
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const unsafe = '<img src=x onerror=alert(1)>', counts = {};
  const counter = { input: 1200, output: 300, cached_input: 600, cache_ratio: 0.5, sessions: 2, busy_hours: 1.5, tool_calls: 4 };
  const data = {
    '/api/worker-runs': { as_of: 'runs-now', runs: Array.from({ length: 54 }, (_, i) => ({ worker: `worker-${i}`, card: `ACME-${i % 2 + 1}`, kind: i % 2 ? 'glm' : 'codex', model: unsafe, effort: 'high', tier: 'priority', start: new Date(Date.now() - (i + 1) * 60000).toISOString(), end: i ? new Date(Date.now() - i * 60000).toISOString() : null, result: i ? 'no_commit' : 'submitted', busy_hours: 1, wall_hours: 2, input: 1200, cached_input: 600, output: 300, tool_calls: 4 })), totals: { runs: 54, submitted: 1, no_commit: 53, busy_hours: 54 }, by_model_effort: [{ kind: 'codex', model: unsafe, effort: 'high', runs: 27, submitted: 1, no_commit: 26, busy_hours: 27, input: 32400, output: 8100 }] },
    '/api/workers': { as_of: 'workers-now', workers: [{ kind: 'codex', name: unsafe, model: 'model', effort: 'high', cards: 'ACME-1 ACME-2', card_titles: { 'ACME-1': unsafe, 'ACME-2': 'second' }, card_epics: { 'ACME-1': 'ACME-9' }, last_write_s: 180, last_line: unsafe }, ...['dispatch_loop', 'review_merge_loop', 'bottleneck_loop'].map((name) => ({ name, kind: '리드 루프', model: 'python' }))], orphans: [{ key: 'ACME-3', title: unsafe }] },
    '/api/epics-flow': { as_of: 'epics-now', epics: [{ key: 'ACME-9', title: unsafe, delay: [{ cause: unsafe, hours: 2, evidence: [unsafe] }], graph: { nodes: [{ id: 'ACME-1', title: unsafe }] }, critical_path: ['ACME-1'] }] },
    '/api/usage': { as_of: 'usage-now', total: counter, efficiency: { waiting_share: 0.2 }, by_kind_model: [{ kind: 'codex', model: unsafe, effort: 'high', ...counter }], by_model: [{ model: unsafe, ...counter }], top_cards: Array.from({ length: 35 }, (_, i) => ({ card: `ACME-${i}`, title: unsafe, input: i })), wasted_runs: [{ cause: unsafe, count: 1, tokens_upper_bound: 1500, examples: [unsafe] }] },
    '/api/agents': { as_of: 'agents-now', groups: [] },
  };
  let missing = false;
  let board = { epics: [{ key: 'ACME-9', title: '보드 에픽 제목' }], columns: [{ tasks: [{ key: 'ACME-1', title: unsafe, epic: 'ACME-9' }, { key: 'ACME-2', title: 'second' }] }] };
  const ops = initOps(async (path) => {
    counts[path] = (counts[path] ?? 0) + 1;
    if (missing) throw Object.assign(new Error('unset'), { status: 404 });
    return data[path];
  }, root, () => board);
  assert.deepEqual(counts, {});
  ops.setVisible(true); await settle();
  assert.match(root.textContent, /runs-now.*workers-now.*epics-now.*usage-now/);
  assert.match(root.textContent, /64,800/); assert.match(root.textContent, /3m ago/);
  assert.equal(root.querySelectorAll('img').length, 0);
  assert.ok(root.querySelectorAll('a').some((a) => a.textContent === 'ACME-1'));
  assert.match(root.textContent, /보드 에픽 제목/);
  assert.match(root.textContent, /Lead loops 3 OK/);
  const live = root.querySelectorAll('table').find((table) => table.className === 'ops-live-table');
  assert.equal(live.querySelector('tbody').rows.length, 2);
  assert.doesNotMatch(live.textContent, /dispatch_loop|python/);
  assert.ok(root.querySelectorAll('div').some((el) => el.className === 'ops-alert'));
  const runBody = () => root.querySelectorAll('table').find((table) => table.querySelector('thead').textContent.includes('Start (KST)')).querySelector('tbody');
  assert.equal(runBody().rows.length, 50);
  root.querySelectorAll('button').find((b) => b.textContent === 'Show more').events.click();
  assert.equal(runBody().rows.length, 54);
  root.querySelectorAll('button').find((b) => b.textContent === 'glm').events.click();
  assert.equal(runBody().rows.length, 27);
  assert.ok(runBody().rows.every((row) => row.children[1].textContent === 'glm'));
  root.querySelectorAll('button').find((b) => b.textContent === 'Submitted').events.click();
  assert.equal(runBody().rows.length, 0, 'kind and result filters compose');
  root.querySelectorAll('button').find((b) => b.textContent === 'All').events.click();
  assert.equal(runBody().rows.length, 1);
  board.columns[0].tasks[0].title = '갱신된 보드 제목'; ops.refreshBoard();
  assert.match(root.textContent, /갱신된 보드 제목/);
  assert.match(root.textContent, /second/);
  const topCards = root.querySelectorAll('table').find((table) => table.querySelector('thead').textContent === 'CardTitleKindTokensBusy time (h)').querySelector('tbody');
  assert.equal(topCards.rows.length, 30);
  assert.equal(topCards.rows[0].children[0].textContent, 'ACME-34');
  const epic = root.querySelectorAll('button').find((b) => b.className === 'epic-select');
  assert.equal(root.querySelectorAll('svg').length, 0);
  epic.events.click(); assert.equal(root.querySelectorAll('svg').length, 1);
  assert.match(root.textContent, /Delay causes/);
  assert.ok(root.querySelectorAll('g').some((n) => n.attrs.class === 'epic-node critical'));
  t.mock.timers.tick(15000); await settle();
  assert.deepEqual(Object.values(counts), [1, 2, 1, 1, 1]);
  t.mock.timers.tick(45000); await settle();
  assert.deepEqual(Object.values(counts), [2, 3, 2, 2, 2]);
  assert.equal(root.querySelectorAll('svg').length, 1, 'selection survives a feed refresh');
  doc.hidden = true; doc.events.visibilitychange();
  t.mock.timers.tick(120000); await settle(); assert.deepEqual(Object.values(counts), [2, 3, 2, 2, 2]);
  doc.hidden = false; doc.events.visibilitychange(); await settle();
  assert.deepEqual(Object.values(counts), [3, 4, 3, 3, 3]);
  ops.setVisible(false); t.mock.timers.tick(120000); await settle();
  assert.deepEqual(Object.values(counts), [3, 4, 3, 3, 3]); assert.equal(root.hidden, true);
  missing = true; ops.setVisible(true); await settle();
  assert.equal(root.children.slice(1).filter((s) => s.textContent.endsWith('Feed not configured')).length, 5);
  ops.setVisible(false);
  const lateRoot = new Element('div'); let finish, calls = 0;
  const late = initOps(() => { calls++; return new Promise((resolve) => { finish = resolve; }); }, lateRoot);
  late.setVisible(true); late.setVisible(false); finish({ as_of: 'late-response' }); await settle();
  t.mock.timers.tick(120000); await settle();
  assert.equal(calls, 5); assert.doesNotMatch(lateRoot.textContent, /late-response/);
});

test('epic PERT renders safe activity boxes, KST forecasts and ranked actions and retains its axis choice', (t) => {
  const previous = globalThis.document, root = new Element('div');
  globalThis.document = { createElement: (tag) => new Element(tag), createElementNS: (_, tag) => new Element(tag) };
  t.after(() => { if (previous === undefined) delete globalThis.document; else globalThis.document = previous; });
  const unsafe = '<img src=x onerror=alert(1)>', key = 'ACME-1/"<script>';
  const pert = {
    te_hours: 12.3, sd_hours: 2.1, p50_hours: 12.3, p85_hours: 14.5, p95_hours: 16,
    p50_at: '2026-10-21T03:00:00Z', p85_at: '2026-10-21T04:00:00Z', p95_at: '2026-10-21T05:00:00Z',
    prob_within: { '24h': 0.95, '48h': 0.99, '7d': 1 }, constrained_hours: 20, binding: 'resources',
    nodes: [{ id: key, title: unsafe, status: 'in_progress', es: 0, te: 2.3, sd: 0.4, ef: 2.3, ls: 0, slack: 0, lf: 2.3, critical: true, open_question: true },
      { id: 'ACME-2', title: '외부 검토', status: 'review', es: 2.3, te: 1.2, ef: 3.5, ls: 3.3, slack: 1, lf: 4.5, near_critical: true, external: true }, { id: 'DONE-1', title: '완료 카드', done: true, es: 0, te: 0 }],
    edges: [{ from: key, to: 'ACME-2', source: unsafe, critical: true }],
  };
  const epic = { key: 'ACME-9', title: unsafe, pert, delay: [{ cause: '검토 대기', hours: 2 }], accelerate: [
    { saves_hours: 0.24, action: '숨길 조치' },
    { saves_hours: 0.25, action: '최소 단축' },
    { saves_hours: 1, action: '작은 단축', cards: ['ACME-2'] },
    { saves_hours: 4, action: unsafe, why: unsafe, cards: [key], user_action: true },
  ] };
  const render = createEpicsRenderer(root);
  render({ epics: [epic] });
  const head = root.querySelector('table').querySelector('thead').textContent;
  assert.match(head, /TE ± SDp85 done \(KST\)ConstraintTop action/);
  assert.equal(root.querySelector('tbody').rows[0].children[9].textContent, '숨길 조치');
  root.querySelectorAll('button').find((b) => b.className === 'epic-select').events.click();
  assert.match(root.textContent, /Delay causes/);
  assert.match(root.textContent, /12\.3h ± SD 2\.1h/);
  assert.match(root.textContent, /13:00 KST/);
  assert.match(root.textContent, /P\(24h\) 95\.0%.*P\(48h\) 99\.0%.*P\(7d\) 100\.0%/);
  assert.match(root.textContent, /Constraint Resources · PERT 12\.3h \/ resources 20\.0h/);
  assert.ok(root.querySelectorAll('g').some((g) => g.attrs.class === 'epic-node pert-node critical'));
  assert.ok(root.querySelectorAll('g').some((g) => g.attrs.class === 'epic-node pert-node near-critical' && g.attrs['data-external'] === 'true'));
  assert.ok(root.querySelectorAll('path').some((p) => p.attrs.class === 'epic-edge pert-edge critical'));
  assert.ok(root.querySelectorAll('g').some((g) => g.attrs['aria-label'] === 'Open question'));
  assert.ok(root.querySelectorAll('a').some((a) => a.attrs.href === `#/${encodeURIComponent(key)}`));
  assert.equal(root.querySelectorAll('img').length, 0);
  assert.ok(root.querySelector('svg').textContent.includes(unsafe));
  assert.match(root.querySelector('svg').textContent, /ES.*TE.*EF.*LS.*slack.*LF/);
  assert.match(root.querySelector('svg').textContent, /TE 2\.3±0\.4/);
  assert.match(root.querySelector('aside').querySelector('li').textContent, /4\.0h saved/);
  assert.match(root.querySelector('aside').textContent, /User action/);
  assert.match(root.textContent, /Done 1/);
  assert.ok(root.querySelectorAll('a').some((a) => a.href === '#/DONE-1'));
  assert.equal(root.querySelectorAll('g').filter((g) => g.attrs.class?.includes('pert-done-chip')).length, 0);
  assert.doesNotMatch(root.querySelector('aside').textContent, /숨길 조치/);
  assert.match(root.querySelector('aside').textContent, /최소 단축/);
  assert.equal(root.querySelector('aside').querySelectorAll('li').length, 3);
  const title = root.querySelectorAll('div').find((el) => el.className === 'pert-node-title');
  assert.equal(title.textContent, `${key} · ${unsafe}`);
  assert.ok(root.querySelectorAll('title').some((el) => el.textContent.includes(`${key} · ${unsafe}`)));
  root.querySelectorAll('button').find((b) => b.textContent === 'Show done cards').events.click();
  assert.equal(root.querySelectorAll('button').find((b) => b.textContent === 'Show done cards').attrs['aria-pressed'], 'true');
  const chip = root.querySelectorAll('g').find((g) => g.attrs.class?.includes('pert-done-chip'));
  assert.equal(chip.querySelector('rect').attrs.height, '24');
  assert.equal(chip.querySelectorAll('foreignObject').length, 1);
  root.querySelectorAll('button').find((b) => b.textContent === 'Dependency layers').events.click();
  assert.equal(root.querySelector('svg').attrs['aria-label'], 'PERT dependency layer chart');
  assert.match(root.querySelector('svg').textContent, /Level 0.*Level 1/);
  render({ epics: [epic] });
  assert.equal(root.querySelector('svg').attrs['aria-label'], 'PERT dependency layer chart');
  root.querySelectorAll('button').find((b) => b.textContent === 'ES time axis').events.click();
  assert.match(root.querySelector('svg').textContent, /Now 0h/);
  root.querySelectorAll('button').find((b) => b.textContent === 'Show done cards').events.click();
  assert.equal(root.querySelectorAll('g').filter((g) => g.attrs.class?.includes('pert-done-chip')).length, 0);
  assert.equal(root.querySelectorAll('button').find((b) => b.textContent === 'Show done cards').attrs['aria-pressed'], 'false');

  render({ epics: [{ ...epic, pert: undefined }] });
  assert.equal(root.querySelectorAll('section').filter((s) => s.className === 'epic-pert').length, 0);
  assert.match(root.textContent, /No dependency graph/);
});


test('timeline assigns one row per worker, clips x positions and extends open runs to now', () => {
  const now = Date.parse('2026-10-21T03:00:00Z'), hour = 3_600_000;
  const run = (worker, from, to) => ({ worker, card: worker, start: new Date(now + from * hour).toISOString(), end: to == null ? null : new Date(now + to * hour).toISOString() });
  const input = [run('older', -20, -18), run('active', -12, null), run('older', -19, -17), run('clipped', -30, -6), run('gone', -30, -25), { worker: 'bad', start: 'invalid' }, run('future', 1, 2)];
  const before = JSON.stringify(input), layout = layoutWorkerRuns(input, now);
  assert.deepEqual(layout.rows.map((row) => row.worker), ['active', 'clipped', 'older']);
  const active = layout.rows[0].bars[0];
  assert.equal(active.x, 50); assert.equal(active.width, 50); assert.equal(active.to, now);
  assert.equal(layout.rows[1].bars[0].x, 0); assert.equal(layout.rows[1].bars[0].width, 75);
  assert.equal(layout.rows[2].lanes, 2); assert.deepEqual(layout.rows[2].bars.map((bar) => bar.lane), [0, 1]);
  assert.equal(layout.ticks[0].x, 0); assert.equal(layout.ticks.at(-1).x, 100);
  assert.equal(JSON.stringify(input), before);
  assert.equal(layoutWorkerRuns([], now).rows.length, 0);
  const many = layoutWorkerRuns(Array.from({ length: 60 }, (_, i) => run(`w-${i}`, -i / 3 - 1, -i / 3)), now);
  assert.equal(many.rows.length, 60);
  assert.ok(many.rows.every((row) => row.lanes === 1));
});


test('bottlenecks count open epics, normalize resource ids and kinds, and retain ties', () => {
  const resources = { codex: { queue_hours: 2 }, merge: { queue_hours: 1 } };
  const epics = [
    { current_bottleneck: { id: 'worker-1', kind: 'codex' }, next_bottleneck: { id: 'merge' } },
    { current_bottleneck: { id: 'codex', kind: 'worker' }, next_bottleneck: 'codex' },
    { status: 'done', current_bottleneck: 'merge', next_bottleneck: 'merge' },
  ];
  assert.deepEqual(frequentBottlenecks(epics, 'current_bottleneck', resources), [['codex', 2]]);
  assert.deepEqual(frequentBottlenecks(epics, 'next_bottleneck', resources), [['codex', 1], ['merge', 1]]);
  assert.deepEqual(frequentBottlenecks([], 'next_bottleneck'), []);
});
