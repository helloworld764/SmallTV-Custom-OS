import test from 'node:test';
import assert from 'node:assert/strict';
import { Connection, checkHealth } from '../web/simulator/connection.mjs';

test('startup does not claim success or emit a recovery notice', () => {
  const c = new Connection();
  assert.equal(c.state, 'unknown');
  assert.equal(c.record(true, 1000), null);
  assert.equal(c.state, 'online');
});

test('three failures confirm outage once; recovery is emitted once', () => {
  const c = new Connection();
  c.record(true, 0);
  assert.equal(c.record(false, 5000), null);
  assert.equal(c.state, 'stale');
  assert.equal(c.record(false, 15000), null);
  assert.equal(c.record(false, 35000), 'Сервер выключен');
  assert.equal(c.state, 'offline');
  assert.equal(c.record(false, 65000), null);
  assert.equal(c.record(true, 95000), 'Сервер включён');
  assert.equal(c.record(true, 100000), null);
});

test('transient failure does not spam connection notices', () => {
  const c = new Connection();
  c.record(true, 0);
  c.record(false, 5000);
  assert.equal(c.record(true, 15000), null);
  assert.equal(c.failures, 0);
  assert.equal(c.delay, 5000);
});

test('stale success becomes yellow, retries are bounded', () => {
  const c = new Connection();
  c.record(true, 0);
  c.age(14999);
  assert.equal(c.state, 'online');
  c.age(15000);
  assert.equal(c.state, 'stale');
  for (let i = 0; i < 20; i++) c.record(false, i + 16000);
  assert.equal(c.delay, 30000);
});

test('API check uses relative URL, no cache and abort signal', async () => {
  const signal = new AbortController().signal;
  await checkHealth(async (url, options) => {
    assert.equal(url, '/health');
    assert.equal(options.cache, 'no-store');
    assert.equal(options.signal, signal);
    return { ok: true, json: async () => ({ status: 'ok' }) };
  }, signal);
});

test('HTTP, invalid payload, JSON and network failures cannot become online', async () => {
  for (const fetcher of [
    async () => ({ ok: false }),
    async () => ({ ok: true, json: async () => ({ status: 'error' }) }),
    async () => ({ ok: true, json: async () => { throw new Error('JSON'); } }),
    async () => { throw new Error('network'); },
  ]) {
    await assert.rejects(checkHealth(fetcher, new AbortController().signal));
  }
});
