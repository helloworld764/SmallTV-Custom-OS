import test from 'node:test';
import assert from 'node:assert/strict';
import { ApiClient, ApiError, changesFrom, hasDraftChanges } from '../web/panel/api.mjs';
import { EnrollmentFlow } from '../web/panel/enrollment.mjs';

const csrf = 'a'.repeat(64); // Deliberately synthetic, not a credential.
const session = { user: { id: 'user-test', display_name: 'Test', role: 'user' }, csrf_token: csrf };
const settings = { device_id: 'device-test', brightness: 70, theme: 'BlackArch Red', start_screen: 'clock' };
const reply = (value, status = 200) => ({ ok: status < 400, status, json: async () => value });

test('wizard API uses session CSRF, JSON bodies and encodes only resource ids in URLs', async () => {
  const calls = [];
  const code = { id: 'code-test', code: 'smalltv_enroll_' + 'x'.repeat(32), expires_at: 1000, expires_in: 300 };
  const client = new ApiClient({ fetcher: async (path, options) => {
    calls.push([path, options.method, options.headers['X-CSRF-Token'], options.body && JSON.parse(options.body)]);
    return reply(path.endsWith('/previews') ? { id: 'preview-test', name: 'Preview', is_preview: true }
      : options.method === 'POST' ? code : { id: 'code/?', state: 'enrolled', device_id: 'test-device', expires_in: 0 },
    options.method === 'DELETE' ? 204 : 200);
  } });
  await client.createPreview('Preview', 'request-id', csrf);
  assert.equal((await client.enrollmentCode('Client', csrf)).code, code.code);
  assert.equal((await client.enrollmentStatus('code/?')).device_id, 'test-device');
  await client.cancelEnrollment('code/?', csrf);
  assert.deepEqual(calls, [
    ['/api/v1/devices/previews', 'POST', csrf, { name: 'Preview', request_id: 'request-id' }],
    ['/api/v1/devices/enrollment-codes', 'POST', csrf, { name: 'Client' }],
    ['/api/v1/devices/enrollment-codes/code%2F%3F', 'GET', undefined, undefined],
    ['/api/v1/devices/enrollment-codes/code%2F%3F', 'DELETE', csrf, undefined],
  ]);
  assert.ok(calls.every(([url]) => !url.includes(code.code)));
});

test('wizard rejects malformed success responses', async () => {
  for (const value of [null, {}, { id: 'id', name: 'wrong', is_preview: true }]) {
    const client = new ApiClient({ fetcher: async () => reply(value) });
    await assert.rejects(client.createPreview('Preview', 'request-id', csrf), error => error.kind === 'protocol');
    await assert.rejects(client.enrollmentCode('Preview', csrf), error => error.kind === 'protocol');
    await assert.rejects(client.enrollmentStatus('id'), error => error.kind === 'protocol');
  }
  const client = new ApiClient({ fetcher: async () => reply({ id: 'id', state: 'enrolled', expires_in: 0 }) });
  await assert.rejects(client.enrollmentStatus('id'), error => error.kind === 'protocol');
});

test('explicit preview retry keeps its id after a lost response', async () => {
  const ids = [];
  const flow = new EnrollmentFlow({ createPreview: async (name, id) => {
    ids.push(id);
    if (ids.length === 1) throw new ApiError(0, 'timeout');
    return { id: 'new-device', name, is_preview: true };
  } }, { uuid: () => 'fixed-request-id' });
  await assert.rejects(flow.createPreview('Preview', csrf));
  assert.equal((await flow.createPreview('Preview', csrf)).id, 'new-device');
  assert.deepEqual(ids, ['fixed-request-id', 'fixed-request-id']);
});

test('enrollment countdown subtracts network latency and clears the expired secret', async () => {
  let time = 1000;
  const flow = new EnrollmentFlow({ enrollmentCode: async () => {
    time += 3000;
    return { id: 'test', code: 'synthetic', expires_in: 300 };
  } }, { clock: () => time });
  await flow.issue('Client', csrf);
  assert.equal(flow.remaining(), 297);
  time = 301000;
  assert.equal(flow.remaining(), 0);
  assert.equal(flow.code.code, '');
  assert.equal(flow.code.id, 'test');
});

