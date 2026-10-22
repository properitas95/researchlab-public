import test from 'node:test';
import assert from 'node:assert/strict';
import { layoutPert, isDonePertNode } from '../public/epics-layout.js';

function assertNoOverlap(layout) {
  for (let i = 0; i < layout.nodes.length; i++) {
    const a = layout.nodes[i];
    for (const b of layout.nodes.slice(i + 1)) {
      assert.ok(a.x + a.width <= b.x || b.x + b.width <= a.x ||
        a.y + a.height <= b.y || b.y + b.height <= a.y,
      `${a.id} overlaps ${b.id}`);
    }
  }
}

function assertEdgesAvoidBoxes(layout) {
  for (const edge of layout.edges) {
    for (let i = 1; i < edge.points.length; i++) {
      const [x1, y1] = edge.points[i - 1], [x2, y2] = edge.points[i];
      assert.ok([x1, y1, x2, y2].every(Number.isFinite));
      assert.ok(x1 === x2 || y1 === y2);
      for (const n of layout.nodes) {
        const crosses = x1 === x2
          ? x1 > n.x && x1 < n.x + n.width && Math.max(y1, y2) > n.y && Math.min(y1, y2) < n.y + n.height
          : y1 > n.y && y1 < n.y + n.height && Math.max(x1, x2) > n.x && Math.min(x1, x2) < n.x + n.width;
        assert.equal(crosses, false, `${edge.from} → ${edge.to} crosses ${n.id}`);
      }
    }
  }
}

test('PERT time layout maps ES proportionally and keeps zero visible for negative ES', () => {
  const result = layoutPert({ nodes: [{ id: 'a', es: -2 }, { id: 'b', es: 0 }, { id: 'c', es: 2 }] });
  const [a, b, c] = result.nodes;
  assert.equal(b.x - a.x, 48);
  assert.equal(c.x - b.x, 48);
  assert.equal(b.x, result.originX);
  assert.ok(a.x >= 0);
  assert.ok(result.ticks.some((tick) => tick.hours === 0 && tick.x === result.originX));
  assertNoOverlap(result);
});

test('PERT packs boxes with equal ES into distinct lanes and reuses free lanes', () => {
  const result = layoutPert({ nodes: [{ id: 'a', es: 0 }, { id: 'b', es: 0 }, { id: 'c', es: 20 }] });
  assert.notEqual(result.nodes[0].lane, result.nodes[1].lane);
  assert.equal(result.nodes[2].lane, result.nodes[0].lane);
  assertNoOverlap(result);
});

test('PERT keeps forty boxes readable in both modes and retains critical edge metadata', () => {
  const pert = {
    nodes: Array.from({ length: 40 }, (_, i) => ({ id: `n${i}`, es: Math.floor(i / 4) * 2 })),
    edges: Array.from({ length: 39 }, (_, i) => ({ from: `n${i}`, to: `n${i + 1}`, critical: true, source: 'feed' })),
  };
  pert.edges.push({ from: 'missing', to: 'n0', critical: true });
  for (const mode of ['time', 'layered']) {
    const result = layoutPert(pert, mode);
    assert.equal(result.nodes.length, 40);
    assert.equal(result.edges.length, 39);
    const { points, ...metadata } = result.edges[0];
    assert.deepEqual(metadata, pert.edges[0]);
    assert.equal(result.nodes[39].layer, 39);
    if (mode === 'layered') {
      assert.ok(result.nodes[39].x > result.nodes[0].x);
      assert.equal(result.ticks.length, 40);
      for (const node of result.nodes) {
        assert.ok(result.ticks.some((tick) => tick.layer === node.layer && tick.x === node.x));
      }
    }
    assertNoOverlap(result);
    assertEdgesAvoidBoxes(result);
    for (const node of result.nodes) {
      assert.ok(node.x + result.nodeWidth <= result.width);
      assert.ok(node.y + result.nodeHeight <= result.height);
    }
  }
});

test('PERT routes short-ES branches and skipped dependencies outside intervening boxes', () => {
  const pert = {
    nodes: [{ id: 'a', es: 0 }, { id: 'b', es: 1 }, { id: 'c', es: 2 }, { id: 'd', es: 30 }],
    edges: [{ from: 'a', to: 'b', critical: true }, { from: 'a', to: 'c' }, { from: 'a', to: 'd' }],
  };
  for (const mode of ['time', 'layered']) assertEdgesAvoidBoxes(layoutPert(pert, mode));
});

