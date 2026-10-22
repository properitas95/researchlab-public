import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { createApp } from '../src/server.js';

let app;
const previousCommand = process.env.AI_KANBAN_AGENTS_CMD;
const fixtureDir = fs.mkdtempSync(path.join(os.tmpdir(), 'kanban-agents-'));
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
  if (previousCommand === undefined) delete process.env.AI_KANBAN_AGENTS_CMD;
  else process.env.AI_KANBAN_AGENTS_CMD = previousCommand;
  await app.close();
  fs.rmSync(fixtureDir, { recursive: true, force: true });
});

function callAgents() {
  return new Promise((resolve) => {
    const req = { method: 'GET', url: '/api/agents', headers: { host: 'localhost' } };
    const res = {
      writeHead(status) { this.status = status; },
      end(body) { resolve({ status: this.status, body: JSON.parse(body) }); },
    };
    app.server.emit('request', req, res);
  });
}

test('agents returns command JSON and caches concurrent requests for 60 seconds', async (t) => {
  let now = Date.now();
  t.mock.method(Date, 'now', () => now);
  const count = path.join(fixtureDir, 'count');
  const file = script('cached.js', `import fs from 'node:fs'; fs.appendFileSync(${JSON.stringify(count)}, 'x'); process.stdout.write(JSON.stringify({sessions: fs.readFileSync(${JSON.stringify(count)}, 'utf8').length}));`);
  process.env.AI_KANBAN_AGENTS_CMD = `${quote(process.execPath)} ${quote(file)}`;
  const [first, second] = await Promise.all([callAgents(), callAgents()]);
  assert.equal(first.status, 200);
  assert.deepEqual(first.body, { sessions: 1 });
  assert.deepEqual(second.body, { sessions: 1 });
  now += 59_999;
  assert.deepEqual((await callAgents()).body, { sessions: 1 });
  now += 2;
  assert.deepEqual((await callAgents()).body, { sessions: 2 });
  assert.equal(fs.readFileSync(count, 'utf8'), 'xx');
});

test('agents returns 404 when the feed is unset', async () => {
  delete process.env.AI_KANBAN_AGENTS_CMD;
  const result = await callAgents();
  assert.equal(result.status, 404);
  assert.deepEqual(result.body, { error: 'agents feed not configured' });
});

test('agents returns 502 for invalid JSON', async () => {
  const file = script('bad.js', "process.stdout.write('nope')");
  process.env.AI_KANBAN_AGENTS_CMD = `${quote(process.execPath)} ${quote(file)}`;
  const result = await callAgents();
  assert.equal(result.status, 502);
  assert.equal(result.body.error, 'Agents command returned invalid JSON');
});

test('agents returns 502 for a nonzero command', async () => {
  const file = script('exit.js', 'process.exit(7)');
  process.env.AI_KANBAN_AGENTS_CMD = `${quote(process.execPath)} ${quote(file)}`;
  const result = await callAgents();
  assert.equal(result.status, 502);
  assert.equal(result.body.error, 'Agents command exited with code 7');
});

test('agents returns 502 when the 20 second command deadline expires', async () => {
  const file = script('slow.js', 'setTimeout(() => {}, 60_000)');
  process.env.AI_KANBAN_AGENTS_CMD = `${quote(process.execPath)} ${quote(file)}`;
  const result = await callAgents();
  assert.equal(result.status, 502);
  assert.equal(result.body.error, 'Agents command timed out');
});
