import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { startServer } from './helpers.js';

test('LAB_PAGES serves only the lineage and knowledge-graph allowlist', async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'labpages-'));
  fs.writeFileSync(path.join(dir, 'lineage.html'), '<h1>lineage</h1>');
  fs.mkdirSync(path.join(dir, 'kg'));
  fs.writeFileSync(path.join(dir, 'kg', 'kg.html'), '<h1>kg</h1>');
  fs.writeFileSync(path.join(dir, 'kg', 'other.txt'), 'no');
  fs.writeFileSync(path.join(dir, 'secret.txt'), 'no');
  const srv = await startServer();
  const get = async (p) => { const r = await fetch(srv.base + p); return [r.status, r.headers.get('content-type'), await r.text()]; };
  try {
    delete process.env.LAB_PAGES;
    assert.equal((await get('/lab/lineage.html'))[0], 404);
    process.env.LAB_PAGES = dir;
    const [status, type, body] = await get('/lab/lineage.html');
    assert.deepEqual([status, body], [200, '<h1>lineage</h1>']);
    assert.match(type, /text\/html/);
    assert.equal((await get('/lab/lineage.json'))[0], 404); // allowlisted but missing
    assert.deepEqual((await get('/lab/kg/kg.html')).filter((_, i) => i !== 1), [200, '<h1>kg</h1>']);
    assert.equal((await get('/lab/kg/kg.json'))[0], 404);
    fs.mkdirSync(path.join(dir, 'replay', '001-t1-plan'), { recursive: true });
    fs.writeFileSync(path.join(dir, 'replay', 'index.html'), '<h1>replay</h1>');
    fs.writeFileSync(path.join(dir, 'replay', '001-t1-plan', 'state.json'), '{"stage":"plan"}');
    assert.deepEqual((await get('/lab/replay/index.html')).filter((_, i) => i !== 1), [200, '<h1>replay</h1>']);
    assert.deepEqual((await get('/lab/replay/001-t1-plan/state.json')).filter((_, i) => i !== 1), [200, '{"stage":"plan"}']);
    for (const p of ['/lab/replay/', '/lab/replay/../secret.txt', '/lab/replay/001-t1-plan/../../secret.txt', '/lab/replay/a/b/c.json', '/lab/replay/%2e%2e/secret.txt']) {
      assert.equal((await get(p))[0], 404, p);
    }
    for (const p of ['/lab/secret.txt', '/lab/', '/lab/../package.json', '/lab/%2e%2e/package.json', '/lab/lineage.html/../secret.txt', '/lab/kg/other.txt', '/lab/kg/', '/lab/kg/../secret.txt', '/lab/kg/%2e%2e/secret.txt']) {
      assert.equal((await get(p))[0], 404, p);
    }
  } finally {
    delete process.env.LAB_PAGES;
    await srv.close();
  }
});

test('Lineage and Knowledge graph are in-board views, not new-tab links', () => {
  const html = fs.readFileSync(new URL('../public/index.html', import.meta.url), 'utf8');
  const app = fs.readFileSync(new URL('../public/app.js', import.meta.url), 'utf8');
  for (const v of ['lineage', 'kg']) assert.match(html, new RegExp(`<button[^>]*data-view="${v}"`));
  assert.doesNotMatch(html, /id="(lineage|kg)-link"/);
  assert.match(app, /lineage: \{ src: '\/lab\/lineage\.html'/);
  assert.match(app, /kg: \{ src: '\/lab\/kg\/kg\.html'/);
});
