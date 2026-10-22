export function layoutGraph(graph = {}) {
  const nodes = new Map((graph.nodes ?? []).map((n) => [n.id, { ...n, layer: 0, order: 0, predecessors: [], successors: [] }]));
  const edges = (graph.edges ?? []).filter((e) => nodes.has(e.from) && nodes.has(e.to));
  for (const e of edges) {
    nodes.get(e.to).predecessors.push(nodes.get(e.from));
    nodes.get(e.from).successors.push(nodes.get(e.to));
  }
  const pending = new Map([...nodes.values()].map((n) => [n.id, n.predecessors.length]));
  const ready = [...nodes.values()].filter((n) => !pending.get(n.id));
  for (let i = 0; i < ready.length; i++) {
    const n = ready[i];
    for (const next of n.successors) {
      next.layer = Math.max(next.layer, n.layer + 1);
      pending.set(next.id, pending.get(next.id) - 1);
      if (!pending.get(next.id)) ready.push(next);
    }
  }
  // Cyclic input has no longest-path layering; retain every card and flag it.
  for (const n of nodes.values()) if (pending.get(n.id)) n.layer = 0;
  const layers = [];
  for (const n of nodes.values()) (layers[n.layer] ??= []).push(n);
  const center = (n) => {
    const before = n.predecessors.filter((p) => p.layer < n.layer);
    return before.length ? before.reduce((sum, p) => sum + p.order, 0) / before.length : 0;
  };
  for (const layer of layers) {
    layer.sort((a, b) => center(a) - center(b));
    layer.forEach((n, i) => { n.order = i; n.x = 20 + n.layer * 280; n.y = 20 + i * 112; });
  }
  return { nodes: [...nodes.values()].map(({ predecessors, successors, ...n }) => n), edges, cyclic: ready.length !== nodes.size };
}

export const isDonePertNode = (node) => node.done === true ||
  (!Object.hasOwn(node, 'done') && (node.status === 'done' || (node.te != null && Number(node.te) === 0)));

export function layoutPert(pert = {}, mode = 'time', showDone = false) {
  const doneNodes = (pert.nodes ?? []).filter(isDonePertNode);
  const openNodes = (pert.nodes ?? []).filter((node) => !isDonePertNode(node));
  const graph = layoutGraph(mode === 'layered' ? pert : { ...pert, nodes: openNodes });
  graph.nodes = graph.nodes.filter((node) => !isDonePertNode(node));
  const nodeWidth = 200, nodeHeight = 64, top = 68;
  const gap = mode === 'layered' ? 36 : 16;
  mode = mode === 'layered' ? 'layered' : 'time';
  const hour = (node) => Number.isFinite(Number(node.es)) ? Number(node.es) : 0;
  const starts = graph.nodes.map(hour).sort((a, b) => a - b);
  // Spread distinct start times enough to keep at most six overlapping boxes where possible.
  const pixelsPerHour = mode === 'time' ? Math.max(24, ...starts.slice(6).map((start, i) =>
    start > starts[i] ? (nodeWidth + gap) / (start - starts[i]) : 24)) : 24;
  const minHour = Math.min(0, ...graph.nodes.map(hour));
  const maxHour = Math.max(0, ...graph.nodes.map(hour));
  const originX = 32 + (showDone && doneNodes.length ? nodeWidth + 24 : 0) - (mode === 'time' ? minHour * pixelsPerHour : 0);
  const nodes = graph.nodes.map((node) => ({
    ...node,
    x: mode === 'time' ? originX + hour(node) * pixelsPerHour : originX + node.layer * 288,
  }));
  const laneEnds = [];
  for (const node of [...nodes].sort((a, b) => a.x - b.x || a.layer - b.layer || a.order - b.order)) {
    let lane = laneEnds.findIndex((end) => end <= node.x);
    if (lane < 0) lane = laneEnds.length;
    laneEnds[lane] = node.x + nodeWidth + gap;
    node.lane = lane;
    node.y = top + lane * (mode === 'layered' ? 164 : nodeHeight + gap);
  }
  const ticks = [];
  if (mode === 'time') {
    const span = maxHour - minHour;
    const roughStep = Math.max(1, span / 10);
    const magnitude = 10 ** Math.floor(Math.log10(roughStep));
    const step = [1, 2, 5, 10].find((factor) => factor * magnitude >= roughStep) * magnitude;
    for (let hours = Math.ceil(minHour / step) * step; hours <= maxHour; hours += step) {
      ticks.push({ hours, x: originX + hours * pixelsPerHour });
    }
  } else {
    for (const layer of [...new Set(nodes.map((node) => node.layer))].sort((a, b) => a - b)) {
      ticks.push({ layer, x: originX + layer * 288 });
    }
  }
  for (const node of nodes) { node.width = nodeWidth; node.height = nodeHeight; }
  if (showDone) nodes.push(...doneNodes.map((node, i) => ({ ...node, done: true, x: 32, y: top + i * 40, width: nodeWidth, height: 24 })));
  const byId = new Map(nodes.map((node) => [node.id, node]));
  const edges = (pert.edges ?? []).filter((edge) => byId.has(edge.from) && byId.has(edge.to)).map((edge) => {
    const a = byId.get(edge.from), b = byId.get(edge.to);
    const between = nodes.some((n) => n !== a && n !== b && n.y === a.y && n.x < b.x && n.x + n.width > a.x + a.width);
    if (a.y + a.height / 2 === b.y + b.height / 2 && b.x > a.x + a.width && !between) {
      return { ...edge, points: [[a.x + a.width, a.y + a.height / 2], [b.x, b.y + b.height / 2]] };
    }
    const startX = a.x + a.width / 2, endX = b.x + b.width / 2, startY = a.y - 14, endY = b.y - 14;
    const obstacles = nodes.filter((n) => n.y < Math.max(startY, endY) && n.y + n.height > Math.min(startY, endY));
    const candidates = [startX, endX, ...obstacles.flatMap((n) => [n.x - 14, n.x + n.width + 14])];
    const distance = (x) => Math.abs(startX - x) + Math.abs(endX - x);
    const gutter = candidates.filter((x) => obstacles.every((n) => x <= n.x - 8 || x >= n.x + n.width + 8))
      .sort((a, b) => distance(a) - distance(b) || a - b)[0];
    return { ...edge, points: [[startX, a.y], [startX, startY], [gutter, startY], [gutter, endY], [endX, endY], [endX, b.y]] };
  });
  return {
    nodes, edges, doneNodes, cyclic: graph.cyclic,
    width: Math.max(640, originX + 32, ...nodes.map((node) => node.x + node.width + 32)),
    height: Math.max(top + nodeHeight + 32, ...nodes.map((node) => node.y + node.height + 16)),
    nodeWidth, nodeHeight, originX, ticks, mode,
  };
}

