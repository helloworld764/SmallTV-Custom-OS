import test from 'node:test';
import assert from 'node:assert/strict';
import { ApiClient, ApiError, changesFrom, hasDraftChanges } from '../web/panel/api.mjs';

const csrf = 'a'.repeat(64); // Deliberately synthetic, not a credential.
const session = { user: { id: 'user-test', display_name: 'Test', role: 'user' }, csrf_token: csrf };
const settings = { device_id: 'device-test', brightness: 70, theme: 'BlackArch Red', start_screen: 'clock' };
const reply = (value, status = 200) => ({ ok: status < 400, status, json: async () => value });

test('login uses JSON, same-origin cookies and the required client header', async () => {
  const client = new ApiClient({ fetcher: async (path, options) => {
    assert.equal(path, '/api/v1/auth/login');
    assert.equal(options.method, 'POST');
    assert.equal(options.credentials, 'same-origin');
    assert.equal(options.cache, 'no-store');
    assert.equal(options.headers['X-SmallTV-Client'], 'web');
    assert.equal(options.headers['Content-Type'], 'application/json');
    assert.deepEqual(JSON.parse(options.body), { username: 'test-user', password: 'test-input' });
    return reply(session);
  } });
  assert.deepEqual(await client.login('test-user', 'test-input'), session);
});

test('session restoration and pagination use their dedicated API routes', async () => {
  const paths = [];
  const client = new ApiClient({ fetcher: async path => {
    paths.push(path);
    return reply(path.endsWith('/session') ? session : []);
  } });
  assert.deepEqual(await client.session(), session);
  assert.deepEqual(await client.devices(50), []);
  assert.deepEqual(paths, ['/api/v1/auth/session', '/api/v1/devices?limit=50&offset=50']);
});

test('saving settings encodes resource identifiers and sends session CSRF', async () => {
  const client = new ApiClient({ fetcher: async (path, options) => {
    assert.equal(path, '/api/v1/devices/device%2F%3F%23/settings');
    assert.equal(options.method, 'PATCH');
    assert.equal(options.headers['X-CSRF-Token'], csrf);
    assert.deepEqual(JSON.parse(options.body), { brightness: 10 });
    return reply({ ...settings, device_id: 'device/?#', brightness: 10 });
  } });
  assert.equal((await client.save('device/?#', { brightness: 10 }, csrf)).brightness, 10);
});

test('logout handles empty 204 response and sends CSRF without a body', async () => {
  const client = new ApiClient({ fetcher: async (path, options) => {
    assert.equal(path, '/api/v1/auth/logout');
    assert.equal(options.method, 'POST');
    assert.equal(options.headers['X-CSRF-Token'], csrf);
    assert.equal(options.body, undefined);
    return { ok: true, status: 204, json() { throw new Error('204 has no JSON'); } };
  } });
  assert.equal(await client.logout(csrf), null);
});

test('HTTP errors preserve status, never display raw server detail or retry mutations', async () => {
  for (const status of [401, 403, 404, 422, 429, 500]) {
    let requests = 0;
    const client = new ApiClient({ fetcher: async () => {
      requests++;
      return reply({ detail: 'untrusted server detail' }, status);
    } });
    await assert.rejects(client.save('device-test', { brightness: 5 }, csrf), error => {
      assert.ok(error instanceof ApiError);
      assert.equal(error.status, status);
      assert.ok(!error.message.includes('untrusted'));
      return true;
    });
    assert.equal(requests, 1);
  }
});

test('network errors and abort timeout are distinguishable from invalid credentials', async () => {
  const connection = [];
  const client = new ApiClient({ onConnection: value => connection.push(value), fetcher: async () => { throw new TypeError('offline'); } });
  await assert.rejects(client.session(), error => error.kind === 'network' && error.status === 0);
  assert.deepEqual(connection, [false]);
  const slow = new ApiClient({ timeout: 5, fetcher: (path, options) => new Promise((resolve, reject) => {
    options.signal.addEventListener('abort', () => reject(new Error('aborted')));
  }) });
  await assert.rejects(slow.session(), error => error.kind === 'timeout' && error.status === 0);
});

test('malformed session, device list, settings and JSON cannot produce success', async () => {
  for (const value of [null, {}, { ...session, csrf_token: 'bad' }, { ...session, user: { ...session.user, role: 'root' } }]) {
    const client = new ApiClient({ fetcher: async () => reply(value) });
    await assert.rejects(client.session(), error => error.kind === 'protocol');
  }
  const list = new ApiClient({ fetcher: async () => reply({ devices: [] }) });
  await assert.rejects(list.devices(), error => error.kind === 'protocol');
  const invalid = new ApiClient({ fetcher: async () => reply({ ...settings, brightness: 101 }) });
  await assert.rejects(invalid.settings('device-test'), error => error.kind === 'protocol');
  const json = new ApiClient({ fetcher: async () => ({ ok: true, status: 200, json() { throw new Error('HTML response'); } }) });
  await assert.rejects(json.session(), error => error.kind === 'protocol');
});

test('only changed settings are sent, including a valid zero brightness', () => {
  assert.deepEqual(changesFrom(settings, settings), {});
  assert.deepEqual(changesFrom(settings, { ...settings, brightness: 0, owner_id: 'ignored' }), { brightness: 0 });
  assert.deepEqual(changesFrom(settings, { ...settings, theme: 'Light', start_screen: 'weather' }), { theme: 'Light', start_screen: 'weather' });
});

test('a response for another device cannot be accepted as a read or save success', async () => {
  const client = new ApiClient({ fetcher: async () => reply(settings) });
  await assert.rejects(client.settings('another-device'), error => error.kind === 'protocol');
  await assert.rejects(client.save('another-device', { brightness: 10 }, csrf), error => error.kind === 'protocol');
});

test('draft protection distinguishes unchanged input, zero brightness and invalid empty input', () => {
  assert.equal(hasDraftChanges(settings, { ...settings, brightness: '70' }), false);
  assert.equal(hasDraftChanges(settings, { ...settings, brightness: '0' }), true);
  assert.equal(hasDraftChanges({ ...settings, brightness: 0 }, { ...settings, brightness: '' }), true);
  assert.equal(hasDraftChanges(settings, { ...settings, brightness: 'invalid' }), true);
  assert.equal(hasDraftChanges(settings, { ...settings, theme: 'Purple' }), true);
  assert.equal(hasDraftChanges(null, settings), false);
});
