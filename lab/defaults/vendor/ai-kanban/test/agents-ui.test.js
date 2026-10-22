import { test } from 'node:test';
import assert from 'node:assert/strict';
import { initOps } from '../public/ops.js';

class Element {
  constructor(tag) { this.tag = tag; this.children = []; this.style = {}; this.dataset = {}; this.attrs = {}; this.events = {}; }
  set textContent(value) { this.text = String(value); this.children = []; }
  get textContent() { return (this.text ?? '') + this.children.map((c) => c.textContent).join(''); }
  set innerHTML(_) { throw new Error('Feed HTML is forbidden'); }
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

test('agent anatomy uses safe text, sorts costs, filters kind details and refreshes with the route', async (t) => {
  const previous = { document: globalThis.document, Node: globalThis.Node };
  const doc = new Element('document'), root = new Element('div');
  doc.createElement = doc.createElementNS = (...args) => new Element(args.at(-1));
  Object.assign(globalThis, { document: doc, Node: Element });
  t.after(() => { for (const [key, value] of Object.entries(previous)) { if (value === undefined) delete globalThis[key]; else globalThis[key] = value; } });
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const unsafe = '<svg onload=alert(1)>', card = 'ACME-1/"<script>';
  const group = { sessions: 3, wall_min_median: 1.2, wall_min_p90: 3.4, turns_median: 2, prefill_median: 100, peak_median: 200, peak_p90: 300, input_median: 400, output_median: 500, cache_ratio: 0.6, tool_calls_median: 7 };
  const feed = {
    as_of: 'agents-now', since: '2026-10-20', sessions: 6,
    groups: [{ ...group, kind: 'codex', model: unsafe, effort: 'high', input_total: 100 }, { ...group, kind: 'claude', model: unsafe, effort: 'low', input_total: 200 }],
    inflow_by_kind: { codex: [{ what: unsafe, tokens: 75 }, { what: '사용자', tokens: 25 }], claude: [{ what: '다른 유입', tokens: 20 }] },
    prefill: { codex: { sessions: 2, files: [{ name: unsafe, share: 0.8 }] } },
    skills: { codex: [{ skill: unsafe, sessions: 2 }], claude: [{ skill: '다른 스킬', sessions: 1 }] },
    tools: { codex: [{ tool: unsafe, calls: 5 }] },
    top_inflows: [{ tokens: 100, kind: 'codex', card, tool: '압축 행의 도구명', input: unsafe, context_reset: true }, { tokens: 200, kind: 'claude', tool: unsafe, input: unsafe }],
    top_cards: [{ card: 'ACME-2', title: unsafe, input: 1, output: 2 }, { card, title: unsafe, sessions: 1, minutes: 3.1, input: 10, output: 20 }],
  };
  let count = 0;
  const ops = initOps(async (path) => { if (path === '/api/agents') { count++; return feed; } return {}; }, root);
  ops.setVisible(true); await settle();
  const content = root.children.find((section) => section.children[0]?.textContent === 'Agent anatomy').children[1];
  const byClass = (tag, name) => content.querySelectorAll(tag).filter((el) => el.className === name);
  const body = (name) => byClass('table', name)[0].querySelector('tbody');
  assert.match(root.textContent, /agents-now/);
  assert.equal(body('ops-agents-table').rows[0].children[0].textContent, 'claude');
  assert.match(body('ops-agents-table').rows[0].textContent, /1.2 \/ 3.4/);
  assert.match(body('ops-agents-table').rows[0].textContent, /200 \/ 300/);
  assert.match(content.textContent, /60%/);
  assert.match(content.textContent, /Context right after compaction\/resume/);
  assert.doesNotMatch(content.textContent, /압축 행의 도구명/);
  assert.equal(body('ops-agent-top-inflows').rows[0].children[0].textContent, '200');
  assert.equal(body('ops-agent-top-cards').rows[0].children[0].textContent, card);
  assert.ok(content.querySelectorAll('a').some((link) => link.href === `#/${encodeURIComponent(card)}`));
  assert.equal(content.querySelectorAll('svg').length, 0);
  assert.ok(content.textContent.includes(unsafe));
  content.querySelectorAll('button').find((b) => b.textContent === 'codex').events.click();
  assert.deepEqual(byClass('div', 'ops-agent-kind').map((el) => el.dataset.kind), ['codex']);
  assert.doesNotMatch(byClass('div', 'ops-agent-kind')[0].textContent, /다른 유입|다른 스킬/);
  assert.deepEqual(byClass('span', 'ops-agent-inflow-segment').map((el) => el.style.width), ['75%', '25%']);
  assert.match(byClass('div', 'ops-agent-kind')[0].textContent, /80%/);
  assert.match(byClass('div', 'ops-agent-chips')[1].textContent, /5calls/);
  assert.equal(body('ops-agents-table').rows.length, 2);
  t.mock.timers.tick(60000); await settle();
  assert.equal(count, 2);
  assert.deepEqual(byClass('div', 'ops-agent-kind').map((el) => el.dataset.kind), ['codex']);
  content.querySelectorAll('button').find((b) => b.textContent === 'All').events.click();
  assert.equal(byClass('div', 'ops-agent-kind').length, 2);
  ops.setVisible(false); t.mock.timers.tick(60000); await settle(); assert.equal(count, 2);
  ops.setVisible(true); await settle(); assert.equal(count, 3);
  for (const key of Object.keys(feed)) delete feed[key];
  t.mock.timers.tick(60000); await settle();
  assert.match(content.textContent, /No session totals/);
  assert.match(content.textContent, /No card totals/);
  ops.setVisible(false);
});