export const isOpenEpic = (e) => !['done', 'closed', 'cancelled', 'canceled', 'archived'].includes(e.status);

export function frequentBottlenecks(epics = [], field, resources = {}) {
  const counts = new Map();
  for (const epic of epics.filter(isOpenEpic)) {
    const b = epic[field];
    const id = typeof b === 'string' ? b : [b?.id, b?.kind].find((key) => Object.hasOwn(resources, key)) ?? b?.id ?? b?.kind;
    if (id) counts.set(id, (counts.get(id) ?? 0) + 1);
  }
  const most = Math.max(0, ...counts.values());
  return [...counts].filter(([, count]) => count === most).sort(([a], [b]) => String(a).localeCompare(String(b)));
}

export function layoutWorkerRuns(runs = [], now = Date.now()) {
  const hour = 3_600_000, start = now - 24 * hour, workers = new Map();
  for (const run of runs) {
    const from = Date.parse(run.start), to = run.end == null ? now : Date.parse(run.end);
    if (!Number.isFinite(from) || !Number.isFinite(to) || to < from || to <= start || from > now) continue;
    const worker = String(run.worker ?? 'Unnamed');
    if (!workers.has(worker)) workers.set(worker, { worker, lastActivity: 0, bars: [] });
    const row = workers.get(worker), left = Math.max(start, from), right = Math.min(now, to);
    row.lastActivity = Math.max(row.lastActivity, right);
    row.bars.push({ run, from: left, to: right, x: (left - start) / (24 * hour) * 100, width: (right - left) / (24 * hour) * 100 });
  }
  const rows = [...workers.values()].sort((a, b) => b.lastActivity - a.lastActivity || a.worker.localeCompare(b.worker));
  for (const row of rows) {
    const ends = [];
    row.bars.sort((a, b) => a.from - b.from || a.to - b.to);
    for (const bar of row.bars) {
      let lane = ends.findIndex((end) => end <= bar.from);
      if (lane < 0) lane = ends.length;
      ends[lane] = bar.to;
      bar.lane = lane;
    }
    row.lanes = ends.length;
  }
  const ticks = [];
  for (let at = Math.ceil(start / hour) * hour; at <= now; at += hour) ticks.push({ at, x: (at - start) / (24 * hour) * 100 });
  return { start, end: now, ticks, rows };
}

export function orderEpics(epics) {
  const sorted = [...epics].sort((a, b) => Number(isOpenEpic(b)) - Number(isOpenEpic(a)) ||
    ((b.actual_or_forecast_hours ?? 0) - (b.expected_hours ?? 0)) - ((a.actual_or_forecast_hours ?? 0) - (a.expected_hours ?? 0)));
  const seen = new Set(), result = [], keys = new Set(sorted.map((e) => e.key));
  function visit(e, depth) {
    if (seen.has(e.key)) return;
    seen.add(e.key);
    result.push({ epic: e, depth });
    for (const child of sorted) if (child.parent === e.key) visit(child, depth + 1);
  }
  for (const e of sorted) if (!keys.has(e.parent)) visit(e, 0);
  for (const e of sorted) visit(e, 0);
  return result;
}
