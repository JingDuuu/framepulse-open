'use strict';

const path = require('node:path');
const net = require('node:net');
const crypto = require('node:crypto');

function parseOptions(argv) {
  const options = { data: null, smokeOutput: null };
  for (let index = 0; index < argv.length; index++) {
    const key = argv[index];
    if (!['--data', '--smoke-output'].includes(key)) continue;
    const value = argv[++index];
    if (!value || !path.isAbsolute(value)) throw new Error(`${key} 必须指定绝对路径。`);
    options[key === '--data' ? 'data' : 'smokeOutput'] = path.normalize(value);
  }
  return options;
}

function isAppUrl(value, origin) {
  try {
    const url = new URL(value);
    return url.origin === origin && !url.username && !url.password;
  } catch { return false; }
}

function sameToken(actual, expected) {
  if (typeof actual !== 'string' || typeof expected !== 'string') return false;
  const first = Buffer.from(actual);
  const second = Buffer.from(expected);
  return first.length === second.length && crypto.timingSafeEqual(first, second);
}

function freeLoopbackPort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.once('error', reject);
    server.listen(0, '127.0.0.1', () => {
      const port = server.address().port;
      server.close(error => error ? reject(error) : resolve(port));
    });
  });
}

const activeStates = new Set(['running', 'cancelling', 'canceling', 'starting']);
const knownStates = new Set([...activeStates, 'pending', 'queued', 'completed', 'failed', 'cancelled']);
function activeJobs(response) {
  if (!response || !Array.isArray(response.jobs)) throw new Error('后台返回了无法识别的任务列表。');
  if (response.jobs.some(job => !job || typeof job.id !== 'string' || !knownStates.has(job.state))) {
    throw new Error('后台返回了无法识别的任务状态，尚不能确认已保存断点。');
  }
  return response.jobs.filter(job => activeStates.has(job.state));
}

// Pausing preserves queued work. Only the executing job is cancelled so the engine
// can finish its checkpoint before the desktop process ends its owned backend.
async function checkpointBeforeExit(request, { isAlive, sleep, onWaiting, maxWait = 120000 }) {
  if (!isAlive()) return true;
  await request('/api/queue/pause', 'POST');
  const deadline = Date.now() + maxWait;
  const cancelled = new Set();
  while (isAlive()) {
    const pending = activeJobs(await request('/api/jobs'));
    if (!pending.length) return true;
    for (const job of pending) {
      if (cancelled.has(job.id)) continue;
      await request(`/api/jobs/${encodeURIComponent(job.id)}/cancel`, 'POST');
      cancelled.add(job.id);
    }
    onWaiting?.(pending.length);
    if (Date.now() >= deadline) return false;
    await sleep(350);
  }
  return true;
}

module.exports = { parseOptions, isAppUrl, sameToken, freeLoopbackPort, activeJobs, checkpointBeforeExit };
