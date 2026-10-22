import { layoutGraph, layoutPert, orderEpics, frequentBottlenecks, isDonePertNode } from './epics-layout.js';

const node = (tag, text) => {
  const el = document.createElement(tag);
  el.textContent = String(text ?? '');
  return el;
};
const hours = (value) => value != null && Number.isFinite(Number(value)) ? `${Number(value).toLocaleString('ko-KR', { minimumFractionDigits: 1, maximumFractionDigits: 1 })}h` : '—';
const count = (value) => value == null ? '—' : Number(value).toLocaleString('ko-KR');
const positive = (value) => Math.max(0, Number(value) || 0);
const bottleneck = (b) => b ? [b.kind, b.id, b.why].filter(Boolean).join(' · ') : '—';
const statuses = ['done', 'in_progress', 'review', 'todo', 'backlog', 'external'];
const statusLabels = { done: 'Done', in_progress: 'In progress', review: 'Review', todo: 'To do', backlog: 'Backlog', external: 'External' };
const finish = (value) => value != null && Number.isFinite(Date.parse(value)) ? `${new Date(value).toLocaleString('ko-KR', { timeZone: 'Asia/Seoul', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false })} KST` : '—';
const probability = (value) => value != null && Number.isFinite(Number(value)) ? `${(Number(value) * 100).toFixed(1)}%` : '—';
const binding = (value) => ({ pert: 'PERT', resources: 'Resources', resource: 'Resources', resource_bound: 'Resources', PERT: 'PERT' }[value] ?? value ?? '—');
function svg(tag, attrs = {}, text) {
  const el = document.createElementNS('http://www.w3.org/2000/svg', tag);
  for (const [key, value] of Object.entries(attrs)) el.setAttribute(key, String(value));
  if (text != null) el.textContent = String(text);
  return el;
}

function drawGraph(epic) {
  const wrap = node('div');
  wrap.className = 'epics-scroll';
  const { nodes, edges, cyclic } = layoutGraph(epic.graph ?? {});
  if (cyclic) wrap.append(node('p', 'Dependency cycle detected: layer display is incomplete.'));
  if (!nodes.length) { wrap.append(node('p', 'No dependency graph')); return wrap; }
  const canvas = svg('svg', { width: Math.max(...nodes.map((n) => n.x)) + 260, height: Math.max(...nodes.map((n) => n.y)) + 112, role: 'group', 'aria-label': 'Card dependency graph' });
  const defs = svg('defs'), marker = svg('marker', { id: 'epics-arrow', viewBox: '0 0 10 10', refX: 9, refY: 5, markerWidth: 6, markerHeight: 6, orient: 'auto-start-reverse' });
  marker.append(svg('path', { d: 'M 0 0 L 10 5 L 0 10 z', fill: 'context-stroke' }));
  defs.append(marker); canvas.append(defs);
  const byId = new Map(nodes.map((n) => [n.id, n])), path = epic.critical_path ?? [];
  const critical = new Set(path), criticalEdges = new Set(path.slice(1).map((id, i) => JSON.stringify([path[i], id])));
  for (const e of edges) {
    const a = byId.get(e.from), b = byId.get(e.to), x = a.x + 240, y = a.y + 42;
    const line = svg('path', { d: `M${x},${y} C${x + 20},${y} ${b.x - 20},${b.y + 42} ${b.x},${b.y + 42}`, class: criticalEdges.has(JSON.stringify([e.from, e.to])) ? 'epic-edge critical' : 'epic-edge', 'marker-end': 'url(#epics-arrow)' });
    line.append(svg('title', {}, `${e.from} to ${e.to} · ${e.source ?? ''}`)); canvas.append(line);
  }
  for (const n of nodes) {
    const status = n.external ? 'external' : statuses.includes(n.status) ? n.status : 'backlog';
    const link = svg('a', { href: `#/${encodeURIComponent(String(n.id))}`, 'aria-label': `${n.id}: ${n.title ?? ''}` });
    const group = svg('g', { transform: `translate(${n.x},${n.y})`, 'data-status': status, class: (n.critical || critical.has(n.id)) ? 'epic-node critical' : 'epic-node' });
    group.append(svg('title', {}, `${n.id}: ${n.title ?? ''}\n${n.status ?? ''} · ${n.stage ?? ''}\nExpected ${hours(n.expected_hours)} · worker ${hours(n.actual_agent_hours)}\n${n.tier ?? ''} · ${n.estimate ?? ''} · ${n.work_type ?? ''}`));
    group.append(svg('rect', { width: 240, height: 86, rx: 8 }), svg('text', { x: 10, y: 20 }, String(n.id).slice(0, 30)), svg('text', { x: 10, y: 42 }, String(n.title ?? '').slice(0, 29)), svg('text', { x: 10, y: 65 }, status), svg('text', { x: 10, y: 102 }, `Slack: ${hours(n.slack_hours)}`));
    link.append(group); canvas.append(link);
  }
  wrap.append(canvas);
  return wrap;
}

