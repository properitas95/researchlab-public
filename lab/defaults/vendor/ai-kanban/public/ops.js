import { createEpicsRenderer } from './epics.js';
import { layoutWorkerRuns, frequentBottlenecks } from './epics-layout.js';

const node = (tag, text) => {
  const el = document.createElement(tag);
  el.textContent = String(text ?? '');
  return el;
};
const rows = (value) => Array.isArray(value) ? value : [];
const number = (value, digits = 0) => value != null && Number.isFinite(Number(value))
  ? Number(value).toLocaleString('ko-KR', { maximumFractionDigits: digits }) : '—';
const percent = (value) => value == null ? '—' : `${number(Number(value) * 100, 1)}%`;
const tokens = (r) => (Number(r.input) || 0) + (Number(r.output) || 0);
const cardLink = (key) => {
  const link = node('a', key);
  link.className = 'task-link';
  link.href = `#/${encodeURIComponent(String(key ?? ''))}`;
  return link;
};
function table(target, labels, className = '') {
  const scroll = node('div'), grid = node('table'); scroll.className = 'epics-scroll';
  grid.className = className;
  const head = grid.createTHead().insertRow();
  for (const label of labels) head.append(node('th', label));
  scroll.append(grid); target.append(scroll);
  return grid.createTBody();
}
function cell(row, value, numeric = false) {
  const td = row.insertCell();
  if (value instanceof Node) td.append(value);
  else td.textContent = String(value ?? '—');
  if (numeric) td.className = 'num';
  return td;
}
const resultLabels = { submitted: 'Submitted', no_commit: 'No commit', running: 'Running', ended: 'Ended' };
const resultOf = (run) => Object.hasOwn(resultLabels, run.result) ? run.result : 'ended';
const kst = (value) => {
  const date = new Date(value);
  return Number.isFinite(date.getTime()) ? new Intl.DateTimeFormat('ko-KR', { timeZone: 'Asia/Seoul', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).format(date) : '—';
};
function badge(result) {
  const el = node('span', resultLabels[result]); el.className = 'ops-result'; el.dataset.result = result; return el;
}
function ellipsis(td, className = '') {
  td.className = `ops-ellipsis ${className}`; td.title = td.textContent; return td;
}
function boardCards(board) {
  const tasks = rows(board?.columns).flatMap((c) => rows(c.tasks)), epics = new Map(rows(board?.epics).map((e) => [e.key, e.title]));
  return new Map(tasks.map((task) => [task.key, { ...task, epicTitle: epics.get(task.epic) }]));
}
function renderWorkers(target, data, cards) {
  target.replaceChildren();
  const loops = rows(data.workers).filter((w) => /lead|loop/i.test(w.kind) || /_loop$/.test(w.name));
  const expected = ['dispatch_loop', 'review_merge_loop', 'bottleneck_loop'];
  const missing = expected.filter((name) => !loops.some((w) => w.name === name));
  const status = node('p', missing.length ? `Lead loops ${number(loops.length)} · missing: ${missing.join(', ')}` : `Lead loops ${number(loops.length)} OK`);
  status.className = missing.length ? 'ops-loop-status warning' : 'ops-loop-status'; target.append(status);
  const body = table(target, ['Kind', 'Worker', 'Model', 'Effort', 'Card', 'Title', 'Epic', 'Elapsed', 'Last record', 'Last log'], 'ops-live-table');
  for (const w of rows(data.workers).filter((w) => !loops.includes(w)).sort((a, b) => String(a.kind).localeCompare(String(b.kind)))) {
    const keys = Array.isArray(w.cards) ? w.cards : String(w.cards ?? '').split(/\s+/).filter(Boolean);
    for (const key of keys.length ? keys : ['']) {
      const task = cards.get(key), row = body.insertRow();
      for (const value of [w.kind, w.name, w.model, w.effort]) ellipsis(cell(row, value), 'ops-identity');
      cell(row, key ? cardLink(key) : '—');
      ellipsis(cell(row, task?.title), 'ops-card-title');
      ellipsis(cell(row, task?.epicTitle), 'ops-epic-title');
      cell(row, w.elapsed, true);
      const seconds = w.last_write_s == null ? NaN : Number(w.last_write_s);
      cell(row, !Number.isFinite(seconds) ? '—' : seconds < 60 ? 'just now' : seconds < 3600 ? `${number(Math.floor(seconds / 60))}m ago` : `${number(Math.floor(seconds / 3600))}h ago`);
      ellipsis(cell(row, w.last_line), 'worker-last-line');
    }
  }
  if (!body.rows.length) target.append(node('p', 'No running workers'));
  target.append(node('h3', 'In progress with no worker'));
  const list = node('ul'); list.className = 'ops-orphans';
  for (const orphan of rows(data.orphans)) {
    const item = node('li');
    item.append(cardLink(orphan.key), node('span', ` ${cards.get(orphan.key)?.title ?? orphan.title ?? ''}${orphan.waiting_on_director ? ' · waiting on director' : ''}`)); list.append(item);
  }
  target.append(list.children.length ? list : node('p', 'None'));
}
function renderTimeline(target, runs, cards) {
  const layout = layoutWorkerRuns(runs), scroll = node('div'), grid = node('div');
  scroll.className = 'ops-timeline'; scroll.tabIndex = 0; scroll.setAttribute('aria-label', 'Worker runs, last 24 hours');
  grid.className = 'ops-timeline-grid';
  const corner = node('div', 'Worker / KST'), axis = node('div');
  corner.className = 'ops-timeline-label ops-timeline-corner'; axis.className = 'ops-timeline-axis';
  for (const tick of layout.ticks) {
    const label = node('span', new Intl.DateTimeFormat('ko-KR', { timeZone: 'Asia/Seoul', hour: '2-digit', hourCycle: 'h23' }).format(tick.at));
    label.style.left = `${tick.x}%`; label.title = kst(tick.at); axis.append(label);
  }
  grid.append(corner, axis);
  for (const row of layout.rows) {
    const label = node('div', row.worker), track = node('div');
    label.className = 'ops-timeline-label'; label.title = row.worker; track.className = 'ops-timeline-track';
    track.style.height = `${Math.max(34, row.lanes * 26 + 8)}px`;
    for (const tick of layout.ticks) { const line = node('span'); line.className = 'ops-hour-line'; line.style.left = `${tick.x}%`; track.append(line); }
    for (const bar of row.bars) {
      const r = bar.run, link = cardLink(r.card); link.className = 'ops-run-bar'; link.dataset.result = resultOf(r);
      link.style.left = `${bar.x}%`; link.style.width = `${bar.width}%`; link.style.top = `${bar.lane * 26 + 4}px`;
      link.title = `${r.card ?? '—'}: ${cards.get(r.card)?.title ?? 'No title'}\n${r.model ?? '—'} · ${r.effort ?? '—'} · ${resultLabels[resultOf(r)]}\nBusy ${number(r.busy_hours, 2)}h · input ${number(r.input)} / output ${number(r.output)} tokens\n${kst(r.start)} ~ ${r.end == null ? 'now' : kst(r.end)}`;
      link.setAttribute('aria-label', link.title); track.append(link);
    }
    grid.append(label, track);
  }
  scroll.append(grid); target.append(scroll);
  if (!layout.rows.length) target.append(node('p', 'No runs in the last 24 hours'));
}
function renderRuns(target, data, cards, filters, limit, onChange) {
  const previousScroll = target.querySelector('.ops-timeline');
  const top = previousScroll?.scrollTop ?? 0, left = previousScroll?.scrollLeft ?? 0;
  const runs = rows(data.runs); target.replaceChildren();
  const legend = node('div'); legend.className = 'ops-run-legend';
  legend.append(node('span', 'Last 24 hours · KST'));
  for (const result of Object.keys(resultLabels)) legend.append(badge(result));
  target.append(legend); renderTimeline(target, runs, cards);
  const timeline = target.querySelector('.ops-timeline');
  if (timeline) { timeline.scrollTop = top; timeline.scrollLeft = left; }
  target.append(node('h3', 'Runs'));
  const chips = node('div'); chips.className = 'ops-run-filters';
  for (const [key, label] of [['kind', 'Kind'], ['model', 'Model'], ['effort', 'Effort'], ['result', 'Result']]) {
    const group = node('div'); group.setAttribute('role', 'group'); group.setAttribute('aria-label', label); group.append(node('b', label));
    const values = [...new Set(runs.map((r) => String(r[key] ?? '—')))].sort();
    for (const value of [null, ...values]) {
      const button = node('button', value == null ? 'All' : key === 'result' ? resultLabels[value] ?? value : value);
      button.type = 'button'; button.className = 'ops-filter-chip'; button.setAttribute('aria-pressed', String((filters[key] ?? null) === value));
      button.addEventListener('click', () => onChange(key, value)); group.append(button);
    }
    chips.append(group);
  }
  target.append(chips);
  const filtered = runs.filter((r) => Object.entries(filters).every(([key, value]) => value == null || String(r[key] ?? '—') === value))
    .sort((a, b) => Date.parse(b.start) - Date.parse(a.start));
  const body = table(target, ['Worker', 'Kind', 'Model', 'Effort', 'Tier', 'Card · title · epic', 'Start (KST)', 'Elapsed (h)', 'Busy (h)', 'Input / output', 'Tool calls', 'Result'], 'ops-runs-table');
  for (const r of filtered.slice(0, limit)) {
    const row = body.insertRow();
    for (const value of [r.worker, r.kind, r.model, r.effort, r.tier]) ellipsis(cell(row, value), 'ops-identity');
    row.children[0].title = `${r.worker ?? '—'}\nSessions ${number(r.sessions)}\nLog: ${r.log ?? '—'}`;
    const task = cards.get(r.card), card = node('div'), title = node('span', task?.title ?? 'No title');
    card.className = 'ops-run-card'; title.className = 'ops-ellipsis'; title.title = title.textContent;
    card.append(cardLink(r.card), title, node('small', task?.epicTitle ?? 'No epic')); cell(row, card);
    cell(row, kst(r.start));
    cell(row, number(r.wall_hours, 2), true); cell(row, number(r.busy_hours, 2), true);
    const tokenCell = cell(row, `${number(r.input)} / ${number(r.output)}`, true);
    tokenCell.title = `Cached input: ${number(r.cached_input)}`;
    cell(row, number(r.tool_calls), true); cell(row, badge(resultOf(r)));
  }
  const footer = node('div'); footer.className = 'ops-run-footer'; footer.append(node('span', `${number(Math.min(limit, filtered.length))} / ${number(filtered.length)} runs`));
  if (filtered.length > limit) {
    const more = node('button', 'Show more'); more.type = 'button'; more.className = 'btn btn-ghost'; more.addEventListener('click', () => onChange('limit', limit + 50)); footer.append(more);
  }
  target.append(footer, node('h3', 'Summary by kind, model, effort'));
  const summary = table(target, ['Kind', 'Model', 'Effort', 'Runs', 'Submit rate', 'No-commit rate', 'Busy (h)', 'Busy (h) per submitted card', 'Tokens per submitted card']);
  for (const r of rows(data.by_model_effort)) {
    const row = summary.insertRow(), submittedCards = new Set(runs.filter((run) => run.result === 'submitted' && run.card && ['kind', 'model', 'effort'].every((key) => run[key] === r[key])).map((run) => run.card)).size;
    for (const key of ['kind', 'model', 'effort']) ellipsis(cell(row, r[key]), 'ops-identity');
    cell(row, number(r.runs), true); cell(row, percent(r.runs ? r.submitted / r.runs : null), true);
    const noCommit = r.runs ? r.no_commit / r.runs : null, rate = cell(row, percent(noCommit), true); if (noCommit > 0.3) rate.className += ' ops-alert';
    cell(row, number(r.busy_hours, 2), true); cell(row, number(submittedCards ? r.busy_hours / submittedCards : null, 2), true);
    cell(row, number(submittedCards ? tokens(r) / submittedCards : null), true);
  }
  target.append(node('p', 'Rates are over all runs · per-card cost counts unique submitted cards · tokens = input + output'));
}
function usageTable(target, data, dimensions) {
  const labels = { kind: 'Kind', model: 'Model', effort: 'Effort' };
  const body = table(target, [...dimensions.map((d) => labels[d]), 'Sessions', 'Busy time (h)', 'Input', 'Cached input', 'Output', 'Cache rate', 'Tool calls', 'Token share']);
  const total = data.reduce((sum, r) => sum + tokens(r), 0);
  for (const r of data) {
    const row = body.insertRow();
    for (const d of dimensions) cell(row, r[d]);
    for (const key of ['sessions', 'busy_hours', 'input', 'cached_input', 'output', 'cache_ratio', 'tool_calls']) {
      cell(row, key === 'cache_ratio' ? percent(r[key]) : number(r[key], key === 'busy_hours' ? 1 : 0), true);
    }
    const share = total ? tokens(r) / total : 0, td = cell(row, percent(share), true), bar = node('progress');
    bar.max = 1; bar.value = share; bar.setAttribute('aria-label', 'Input + output token share'); td.append(bar);
  }
  if (!data.length) target.append(node('p', 'No usage'));
}
function renderUsage(target, data) {
  target.replaceChildren(node('p', `Source: ${data.source ?? '—'} · since: ${data.since ?? '—'} · record coverage: ${percent(data.efficiency?.coverage_share)}`));
  target.append(node('h3', 'By kind, model, effort'));
  usageTable(target, rows(data.by_kind_model), ['kind', 'model', 'effort']);
  target.append(node('h3', 'Summary by model')); usageTable(target, rows(data.by_model), ['model']);
  target.append(node('h3', 'Top 30 cards by tokens'));
  const cards = table(target, ['Card', 'Title', 'Kind', 'Tokens', 'Busy time (h)']);
  for (const r of [...rows(data.top_cards)].sort((a, b) => tokens(b) - tokens(a)).slice(0, 30)) {
    const row = cards.insertRow();
    cell(row, cardLink(r.card)); cell(row, r.title); cell(row, r.kind); cell(row, number(tokens(r)), true); cell(row, number(r.busy_hours, 1), true);
  }
  target.append(node('h3', 'Wasted runs'));
  const wasted = table(target, ['Cause', 'Count', 'Token cap', 'Example']);
  for (const r of rows(data.wasted_runs)) {
    const row = wasted.insertRow(); cell(row, r.cause); cell(row, number(r.count), true);
    cell(row, number(r.tokens_upper_bound ?? r.token_upper_bound), true);
    cell(row, rows(r.examples).map((e) => typeof e === 'string' ? e : [e.card, e.title].filter(Boolean).join(' ')).join(', '));
  }
  if (!wasted.rows.length) target.append(node('p', 'No wasted runs'));
}

function renderAgents(target, data, selectedKind, onChange) {
  target.replaceChildren(node('p', `Since: ${data.since ?? '—'} · sessions: ${number(data.sessions)} · initial/max context and I/O unit: tokens`));
  const groups = table(target, ['Kind', 'Model', 'Effort', 'Sessions', 'Run min median / p90', 'Turns median', 'Initial context', 'Max context median / p90', 'Input / session', 'Output / session', 'Cache rate', 'Tool calls median', 'Total input'], 'ops-agents-table');
  for (const r of [...rows(data.groups)].sort((a, b) => (Number(b.input_total) || 0) - (Number(a.input_total) || 0))) {
    const row = groups.insertRow();
    for (const key of ['kind', 'model', 'effort']) cell(row, r[key]);
    for (const value of [number(r.sessions), `${number(r.wall_min_median, 1)} / ${number(r.wall_min_p90, 1)}`, number(r.turns_median, 1), number(r.prefill_median), `${number(r.peak_median)} / ${number(r.peak_p90)}`, number(r.input_median), number(r.output_median), percent(r.cache_ratio), number(r.tool_calls_median, 1), number(r.input_total)]) cell(row, value, true);
  }
  if (!groups.rows.length) target.append(node('p', 'No session totals'));
  const kinds = [...new Set([
    ...rows(data.groups).map((r) => String(r.kind ?? '—')),
    ...['inflow_by_kind', 'prefill', 'skills', 'tools'].flatMap((key) => Object.keys(data[key] ?? {})),
  ])].sort();
  const chips = node('div'); chips.className = 'ops-run-filters ops-agent-filters'; chips.setAttribute('role', 'group'); chips.setAttribute('aria-label', 'Agent kind');
  for (const kind of [null, ...kinds]) {
    const button = node('button', kind ?? 'All'); button.type = 'button'; button.className = 'ops-filter-chip';
    button.setAttribute('aria-pressed', String(selectedKind === kind)); button.addEventListener('click', () => onChange(kind)); chips.append(button);
  }
  target.append(chips);
  for (const kind of kinds.filter((kind) => selectedKind == null || kind === selectedKind)) {
    const section = node('div'); section.className = 'ops-agent-kind'; section.dataset.kind = kind;
    section.append(node('h3', kind), node('h4', 'Context inflow'));
    const inflows = rows(data.inflow_by_kind?.[kind]);
    const total = inflows.reduce((sum, r) => sum + Math.max(0, Number(r.tokens) || 0), 0);
    const bar = node('div'); bar.className = 'ops-agent-inflow-bar'; bar.setAttribute('role', 'img');
    bar.setAttribute('aria-label', `${kind} context inflow: ${inflows.map((r) => `${r.what} ${number(r.tokens)} tokens`).join(', ') || 'no data'}`);
    const legend = node('ul'); legend.className = 'ops-agent-legend';
    inflows.forEach((r, i) => {
      const share = total ? Math.max(0, Number(r.tokens) || 0) / total : 0;
      const segment = node('span', r.what); segment.className = 'ops-agent-inflow-segment'; segment.dataset.index = String(i % 8);
      segment.style.width = `${share * 100}%`; segment.title = `${r.what}: ${number(r.tokens)} tokens · ${percent(share)}`; bar.append(segment);
      const item = node('li', segment.title); item.dataset.index = String(i % 8); legend.append(item);
    });
    section.append(bar, legend);
    if (!inflows.length) section.append(node('p', 'No context inflow data'));
    const prefill = data.prefill?.[kind];
    section.append(node('h4', `Initial context files · sessions ${number(prefill?.sessions)}`));
    const files = node('ul');
    for (const file of rows(prefill?.files)) files.append(node('li', `${file.name ?? '—'} · ${percent(file.share)}`));
    section.append(files.children.length ? files : node('p', 'No initial context file data'));
    for (const [key, label, field, count, unit] of [['skills', 'Skills', 'skill', 'sessions', 'sessions'], ['tools', 'Tools', 'tool', 'calls', 'calls']]) {
      section.append(node('h4', label));
      const list = node('div'); list.className = 'ops-agent-chips';
      for (const entry of rows(data[key]?.[kind])) list.append(node('span', `${entry[field] ?? '—'} · ${number(entry[count])}${unit}`));
      section.append(list.children.length ? list : node('p', `No ${label.toLowerCase()} data`));
    }
    target.append(section);
  }
  target.append(node('h3', 'Top context inflow records'));
  const inflows = table(target, ['Tokens', 'Kind', 'Card', 'Inflow type', 'Input'], 'ops-agent-top-inflows');
  for (const r of [...rows(data.top_inflows)].sort((a, b) => (Number(b.tokens) || 0) - (Number(a.tokens) || 0))) {
    const row = inflows.insertRow(); cell(row, number(r.tokens), true); cell(row, r.kind); cell(row, r.card ? cardLink(r.card) : '—');
    cell(row, r.context_reset ? 'Context right after compaction/resume' : r.tool ?? '—'); ellipsis(cell(row, r.input), 'ops-card-title');
  }
  if (!inflows.rows.length) target.append(node('p', 'No context inflow records'));
  target.append(node('h3', 'Top cards by agent tokens'));
  const cards = table(target, ['Card', 'Title', 'Sessions', 'Run (min)', 'Input', 'Output', 'Total tokens'], 'ops-agent-top-cards');
  for (const r of [...rows(data.top_cards)].sort((a, b) => tokens(b) - tokens(a))) {
    const row = cards.insertRow(); cell(row, cardLink(r.card)); cell(row, r.title);
    for (const key of ['sessions', 'minutes', 'input', 'output']) cell(row, number(r[key], key === 'minutes' ? 1 : 0), true);
    cell(row, number(tokens(r)), true);
  }
  if (!cards.rows.length) target.append(node('p', 'No card totals'));
}

function commonBottleneck(data, key) {
  const tied = frequentBottlenecks(rows(data?.epics), key, data?.resources);
  if (!tied.length) return { value: '—', detail: 'No open-epic bottleneck info' };
  return { value: tied.map(([id]) => id).join(', '), detail: tied.map(([id, count]) => {
    const resource = data?.resources?.[id];
    return `${id}: ${number(count)} epics · queue ${number(resource?.queue_hours, 1)}h · utilization ${percent(resource?.utilization)}`;
  }).join(' / ') };
}

export function initOps(api, root = document.querySelector('#ops'), getBoard = () => null) {
  const header = node('header'), stamps = node('p'), metrics = node('div');
  header.append(node('h1', 'Operations'), stamps, metrics); metrics.className = 'ops-metrics'; stamps.className = 'ops-feed-stamps'; root.append(header);
  let filters = {}, limit = 50, agentKind = null;
  const feeds = [
    { name: 'Runs', path: '/api/worker-runs', interval: 60000, title: 'By worker', render: (target, data) => renderRuns(target, data, boardCards(getBoard()), filters, limit, (key, value) => {
      if (key === 'limit') limit = value;
      else { filters = { ...filters, [key]: value }; limit = 50; }
      feeds[0].render(target, data);
    }) },
    { name: 'Workers', path: '/api/workers', interval: 15000, title: 'Workers running now', render: (target, data) => renderWorkers(target, data, boardCards(getBoard())) },
    { name: 'Epics', path: '/api/epics-flow', interval: 60000, title: 'Resources and epics' },
    { name: 'Usage', path: '/api/usage', interval: 60000, title: 'Tokens and usage', render: renderUsage },
    { name: 'Agents', path: '/api/agents', interval: 60000, title: 'Agent anatomy', render: (target, data) => renderAgents(target, data, agentKind, (kind) => {
      agentKind = kind; feeds[4].render(target, data);
    }) },
  ];
  for (const feed of feeds) {
    const section = node('section'); section.append(node('h2', feed.title));
    feed.content = node('div'); section.append(feed.content); root.append(section);
    feed.content.append(node('p', 'Loading…'));
    if (!feed.render) {
      const render = createEpicsRenderer(feed.content);
      feed.render = (_, data) => render(data);
    }
  }
  let onRoute = false, active = false;
  function renderHeader() {
    stamps.textContent = feeds.map((f) => `${f.name}: ${f.note ?? f.data?.as_of ?? f.updated ?? '—'}`).join(' · ');
    const runData = feeds[0].data, total = runData?.totals, usage = feeds[3].data, efficiency = usage?.efficiency;
    const submittedCards = new Set(rows(runData?.runs).filter((r) => r.result === 'submitted' && r.card).map((r) => r.card)).size;
    const sums = runData ? rows(runData.runs).reduce((acc, r) => {
      for (const key of ['input', 'cached_input', 'output']) acc[key] += Number(r[key]) || 0;
      return acc;
    }, { input: 0, cached_input: 0, output: 0 }) : null;
    const noCommit = total?.runs ? total.no_commit / total.runs : null;
    const current = commonBottleneck(feeds[2].data, 'current_bottleneck'), next = commonBottleneck(feeds[2].data, 'next_bottleneck');
    metrics.replaceChildren();
    for (const [label, value, detail, alert] of [
      ['Worker busy (h) · 48h', number(total?.busy_hours, 1)], ['Runs', number(total?.runs)],
      ['Submit rate', percent(total?.runs ? total.submitted / total.runs : null)], ['No-commit rate', percent(noCommit), 'Runs ended without a commit / all runs', noCommit > 0.3],
      ['Busy (h) per submitted card', number(submittedCards ? total?.busy_hours / submittedCards : null, 2), 'Unique submitted cards'],
      ['Tokens · input / cached / output', sums ? `${number(sums.input)} / ${number(sums.cached_input)} / ${number(sums.output)}` : '—', `Cache rate ${percent(sums?.input ? sums.cached_input / sums.input : null)}`],
      ['Wait share', percent(efficiency?.waiting_share)], ['Current bottleneck', current.value, current.detail], ['Next bottleneck', next.value, next.detail],
    ]) {
      const item = node('div'); if (alert) item.className = 'ops-alert'; item.title = detail ?? label;
      item.append(node('small', label), node('b', value));
      if (label.startsWith('Tokens')) item.append(node('small', detail)); metrics.append(item);
    }
  }
  async function refresh(feed) {
    if (!active || feed.busy) return;
    feed.busy = true;
    try {
      const data = await api(feed.path);
      if (active) { feed.data = data; feed.note = null; feed.updated = kst(Date.now()); feed.render(feed.content, data); }
    } catch (err) {
      if (active) {
        feed.data = null; feed.note = err.status === 404 ? 'Feed not configured' : `Feed error: ${err.message}`;
        feed.content.replaceChildren(node('p', feed.note));
      }
    } finally {
      feed.busy = false;
      if (active) { renderHeader(); feed.timer = setTimeout(() => refresh(feed), feed.interval); }
    }
  }
  function sync() {
    const visible = onRoute && !document.hidden;
    if (active === visible) return;
    active = visible;
    for (const feed of feeds) { clearTimeout(feed.timer); if (active) refresh(feed); }
  }
  document.addEventListener('visibilitychange', sync);
  renderHeader();
  return {
    setVisible(visible) { onRoute = visible; root.hidden = !visible; sync(); },
    refreshBoard() { if (active) for (const feed of feeds.slice(0, 2)) if (feed.data) feed.render(feed.content, feed.data); },
  };
}
