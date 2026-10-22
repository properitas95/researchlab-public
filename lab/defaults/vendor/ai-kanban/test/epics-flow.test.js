import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { createApp } from '../src/server.js';
import { layoutGraph, orderEpics } from '../public/epics-layout.js';

let app;
const previousCommand = process.env.AI_KANBAN_EPICS_CMD;
const fixtureDir = fs.mkdtempSync(path.join(os.tmpdir(), 'kanban-epics-'));
const script = (name, body) => {
  const file = path.join(fixtureDir, name);
  fs.writeFileSync(file, body);
  return file;
};
const quote = (value) => `'${value.replaceAll("'", "'\\''")}'`;

before(() => { app = createApp({ dbPath: ':memory:' }); });
after(async () => {
  if (previousCommand === undefined) delete process.env.AI_KANBAN_EPICS_CMD;
  else process.env.AI_KANBAN_EPICS_CMD = previousCommand;
  await app.close();
  fs.rmSync(fixtureDir, { recursive: true, force: true });
});

function callEpics() {
  return new Promise((resolve) => {
    const req = { method: 'GET', url: '/api/epics-flow', headers: { host: 'localhost' } };
    const res = {
      writeHead(status) { this.status = status; },
      end(body) { resolve({ status: this.status, body: JSON.parse(body) }); },
    };
    app.server.emit('request', req, res);
  });
}

test('epics flow returns command JSON', async () => {
  const file = script('flow.js', "process.stdout.write(JSON.stringify({epics:[],as_of:'now'}))");
  process.env.AI_KANBAN_EPICS_CMD = `${quote(process.execPath)} ${quote(file)}`;
  assert.deepEqual(await callEpics(), { status: 200, body: { epics: [], as_of: 'now' } });
});

test('epics flow shares concurrent results and caches for 60 seconds', async (t) => {
  let now = Date.now();
  t.mock.method(Date, 'now', () => now);
  const count = path.join(fixtureDir, 'count');
  const file = script('cached.js', `import fs from 'node:fs'; fs.appendFileSync(${JSON.stringify(count)}, 'x'); process.stdout.write(JSON.stringify({count: fs.readFileSync(${JSON.stringify(count)}, 'utf8').length}));`);
  process.env.AI_KANBAN_EPICS_CMD = `${quote(process.execPath)} ${quote(file)}`;
  const [first, second] = await Promise.all([callEpics(), callEpics()]);
  assert.deepEqual(first.body, { count: 1 });
  assert.deepEqual(second.body, { count: 1 });
  now += 59_999;
  assert.deepEqual((await callEpics()).body, { count: 1 });
  now += 2;
  assert.deepEqual((await callEpics()).body, { count: 2 });
});

test('epics flow returns 404 when unset', async () => {
  delete process.env.AI_KANBAN_EPICS_CMD;
  assert.deepEqual(await callEpics(), { status: 404, body: { error: 'epics feed not configured' } });
});

test('epics flow returns 502 for invalid JSON and command failure', async () => {
  const bad = script('bad.js', "process.stdout.write('nope')");
  process.env.AI_KANBAN_EPICS_CMD = `${quote(process.execPath)} ${quote(bad)}`;
  assert.equal((await callEpics()).status, 502);
  const fail = script('fail.js', 'process.exit(7)');
  process.env.AI_KANBAN_EPICS_CMD = `${quote(process.execPath)} ${quote(fail)}`;
  const result = await callEpics();
  assert.equal(result.status, 502);
  assert.equal(result.body.error, 'Epics command exited with code 7');
});

test('graph uses longest-path layers and predecessor barycenters', () => {
  const graph = { nodes: ['A', 'B', 'D', 'C', 'E'].map((id) => ({ id })), edges: [
    { from: 'A', to: 'C' }, { from: 'B', to: 'D' }, { from: 'C', to: 'E' }, { from: 'A', to: 'E' },
  ] };
  const original = JSON.stringify(graph), result = layoutGraph(graph);
  assert.equal(result.cyclic, false);
  assert.deepEqual(Object.fromEntries(result.nodes.map((n) => [n.id, n.layer])), { A: 0, B: 0, D: 1, C: 1, E: 2 });
  assert.ok(result.nodes.find((n) => n.id === 'C').order < result.nodes.find((n) => n.id === 'D').order);
  assert.equal(JSON.stringify(graph), original);
});

test('graph retains cycles, ignores dangling edges, and spaces 40 nodes without overlap', () => {
  const cycle = layoutGraph({ nodes: [{ id: 'A' }, { id: 'B' }], edges: [{ from: 'A', to: 'B' }, { from: 'B', to: 'A' }, { from: 'missing', to: 'A' }] });
  assert.equal(cycle.cyclic, true);
  assert.equal(cycle.nodes.length, 2);
  assert.equal(cycle.edges.length, 2);
  const nodes = Array.from({ length: 40 }, (_, i) => ({ id: String(i) }));
  const result = layoutGraph({ nodes, edges: nodes.slice(1).map((n, i) => ({ from: String(i), to: n.id })) });
  assert.equal(result.nodes.at(-1).layer, 39);
  assert.equal(new Set(result.nodes.map((n) => `${n.x},${n.y}`)).size, 40);
  assert.deepEqual(layoutGraph().nodes, []);
});

test('epics sort open first by overrun and keep children under parents, including parent cycles', () => {
  const epics = [{ key: 'closed', status: 'done', expected_hours: 1, actual_or_forecast_hours: 100 },
    { key: 'small', expected_hours: 1, actual_or_forecast_hours: 2 },
    { key: 'large', expected_hours: 1, actual_or_forecast_hours: 4 }, { key: 'child', parent: 'large' }];
  assert.deepEqual(orderEpics(epics).map(({ epic, depth }) => [epic.key, depth]), [['large', 0], ['child', 1], ['small', 0], ['closed', 0]]);
  assert.equal(orderEpics([{ key: 'A', parent: 'B' }, { key: 'B', parent: 'A' }]).length, 2);
});