function drawPert(pert, mode, showDone) {
  const wrap = node('div'); wrap.className = 'epics-scroll pert-scroll'; wrap.tabIndex = 0;
  wrap.setAttribute('aria-label', 'PERT chart scroll');
  const layout = layoutPert(pert, mode, showDone), { nodes, edges, width, height } = layout;
  if (layout.cyclic) wrap.append(node('p', 'Dependency cycle detected: layer display is incomplete.'));
  if (!nodes.length) { wrap.append(node('p', 'No PERT cards')); return wrap; }
  const canvas = svg('svg', { width, height, role: 'group', 'aria-label': mode === 'time' ? 'PERT expected start time chart' : 'PERT dependency layer chart' });
  const defs = svg('defs'), marker = svg('marker', { id: 'pert-arrow', viewBox: '0 0 10 10', refX: 9, refY: 5, markerWidth: 6, markerHeight: 6, orient: 'auto' });
  marker.append(svg('path', { d: 'M 0 0 L 10 5 L 0 10 z', fill: 'context-stroke' })); defs.append(marker); canvas.append(defs);
  canvas.append(svg('text', { x: 8, y: 18, class: 'pert-axis-label' }, mode === 'time' ? 'Expected start ES · time (h)' : 'Dependency depth'));
  for (const tick of layout.ticks) {
    canvas.append(svg('line', { x1: tick.x, x2: tick.x, y1: 48, y2: height - 8, class: 'pert-tick' }), svg('text', { x: tick.x + 4, y: 40, class: 'pert-axis-label' }, mode === 'time' ? `${tick.hours.toFixed(1)}h` : `Level ${tick.layer}`));
  }
  if (mode === 'time') canvas.append(svg('line', { x1: layout.originX, x2: layout.originX, y1: 48, y2: height - 8, class: 'pert-now' }), svg('text', { x: layout.originX + 4, y: 57, class: 'pert-axis-label' }, 'Now 0h'));
  for (const edge of edges) {
    const d = edge.points.map(([x, y], i) => `${i ? 'L' : 'M'}${x},${y}`).join(' ');
    const path = svg('path', { d, class: `epic-edge pert-edge${edge.critical ? ' critical' : ''}`, 'marker-end': 'url(#pert-arrow)' });
    path.append(svg('title', {}, `${edge.from} -> ${edge.to} · ${edge.source ?? ''}`)); canvas.append(path);
  }
  for (const n of nodes) {
    const status = n.done ? 'done' : statuses.includes(n.status) ? n.status : 'backlog';
    const link = svg('a', { href: `#/${encodeURIComponent(String(n.id))}`, 'aria-label': `${n.id} · ${n.title ?? ''}` });
    const group = svg('g', { transform: `translate(${n.x},${n.y})`, 'data-status': status, 'data-external': String(Boolean(n.external)), class: `epic-node pert-node${n.done ? ' pert-done-chip' : ''}${n.critical ? ' critical' : n.near_critical ? ' near-critical' : ''}` });
    group.append(svg('title', {}, `${n.id} · ${n.title ?? ''}\n${statusLabels[status]} · confidence ${n.confidence ?? '—'}\nOptimistic ${hours(n.o)} · likely ${hours(n.m)} · pessimistic ${hours(n.p)}\nSD ${hours(n.sd)} · worker ${hours(n.agent_hours_te)}${n.open_question ? '\nOpen question' : ''}`));
    group.append(svg('rect', { width: n.width, height: n.height, rx: 5 }));
    const heading = svg('foreignObject', { x: 8, y: 3, width: n.width - (n.open_question ? 34 : 16), height: 20 });
    const label = node('div', `${n.id} · ${n.title ?? ''}`); label.className = 'pert-node-title';
    heading.append(label); group.append(heading);
    if (!n.done) {
      const value = (v) => v != null && Number.isFinite(Number(v)) ? Number(v).toFixed(1) : '—';
      const rows = [`ES ${value(n.es)} · TE ${value(n.te)}±${value(n.sd)} · EF ${value(n.ef)}`,
        `LS ${value(n.ls)} · slack ${value(n.slack)} · LF ${value(n.lf)}`];
      group.append(svg('path', { d: `M0,24 H${n.width}`, class: 'pert-grid' }));
      for (const [i, text] of rows.entries()) {
        const row = svg('foreignObject', { x: 8, y: 26 + i * 18, width: n.width - 16, height: 18 });
        const metrics = node('div', text); metrics.className = 'pert-node-metrics'; metrics.title = text;
        row.append(metrics); group.append(row);
      }
    }
    if (n.open_question) {
      const badge = svg('g', { class: 'pert-question', 'aria-label': 'Open question' });
      badge.append(svg('circle', { cx: n.width - 13, cy: 12, r: 8 }), svg('text', { x: n.width - 17, y: 16 }, '?')); group.append(badge);
    }
    link.append(group); canvas.append(link);
  }
  wrap.append(canvas); return wrap;
}

