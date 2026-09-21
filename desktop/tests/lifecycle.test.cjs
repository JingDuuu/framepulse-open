'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const net = require('node:net');
const { parseOptions, isAppUrl, sameToken, freeLoopbackPort, checkpointBeforeExit } = require('../lifecycle.cjs');

test('only exact loopback origin grants native IPC authority', () => {
  const origin = 'http://127.0.0.1:51234';
  assert.equal(isAppUrl(`${origin}/?path=video`, origin), true);
  for (const value of ['https://127.0.0.1:51234', 'http://127.0.0.1:51235', 'http://127.0.0.1.evil:51234', 'file:///C:/media', 'http://user@127.0.0.1:51234', 'invalid']) {
    assert.equal(isAppUrl(value, origin), false, value);
  }
  assert.equal(sameToken('secret', 'secret'), true);
  assert.equal(sameToken('secretX', 'secret'), false);
  assert.equal(sameToken(null, 'secret'), false);
});

test('data folder must be explicit absolute path', () => {
  assert.throws(() => parseOptions(['--data', '../media']));
  assert.throws(() => parseOptions(['--data']));
  const absolute = path.resolve('test-library');
  assert.equal(parseOptions(['--data', absolute]).data, absolute);
});

test('dynamic port is available on loopback without using the browser service port', async () => {
  const port = await freeLoopbackPort();
  assert.ok(port > 1023);
  const server = net.createServer();
  await new Promise((resolve, reject) => { server.once('error', reject); server.listen(port, '127.0.0.1', resolve); });
  await new Promise(resolve => server.close(resolve));
});

test('close pauses pending queue, cancels only active job, waits for checkpoint', async () => {
  const calls = [];
  let polls = 0;
  const request = async (url, method = 'GET') => {
    calls.push([url, method]);
    if (url === '/api/jobs') return { jobs: [
      { id: 'pending-id', state: 'pending' },
      { id: 'active-id', state: ++polls < 3 ? 'running' : 'cancelled' },
    ] };
    return {};
  };
  const saved = await checkpointBeforeExit(request, { isAlive: () => true, sleep: async () => {} });
  assert.equal(saved, true);
  assert.equal(polls, 3);
  assert.deepEqual(calls[0], ['/api/queue/pause', 'POST']);
  assert.equal(calls.filter(([route]) => route === '/api/jobs/active-id/cancel').length, 1);
  assert.equal(calls.some(([route]) => route.includes('pending-id')), false);
});

test('malformed or disconnected backend cannot be treated as a safe checkpoint', async () => {
  await assert.rejects(() => checkpointBeforeExit(async () => ({}), { isAlive: () => true, sleep: async () => {} }), /任务列表/);
  await assert.rejects(() => checkpointBeforeExit(async () => ({ jobs: [{ id: 'unknown', status: 'running' }] }), { isAlive: () => true, sleep: async () => {} }), /任务状态/);
  let calls = 0;
  assert.equal(await checkpointBeforeExit(async () => { calls++; }, { isAlive: () => false, sleep: async () => {} }), true);
  assert.equal(calls, 0);
});

test('checkpoint timeout requires explicit user decision rather than forced termination', async () => {
  const request = async route => route === '/api/jobs' ? { jobs: [{ id: 'slow', state: 'running' }] } : {};
  assert.equal(await checkpointBeforeExit(request, { isAlive: () => true, sleep: async () => {}, maxWait: -1 }), false);
});