test('pending, enrolled, expired and legacy closed enrollment states are distinguished', async () => {
  for (const state of ['pending', 'enrolled', 'expired', 'closed']) {
    const flow = new EnrollmentFlow({
      enrollmentCode: async () => ({ id: 'test', code: 'synthetic', expires_in: 300 }),
      enrollmentStatus: async () => ({ id: 'test', state, expires_in: state === 'pending' ? 10 : 0 }),
    }, { clock: () => 0 });
    await flow.issue('Client', csrf);
    assert.equal((await flow.check()).state, state);
    assert.equal(flow.code.code, state === 'pending' ? 'synthetic' : '');
    assert.equal(flow.remaining(), state === 'pending' ? 10 : 0);
  }
});

test('failed cancellation hides the secret but retains id for explicit retry', async () => {
  let calls = 0;
  const flow = new EnrollmentFlow({
    enrollmentCode: async () => ({ id: 'test', code: 'synthetic', expires_in: 300 }),
    cancelEnrollment: async id => { assert.equal(id, 'test'); if (++calls === 1) throw new ApiError(0); },
  });
  await flow.issue('Client', csrf);
  await assert.rejects(flow.cancel(csrf));
  assert.equal(flow.code.code, '');
  assert.equal(flow.code.id, 'test');
  await flow.cancel(csrf);
  assert.equal(flow.code, null);
  assert.equal(calls, 2);
});

test('account reset discards delayed code, preview and status responses', async () => {
  for (const operation of ['issue', 'createPreview', 'check']) {
    let finish;
    const deferred = () => new Promise(resolve => { finish = resolve; });
    const flow = new EnrollmentFlow({ enrollmentCode: deferred, createPreview: deferred, enrollmentStatus: deferred }, { uuid: () => 'request-id' });
    if (operation === 'check') flow.code = { id: 'test', code: 'synthetic' };
    const promise = flow[operation]('Client', csrf);
    flow.reset();
    finish({ id: 'test', code: 'synthetic', state: 'pending', expires_in: 300 });
    assert.equal(await promise, null);
    assert.equal(flow.code, null);
    assert.equal(flow.preview, null);
  }
});

test('lost code issue response is not retried automatically or treated as a registration', async () => {
  let calls = 0;
  const flow = new EnrollmentFlow({ enrollmentCode: async () => { calls++; throw new ApiError(0); } });
  await assert.rejects(flow.issue('Client', csrf));
  assert.equal(calls, 1);
  assert.equal(flow.code, null);
});

test('Telegram linking uses private API calls with CSRF and never URL credentials', async () => {
  const calls = [];
  const client = new ApiClient({ fetcher: async (path, options) => {
    calls.push([path, options.method, options.headers['X-CSRF-Token']]);
    return reply({}, options.method === 'DELETE' ? 204 : 200);
  } });
  await client.telegramStatus();
  await client.telegramCode(csrf);
  await client.cancelTelegramCode(csrf);
  await client.unlinkTelegram(csrf);
  assert.deepEqual(calls, [
    ['/api/v1/telegram/status', 'GET', undefined], ['/api/v1/telegram/link-code', 'POST', csrf],
    ['/api/v1/telegram/link-code', 'DELETE', csrf], ['/api/v1/telegram/link', 'DELETE', csrf],
  ]);
});

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

test('weather city preference reads privately and writes with CSRF', async () => {
  const paths = [];
  const client = new ApiClient({ fetcher: async (path, options) => {
    paths.push([path, options.method, options.headers['X-CSRF-Token']]);
    return reply({ city: 'Kazan', timezone: 'Europe/Moscow' });
  } });
  assert.deepEqual(await client.weatherPreference(), { city: 'Kazan', timezone: 'Europe/Moscow' });
  assert.deepEqual(await client.saveWeatherPreference('Kazan', csrf), { city: 'Kazan', timezone: 'Europe/Moscow' });
  assert.deepEqual(paths, [
    ['/api/v1/preferences/weather', 'GET', undefined],
    ['/api/v1/preferences/weather', 'PATCH', csrf],
  ]);
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