function pertDetail(epic, mode, setMode, showDone, setShowDone) {
  const pert = epic.pert, block = node('section'); block.className = 'epic-pert';
  block.append(node('h4', 'PERT completion forecast'));
  const summary = node('div'); summary.className = 'pert-summary';
  summary.append(node('b', `TE ${hours(pert.te_hours)} ± SD ${hours(pert.sd_hours)}`));
  for (const p of ['p50', 'p85', 'p95']) summary.append(node('span', `${p} ${finish(pert[`${p}_at`])} · ${hours(pert[`${p}_hours`])}`));
  summary.append(node('span', ['24h', '48h', '7d'].map((key) => `P(${key}) ${probability(pert.prob_within?.[key])}`).join(' · ')), node('span', `Constraint ${binding(pert.binding)} · PERT ${hours(pert.te_hours)} / resources ${hours(pert.constrained_hours)}`));
  const controls = node('div'); controls.className = 'pert-controls';
  for (const [value, label] of [['time', 'ES time axis'], ['layered', 'Dependency layers']]) {
    const button = node('button', label); button.type = 'button'; button.className = 'ops-filter-chip'; button.setAttribute('aria-pressed', String(mode === value)); button.addEventListener('click', () => setMode(value)); controls.append(button);
  }
  const legend = node('div'); legend.className = 'epic-legend';
  for (const status of statuses) { const label = node('span', statusLabels[status]); label.dataset.status = status; legend.append(label); }
  legend.append(node('span', 'Thick red outline/arrow = critical · orange = near-critical · dashed = external · ? = open question'));
  const body = node('div'), chart = node('div'), actions = node('aside'); body.className = 'pert-body'; chart.className = 'pert-chart'; actions.className = 'pert-accelerate';
  const completed = node('div'); completed.className = 'pert-completed';
  const doneNodes = (pert.nodes ?? []).filter(isDonePertNode);
  completed.append(node('span', `Done ${doneNodes.length}`));
  for (const card of doneNodes) {
    const link = node('a', card.id); link.href = `#/${encodeURIComponent(String(card.id))}`;
    link.title = `${card.id} · ${card.title ?? ''}`; completed.append(link);
  }
  const toggle = node('button', 'Show done cards'); toggle.type = 'button'; toggle.className = 'ops-filter-chip';
  toggle.setAttribute('aria-pressed', String(showDone)); toggle.addEventListener('click', () => setShowDone(!showDone));
  controls.append(toggle);
  chart.append(controls, legend, completed, drawPert(pert, mode, showDone)); actions.append(node('h4', 'Speed-up actions'));
  const list = node('ol'), accelerations = [...(epic.accelerate ?? [])].filter((action) => Number(action.saves_hours) >= 0.25).sort((a, b) => positive(b.saves_hours) - positive(a.saves_hours));
  for (const action of accelerations) {
    const item = node('li'); item.append(node('b', `${hours(action.saves_hours)} saved · ${action.action ?? '—'}`));
    if (action.user_action) { const tag = node('span', 'User action'); tag.className = 'pert-user-action'; item.append(tag); }
    item.append(node('p', action.why));
    for (const key of action.cards ?? []) { const link = node('a', key); link.href = `#/${encodeURIComponent(String(key))}`; item.append(link, node('span', ' ')); }
    list.append(item);
  }
  actions.append(list); if (!accelerations.length) actions.append(node('p', 'No speed-up actions reported'));
  body.append(chart, actions); block.append(summary, body); return block;
}

