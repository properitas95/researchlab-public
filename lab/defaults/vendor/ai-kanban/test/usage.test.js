import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { createApp } from '../src/server.js';

let app;
const previousCommand = process.env.AI_KANBAN_USAGE_CMD;
const fixtureDir = fs.mkdtempSync(path.join(os.tmpdir(), 'kanban-usage-'));
const script = (name, body) => {
  const file = path.join(fixtureDir, name);
  fs.writeFileSync(file, body);
  return file;
};
const quote = (value) => `'${value.replaceAll("'", "'\\''")}'`;

before(() => { app = createApp({ dbPath: ':memory:' }); });
after(async () => {
  if (previousCommand === undefined) delete process.env.AI_KANBAN_USAGE_CMD;
  else process.env.AI_KANBAN_USAGE_CMD = previousCommand;
  await app.close();
  fs.rmSync(fixtureDir, { recursive: true, force: true });
});

function callUsage() {
  return new Promise((resolve) => {
    const req = { method: 'GET', url: '/api/usage', headers: { host: 'localhost' } };
    const res = {
      writeHead(status) { this.status = status; },
      end(body) { resolve({ status: this.status, body: JSON.parse(body) }); },
    };
    app.server.emit('request', req, res);
  });
}

test('usage returns command JSON', async () => {
  const file = script('usage.js', "process.stdout.write(JSON.stringify({as_of:'now',total:{input:12,cached_input:4,output:3},by_kind_model:[]}))");
  process.env.AI_KANBAN_USAGE_CMD = `${quote(process.execPath)} ${quote(file)}`;
  assert.deepEqual(await callUsage(), { status: 200, body: { as_of: 'now', total: { input: 12, cached_input: 4, output: 3 }, by_kind_model: [] } });
});

test('usage shares concurrent results and caches for 60 seconds', async (t) => {
  let now = Date.now();
  t.mock.method(Date, 'now', () => now);
  const count = path.join(fixtureDir, 'count');
  const file = script('cached.js', `import fs from 'node:fs'; fs.appendFileSync(${JSON.stringify(count)}, 'x'); process.stdout.write(JSON.stringify({count: fs.readFileSync(${JSON.stringify(count)}, 'utf8').length}));`);
  process.env.AI_KANBAN_USAGE_CMD = `${quote(process.execPath)} ${quote(file)}`;
  const [first, second] = await Promise.all([callUsage(), callUsage()]);
  assert.deepEqual(first.body, { count: 1 });
  assert.deepEqual(second.body, { count: 1 });
  now += 59_999;
  assert.deepEqual((await callUsage()).body, { count: 1 });
  now += 2;
  assert.deepEqual((await callUsage()).body, { count: 2 });
});

test('usage returns 404 when unset', async () => {
  delete process.env.AI_KANBAN_USAGE_CMD;
  assert.deepEqual(await callUsage(), { status: 404, body: { error: 'usage feed not configured' } });
});

test('usage returns 502 for invalid JSON and command failure', async () => {
  const bad = script('bad.js', "process.stdout.write('nope')");
  process.env.AI_KANBAN_USAGE_CMD = `${quote(process.execPath)} ${quote(bad)}`;
  assert.equal((await callUsage()).status, 502);
  const fail = script('fail.js', 'process.exit(7)');
  process.env.AI_KANBAN_USAGE_CMD = `${quote(process.execPath)} ${quote(fail)}`;
  const result = await callUsage();
  assert.equal(result.status, 502);
  assert.equal(result.body.error, 'Usage command exited with code 7');
});