test('PERT is deterministic and does not mutate feed data', () => {
  const pert = { nodes: [{ id: 'a', es: 0, critical: true }, { id: 'b', es: 2 }], edges: [{ from: 'a', to: 'b' }] };
  const original = structuredClone(pert);
  assert.deepEqual(layoutPert(pert), layoutPert(pert));
  assert.deepEqual(pert, original);
});

test('PERT handles empty input, missing ES, cycles, and bounds the time ticks', () => {
  assert.deepEqual(layoutPert().nodes, []);
  const cyclic = layoutPert({
    nodes: [{ id: 'a' }, { id: 'b', es: 'invalid' }, { id: 'c', es: 1e6 }],
    edges: [{ from: 'a', to: 'b' }, { from: 'b', to: 'a' }],
  });
  assert.equal(cyclic.cyclic, true);
  assert.equal(cyclic.nodes[0].x, cyclic.originX);
  assert.equal(cyclic.nodes[1].x, cyclic.originX);
  assert.ok(cyclic.ticks.length <= 12);
  assertNoOverlap(cyclic);
});


test('PERT completion respects explicit done and falls back to status or zero TE only when absent', () => {
  for (const node of [{ done: true, te: 5 }, { status: 'done' }, { te: 0 }, { te: '0' }]) assert.equal(isDonePertNode(node), true);
  for (const node of [{ done: false, status: 'done', te: 0 }, {}, { te: null }, { te: 1 }, { te: 'invalid' }]) assert.equal(isDonePertNode(node), false);
});

test('PERT hides completed cards and their edges, or shows compact chips in a separate left column', () => {
  const pert = {
    nodes: [{ id: 'd1', done: true, es: 0 }, { id: 'd2', te: 0, es: 0 },
      { id: 'open', done: false, te: 0, es: 0 }, { id: 'next', te: 2, es: 1 }],
    edges: [{ from: 'd1', to: 'open' }, { from: 'd2', to: 'next' }, { from: 'open', to: 'next' }, { from: 'd1', to: 'd2' }],
  };
  for (const mode of ['time', 'layered']) {
    const hidden = layoutPert(pert, mode), shown = layoutPert(pert, mode, true);
    assert.deepEqual(hidden.nodes.map((node) => node.id), ['open', 'next']);
    assert.equal(hidden.doneNodes.length, 2);
    assert.deepEqual(hidden.edges.map((edge) => [edge.from, edge.to]), [['open', 'next']]);
    assert.equal(shown.edges.length, 4);
    for (const chip of shown.nodes.filter((node) => node.done)) {
      assert.equal(chip.height, 24);
      assert.ok(shown.nodes.filter((node) => !node.done).every((node) => chip.x + chip.width < node.x));
    }
    assertNoOverlap(shown);
    assertEdgesAvoidBoxes(shown);
  }
  assert.equal(layoutPert({ nodes: pert.nodes.slice(0, 2) }).nodes.length, 0);
  assert.equal(layoutPert({ nodes: pert.nodes.slice(0, 2) }, 'time', true).nodes.length, 2);
});

test('PERT fits twenty open cards with staggered ES in 600 high while completed cards consume no lanes', () => {
  const open = Array.from({ length: 20 }, (_, i) => ({ id: `open${i}`, te: 2, es: Math.floor(i / 4) * 4 }));
  const done = Array.from({ length: 30 }, (_, i) => ({ id: `done${i}`, done: true, es: 0 }));
  const result = layoutPert({ nodes: [...done, ...open] });
  assert.equal(result.nodes.length, 20);
  assert.equal(result.nodeWidth, 200);
  assert.equal(result.nodeHeight, 64);
  assert.ok(result.height <= 600, `chart height ${result.height}`);
  assert.deepEqual(result.nodes, layoutPert({ nodes: open }).nodes);
  assertNoOverlap(result);
});


test('PERT layered mode retains dependency depth and spacing when a completed predecessor is hidden', () => {
  const result = layoutPert({ nodes: [{ id: 'done', done: true }, { id: 'open' }, { id: 'next' }],
    edges: [{ from: 'done', to: 'open' }, { from: 'open', to: 'next' }] }, 'layered');
  assert.deepEqual(result.nodes.map((node) => node.layer), [1, 2]);
  assert.equal(result.nodes[1].x - result.nodes[0].x, 288);
  assert.equal(result.edges.length, 1);
});