export function createEpicsRenderer(content) {
  let selected, pertMode = 'time', showDone = false;
  function detail(epic, target) {
    selected = epic.key;
    target.replaceChildren(node('h3', `${epic.key} · ${epic.title}`));
    target.append(node('h4', 'Delay causes'));
    const delays = Array.isArray(epic.delay) ? epic.delay : [], total = delays.reduce((sum, d) => sum + positive(d.hours), 0);
    const bar = node('div'), list = node('ul');
    bar.className = 'epic-delays';
    delays.forEach((delay, i) => {
      const label = `${delay.cause}: ${hours(delay.hours)}`, segment = node('span', label);
      segment.style.width = `${total ? positive(delay.hours) / total * 100 : 0}%`;
      segment.style.background = `var(--c-${statuses[i % 5]})`;
      segment.title = label; bar.append(segment);
      const item = node('li', label), evidence = node('ul');
      for (const key of delay.cards ?? []) {
        const link = node('a', key); link.href = `#/${encodeURIComponent(String(key))}`;
        item.append(node('span', ' '), link);
      }
      for (const text of delay.evidence ?? []) evidence.append(node('li', text));
      item.append(evidence); list.append(item);
    });
    target.append(bar, list);
    if (!delays.length) target.append(node('p', 'No delays reported'));
    if (epic.pert) {
      target.append(pertDetail(epic, pertMode, (mode) => { pertMode = mode; detail(epic, target); }, showDone, (value) => { showDone = value; detail(epic, target); }));
      return;
    }
    target.append(node('h4', 'Dependency graph · thick lines = critical path'));
    const legend = node('div'); legend.className = 'epic-legend';
    for (const status of statuses) { const label = node('span', status); label.dataset.status = status; legend.append(label); }
    target.append(legend, drawGraph(epic));
  }
  function render(data) {
    const epics = Array.isArray(data?.epics) ? data.epics : [];
    content.replaceChildren();
    const strip = node('div'); strip.className = 'epic-resources';
    const nextResources = new Set(frequentBottlenecks(epics, 'next_bottleneck', data?.resources).map(([id]) => id));
    const kinds = [...new Set(['codex', 'glm', 'review', 'merge', 'director', ...Object.keys(data?.resources ?? {})])];
    for (const kind of kinds) {
      const resource = data?.resources?.[kind], box = node('div');
      const next = nextResources.has(kind);
      box.className = next ? 'epic-resource next' : 'epic-resource';
      box.append(node('b', `${kind}${next ? ' · next bottleneck' : ''}`));
      if (kind === 'director') box.append(node('p', `Median response: ${hours(resource?.median_answer_hours)}`));
      else {
        const utilization = resource?.utilization, meter = node('progress'); meter.max = 1;
        meter.value = Math.min(1, positive(utilization)); meter.setAttribute('aria-label', `${kind} utilization`);
        box.append(node('p', `${utilization == null ? '—' : `${Math.round(meter.value * 100)}%`} · ${count(resource?.slots)} slots`), meter, node('p', `Queue: ${hours(resource?.queue_hours)}`));
      }
      strip.append(box);
    }
    const scroll = node('div'), table = node('table'); scroll.className = 'epics-scroll';
    const head = table.createTHead().insertRow();
    for (const label of ['Epic', 'Progress', 'Epic time', 'Worker time', 'Expected / actual·forecast', 'Due', 'TE ± SD', 'p85 done (KST)', 'Constraint', 'Top action', 'Current bottleneck', 'Next bottleneck']) head.append(node('th', label));
    const body = table.createTBody(), details = node('section'); details.className = 'epic-detail';
    details.setAttribute('aria-label', 'Epic details');
    for (const { epic: e, depth } of orderEpics(epics)) {
      const row = body.insertRow(), title = row.insertCell(), button = node('button', `${e.key} · ${e.title}${e.proposed ? ' · proposed' : ''}`);
      button.className = 'epic-select'; button.style.paddingInlineStart = `${depth * 18}px`;
      button.addEventListener('click', () => {
        selected = selected === e.key ? undefined : e.key;
        details.replaceChildren();
        if (selected) detail(e, details);
        for (const b of body.querySelectorAll('button')) b.setAttribute('aria-pressed', String(b === button && selected === e.key));
      });
      button.setAttribute('aria-pressed', String(e.key === selected)); title.append(button, node('small', e.status));
      const progress = node('td', `${count(e.cards_done)}/${count(e.cards_total)} cards · ${count(e.points_done)}/${count(e.points_total)} points`); progress.className = 'num';
      row.append(progress, Object.assign(node('td', hours(e.epic_hours)), { className: 'num' }));
      const agents = node('td', hours(e.agent_hours?.total)); agents.className = 'num';
      agents.title = Object.entries(e.agent_hours ?? {}).filter(([kind]) => kind !== 'total').map(([kind, value]) => `${kind}: ${hours(value)}`).join('\n'); row.append(agents);
      const forecast = node('td', `${hours(e.expected_hours)} / ${hours(e.actual_or_forecast_hours)}`), bar = node('div'), planned = node('span');
      forecast.className = 'num';
      bar.className = positive(e.actual_or_forecast_hours) > positive(e.expected_hours) ? 'epic-forecast overrun' : 'epic-forecast';
      planned.style.width = `${positive(e.expected_hours) / Math.max(positive(e.expected_hours), positive(e.actual_or_forecast_hours), 1) * 100}%`;
      bar.title = `Over: ${hours(Number(e.actual_or_forecast_hours) - Number(e.expected_hours))}`;
      bar.append(planned); forecast.append(bar);
      const action = node('td', e.accelerate?.[0]?.action ?? '—'); action.className = 'epic-top-action'; action.title = action.textContent;
      row.append(forecast, node('td', e.eta ?? '—'), node('td', e.pert ? `${hours(e.pert.te_hours)} ± ${hours(e.pert.sd_hours)}` : '—'), node('td', finish(e.pert?.p85_at)), node('td', binding(e.pert?.binding)), action, node('td', bottleneck(e.current_bottleneck)), node('td', bottleneck(e.next_bottleneck)));
    }
    scroll.append(table); content.append(strip, scroll, details);
    if (!epics.length) content.append(node('p', 'No epic'));
    const chosen = epics.find((e) => e.key === selected);
    if (chosen) {
      detail(chosen, details);
      for (const [i, { epic }] of orderEpics(epics).entries()) body.rows[i].querySelector('button').setAttribute('aria-pressed', String(epic.key === selected));
    }
  }
  return render;
}
