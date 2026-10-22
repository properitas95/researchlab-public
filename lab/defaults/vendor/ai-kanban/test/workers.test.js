import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { createApp } from '../src/server.js';
import { splitArgv } from '../src/workers.js';

let app;
const previousCommand = process.env.AI_KANBAN_WORKERS_CMD;
const previousRunsCommand = process.env.AI_KANBAN_RUNS_CMD;
const fixtureDir = fs.mkdtempSync(path.join(os.tmpdir(), 'kanban-workers-'));
const script = (name, body) => {
  const file = path.join(fixtureDir, name);
  fs.writeFileSync(file, body);
  return file;
};
const quote = (value) => `'${value.replaceAll("'", "'\\''")}'`;

before(() => {
  app = createApp({ dbPath: ':memory:' });
});

after(async () => {
  if (previousCommand === undefined) delete process.env.AI_KANBAN_WORKERS_CMD;
  else process.env.AI_KANBAN_WORKERS_CMD = previousCommand;
  if (previousRunsCommand === undefined) delete process.env.AI_KANBAN_RUNS_CMD;
  else process.env.AI_KANBAN_RUNS_CMD = previousRunsCommand;
  await app.close();
  fs.rmSync(fixtureDir, { recursive: true, force: true });
});

function callWorkers() {
  return callFeed('/api/workers');
}

function callFeed(url) {
  // Exercise the HTTP handler without requiring a listening socket.
  return new Promise((resolve) => {
    const req = { method: 'GET', url, headers: { host: 'localhost' } };
    const res = {
      writeHead(status) { this.status = status; },
      end(body) { resolve({ status: this.status, body: JSON.parse(body) }); },
    };
    app.server.emit('request', req, res);
  });
}

test('workers returns command JSON and safely handles quoted paths', async () => {
  const file = script('worker fixture with spaces.js', "process.stdout.write(JSON.stringify({as_of:'now',workers:[],orphans:[]}))");
  process.env.AI_KANBAN_WORKERS_CMD = `${quote(process.execPath)} ${quote(file)}`;
  const result = await callWorkers();
  assert.equal(result.status, 200);
  assert.deepEqual(result.body, { as_of: 'now', workers: [], orphans: [] });
});

test('workers command splits quoted, empty and escaped arguments without shell expansion', () => {
  assert.deepEqual(splitArgv('node "two words" \'\' escaped\\ path $(literal) ;'), ['node', 'two words', '', 'escaped path', '$(literal)', ';']);
  assert.throws(() => splitArgv('node "unfinished'), /quoting/);
  assert.throws(() => splitArgv("''"), /empty command/);
});

test('workers caches command output for 10 seconds', async (t) => {
  let now = Date.now();
  t.mock.method(Date, 'now', () => now);
  const count = path.join(fixtureDir, 'count file');
  const file = script('cached.js', `import fs from 'node:fs'; fs.appendFileSync(${JSON.stringify(count)}, 'x'); process.stdout.write(JSON.stringify({count: fs.readFileSync(${JSON.stringify(count)}, 'utf8').length}));`);
  process.env.AI_KANBAN_WORKERS_CMD = `${quote(process.execPath)} ${quote(file)}`;
  const [first, second] = await Promise.all([callWorkers(), callWorkers()]);
  assert.deepEqual(first.body, { count: 1 });
  assert.deepEqual(second.body, { count: 1 });
  assert.deepEqual((await callWorkers()).body, { count: 1 });
  assert.equal(fs.readFileSync(count, 'utf8'), 'x');
  now += 9999;
  assert.deepEqual((await callWorkers()).body, { count: 1 });
  now += 2;
  assert.deepEqual((await callWorkers()).body, { count: 2 });
});

test('workers returns 404 when the feed is unset', async () => {
  delete process.env.AI_KANBAN_WORKERS_CMD;
  const result = await callWorkers();
  assert.equal(result.status, 404);
  assert.deepEqual(result.body, { error: 'workers feed not configured' });
});

test('workers returns 502 for invalid JSON', async () => {
  const file = script('bad.js', "process.stdout.write('nope')");
  process.env.AI_KANBAN_WORKERS_CMD = `${quote(process.execPath)} ${quote(file)}`;
  const result = await callWorkers();
  assert.equal(result.status, 502);
  assert.equal(result.body.error, 'Workers command returned invalid JSON');
});

test('workers returns 502 for a nonzero command', async () => {
  const file = script('exit.js', 'process.exit(7)');
  process.env.AI_KANBAN_WORKERS_CMD = `${quote(process.execPath)} ${quote(file)}`;
  const result = await callWorkers();
  assert.equal(result.status, 502);
  assert.equal(result.body.error, 'Workers command exited with code 7');
});

test('workers returns 502 when the command times out', async () => {
  const file = script('slow.js', 'setTimeout(() => {}, 60_000)');
  process.env.AI_KANBAN_WORKERS_CMD = `${quote(process.execPath)} ${quote(file)}`;
  const result = await callWorkers();
  assert.equal(result.status, 502);
  assert.equal(result.body.error, 'Workers command timed out');
});

test('worker-runs returns command JSON and caches it for 60 seconds', async (t) => {
  let now = Date.now();
  t.mock.method(Date, 'now', () => now);
  const count = path.join(fixtureDir, 'runs count file');
  const file = script('runs-cached.js', `import fs from 'node:fs'; fs.appendFileSync(${JSON.stringify(count)}, 'x'); process.stdout.write(JSON.stringify({runs: fs.readFileSync(${JSON.stringify(count)}, 'utf8').length}));`);
  process.env.AI_KANBAN_RUNS_CMD = `${quote(process.execPath)} ${quote(file)}`;
  assert.deepEqual((await callFeed('/api/worker-runs')).body, { runs: 1 });
  assert.deepEqual((await callFeed('/api/worker-runs')).body, { runs: 1 });
  now += 59_999;
  assert.deepEqual((await callFeed('/api/worker-runs')).body, { runs: 1 });
  now += 2;
  assert.deepEqual((await callFeed('/api/worker-runs')).body, { runs: 2 });
  assert.equal(fs.readFileSync(count, 'utf8'), 'xx');
});

test('worker-runs returns 404 when the feed is unset', async () => {
  delete process.env.AI_KANBAN_RUNS_CMD;
  const result = await callFeed('/api/worker-runs');
  assert.equal(result.status, 404);
  assert.deepEqual(result.body, { error: 'worker runs feed not configured' });
});

test('worker-runs returns 502 for invalid JSON', async () => {
  const file = script('runs-bad.js', "process.stdout.write('nope')");
  process.env.AI_KANBAN_RUNS_CMD = `${quote(process.execPath)} ${quote(file)}`;
  const result = await callFeed('/api/worker-runs');
  assert.equal(result.status, 502);
  assert.equal(result.body.error, 'Worker runs command returned invalid JSON');
});

test('worker-runs returns 502 for a nonzero command', async () => {
  const file = script('runs-exit.js', 'process.exit(7)');
  process.env.AI_KANBAN_RUNS_CMD = `${quote(process.execPath)} ${quote(file)}`;
  const result = await callFeed('/api/worker-runs');
  assert.equal(result.status, 502);
  assert.equal(result.body.error, 'Worker runs command exited with code 7');
});
