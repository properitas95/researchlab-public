import { execFile } from 'node:child_process';
import { ApiError } from './service.js';

const TIMEOUT_MS = 15_000;
const CACHE_MS = 10_000;

// A small shell-like splitter for executable paths and quoted arguments. No shell is involved.
export function splitArgv(command) {
  const argv = [];
  let word = '';
  let started = false;
  let quote = null;
  let escaped = false;
  for (const char of String(command)) {
    if (escaped) {
      word += char;
      started = true;
      escaped = false;
    } else if (char === '\\' && quote !== "'") {
      escaped = true;
    } else if (quote) {
      if (char === quote) quote = null;
      else word += char;
    } else if (char === "'" || char === '"') {
      quote = char;
      started = true;
    } else if (/\s/.test(char)) {
      if (started) {
        argv.push(word);
        word = '';
        started = false;
      }
    } else {
      word += char;
      started = true;
    }
  }
  if (escaped || quote) throw new Error('invalid command quoting');
  if (started) argv.push(word);
  if (!argv[0]) throw new Error('empty command');
  return argv;
}

function runCommand(command, { timeoutMs, label }) {
  let argv;
  try {
    argv = splitArgv(command);
  } catch (err) {
    return Promise.reject(new ApiError(502, `${label} command ${err.message}`));
  }
  return new Promise((resolve, reject) => execFile(argv[0], argv.slice(1), { shell: false, timeout: timeoutMs, killSignal: 'SIGKILL', maxBuffer: 32 * 1024 * 1024 }, (err, stdout) => {
    if (err) {
      if (err.killed && err.code === null) return reject(new ApiError(502, `${label} command timed out`));
      if (typeof err.code === 'number') return reject(new ApiError(502, `${label} command exited with code ${err.code}`));
      return reject(new ApiError(502, `${label} command failed`));
    }
    try {
      resolve(JSON.parse(stdout));
    } catch {
      reject(new ApiError(502, `${label} command returned invalid JSON`));
    }
  }));
}

export function createWorkersFeed({ envVar = 'AI_KANBAN_WORKERS_CMD', timeoutMs = TIMEOUT_MS, cacheMs = CACHE_MS, label = 'Workers' } = {}) {
  let cached;
  return async function workersFeed() {
    const command = process.env[envVar];
    if (!command) throw new ApiError(404, `${label.toLowerCase()} feed not configured`);
    const now = Date.now();
    if (cached && cached.command === command && (cached.expiresAt === 0 || cached.expiresAt > now)) return cached.promise;
    const promise = runCommand(command, { timeoutMs, label });
    cached = { command, expiresAt: 0, promise };
    promise.then(
      () => { if (cached.promise === promise) cached.expiresAt = Date.now() + cacheMs; },
      () => { if (cached.promise === promise) cached.expiresAt = Date.now() + cacheMs; },
    );
    return promise;
  };
}

export function createWorkerRunsFeed() {
  return createWorkersFeed({
    envVar: 'AI_KANBAN_RUNS_CMD',
    timeoutMs: 20_000,
    cacheMs: 60_000,
    label: 'Worker runs',
  });
}

export function createAgentsFeed() {
  return createWorkersFeed({
    envVar: 'AI_KANBAN_AGENTS_CMD',
    timeoutMs: 20_000,
    cacheMs: 60_000,
    label: 'Agents',
  });
}
