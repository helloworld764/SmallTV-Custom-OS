import test from 'node:test';
import assert from 'node:assert/strict';
import { Connection, checkHealth } from '../web/simulator/connection.mjs';
import { PreviewSettings } from '../web/simulator/preview.mjs';

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

test('standalone preview needs no account and settings start as unconfirmed', () => {
  const standalone = new PreviewSettings();
  assert.equal(standalone.state, 'standalone');
  assert.equal(standalone.screenTitle, 'Часы');
  const device = new PreviewSettings('device-test');
  assert.equal(device.state, 'loading');
  assert.equal(device.updatedAt, null);
});

test('preview updates theme, brightness and start screen from authorized settings', () => {
  const preview = new PreviewSettings('device-test');
  preview.apply({ device_id: 'device-test', brightness: 0, theme: 'Light', start_screen: 'weather' }, 1000);
  assert.equal(preview.state, 'fresh');
  assert.equal(preview.theme, 'light');
  assert.equal(preview.settings.brightness, 0);
  assert.equal(preview.screenTitle, 'Погода');
  assert.equal(preview.updatedAt, 1000);
});

test('network outage keeps cached settings stale and recovery replaces them', () => {
  const preview = new PreviewSettings('device-test');
  const initial = { device_id: 'device-test', brightness: 25, theme: 'Purple', start_screen: 'clock' };
  preview.apply(initial, 1000);
  preview.fail(0);
  assert.equal(preview.state, 'stale');
  assert.deepEqual(preview.settings, initial);
  preview.apply({ ...initial, brightness: 50 }, 2000);
  assert.equal(preview.state, 'fresh');
  assert.equal(preview.settings.brightness, 50);
});

test('expired session or denied ownership clears private cached settings', () => {
  for (const status of [401, 403, 404]) {
    const preview = new PreviewSettings('device-test');
    preview.apply({ device_id: 'device-test', brightness: 5, theme: 'Purple', start_screen: 'null' }, 1000);
    preview.fail(status);
    assert.equal(preview.state, status === 401 ? 'signed-out' : 'unavailable');
    assert.equal(preview.updatedAt, null);
    assert.equal(preview.settings.theme, 'BlackArch Red');
  }
});

test('wrong-device data cannot enter preview cache', () => {
  const preview = new PreviewSettings('device-test');
  assert.throws(() => preview.apply({ device_id: 'other', theme: 'Purple' }, 1000));
  assert.equal(preview.state, 'loading');
  assert.equal(preview.updatedAt, null);
});
