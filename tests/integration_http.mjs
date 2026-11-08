// Called by pytest with an isolated Windows/local test server and runtime-only input.
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { ApiClient, ApiError } from '../web/panel/api.mjs';
import { PreviewSettings } from '../web/simulator/preview.mjs';
import { checkHealth } from '../web/simulator/connection.mjs';
import { Notifications, readNotifications } from '../web/simulator/notifications.mjs';

const { base, password } = JSON.parse(readFileSync(0, 'utf8'));
const cookies = new Map();
async function fetcher(path, options = {}) {
  const url = new URL(path, base);
  assert.equal(url.origin, base);
  const response = await fetch(url, { ...options, headers: {
    ...options.headers, Origin: base,
    Cookie: [...cookies].map(([key, value]) => `${key}=${value}`).join('; '),
  } });
  for (const header of response.headers.getSetCookie()) {
    const [name, value] = header.split(';')[0].split('=');
    if (value && value !== '""') cookies.set(name, value);
    else cookies.delete(name);
  }
  return response;
}
const client = new ApiClient({ fetcher });
await assert.rejects(client.devices(), error => error instanceof ApiError && error.status === 401);
const session = await client.login('integration-user', password);
assert.equal((await client.session()).user.id, session.user.id);
const devices = await client.devices();
assert.deepEqual(devices.map(device => device.id), ['integration-tv']);
const preview = new PreviewSettings('integration-tv');
preview.apply(await client.settings('integration-tv'), Date.now());
assert.equal(preview.theme, 'red');
await assert.rejects(client.save('integration-tv', { brightness: 15 }, 'incorrect'), error => error.status === 403);
await client.save('integration-tv', { brightness: 15, theme: 'Dark Blue', start_screen: 'weather' }, session.csrf_token);
preview.apply(await client.settings('integration-tv'), Date.now());
assert.equal(preview.theme, 'blue');
assert.equal(preview.settings.brightness, 15);
assert.equal(preview.screenTitle, 'Погода');
preview.fail(0);
assert.equal(preview.state, 'stale');
preview.apply(await client.settings('integration-tv'), Date.now());
assert.equal(preview.state, 'fresh');
await assert.rejects(client.settings('foreign-tv'), error => error.status === 404);
await checkHealth(fetcher, new AbortController().signal);
const pairing = await client.request('/api/v1/devices/enrollment-codes', {
  method: 'POST', body: { name: 'HTTP software client' }, headers: { 'X-CSRF-Token': session.csrf_token },
});
const deviceCredential = await client.request('/api/v1/devices/enroll', { method: 'POST', body: { code: pairing.code } });
const statePath = `/api/v1/devices/${deviceCredential.device_id}/state`;
const deviceAuth = { Authorization: `Bearer ${deviceCredential.token}` };
const deviceState = await client.request(statePath, { headers: deviceAuth });
assert.equal(deviceState.device_id, deviceCredential.device_id);
assert.equal(deviceState.settings.start_screen, 'clock');
await client.request(`/api/v1/devices/${deviceCredential.device_id}/credentials/revoke`, {
  method: 'POST', headers: { 'X-CSRF-Token': session.csrf_token },
});
await assert.rejects(client.request(statePath, { headers: deviceAuth }), error => error.status === 401);
const agentCredential = await client.request('/api/v1/agents/codex', {
  method: 'POST', body: { name: 'HTTP agent identity test' }, headers: { 'X-CSRF-Token': session.csrf_token },
});
const agentAuth = { Authorization: `Bearer ${agentCredential.token}` };
const presencePath = `/api/v1/agents/codex/${agentCredential.agent_id}/presence`;
assert.equal((await client.request(presencePath)).state, 'UNKNOWN');
const softwareStart = { event_id: randomUUID(), run_id: randomUUID(), sequence: 1,
  kind: 'START', occurred_at: Math.floor(Date.now() / 1000) };
const report = body => client.request('/api/v1/agents/codex/events', {
  method: 'POST', body, headers: agentAuth,
});
assert.equal((await report(softwareStart)).duplicate, false);
assert.equal((await report(softwareStart)).duplicate, true);
assert.equal((await client.request(presencePath)).state, 'UNKNOWN');
const runPath = `/api/v1/agents/codex/${agentCredential.agent_id}/runs/${softwareStart.run_id}`;
assert.equal((await client.request(runPath)).last_reported_state, 'WORKING');
await assert.rejects(report({ ...softwareStart, kind: 'DONE' }), error => error.status === 409);
const softwareDone = { ...softwareStart, event_id: randomUUID(), sequence: 2, kind: 'DONE' };
await report(softwareDone);
const inbox = new Notifications('integration-tv');
inbox.apply(await readNotifications(client, 'integration-tv', 0), 0);
assert.match(inbox.tick(0), /ответ завершён/);
assert.deepEqual((await readNotifications(client, 'integration-tv', inbox.cursor)).events, []);
assert.equal(inbox.tick(4000), null);
assert.equal(preview.settings.start_screen, 'weather');
await assert.rejects(readNotifications(client, 'foreign-tv', 0), error => error.status === 404);
assert.equal((await client.request(runPath)).last_reported_state, 'DONE');
assert.equal((await client.request(`${runPath}/events?after_sequence=1`)).length, 1);
await client.request('/api/v1/agents/codex/heartbeat', { method: 'POST', body: {}, headers: agentAuth });
const presence = await client.request(presencePath);
assert.equal(presence.state, 'ONLINE');
assert.ok(Number.isInteger(presence.last_seen_at));
await assert.rejects(client.request('/api/v1/agents/codex/heartbeat', {
  method: 'POST', body: { state: 'DONE' }, headers: agentAuth,
}), error => error.status === 422);
assert.deepEqual(await client.request('/api/v1/agents/codex/identity', { headers: agentAuth }),
  { agent_id: agentCredential.agent_id, kind: 'codex_windows' });
await assert.rejects(client.request('/api/v1/agents/codex/identity', { headers: deviceAuth }), error => error.status === 401);
await assert.rejects(client.request(statePath, { headers: agentAuth }), error => error.status === 401);
await client.request(`/api/v1/agents/codex/${agentCredential.agent_id}/credentials/revoke`, {
  method: 'POST', headers: { 'X-CSRF-Token': session.csrf_token },
});
await assert.rejects(client.request('/api/v1/agents/codex/identity', { headers: agentAuth }), error => error.status === 401);
assert.equal((await client.request(presencePath)).state, 'OFFLINE');
await assert.rejects(report(softwareDone), error => error.status === 401);
assert.equal((await client.request(runPath)).last_reported_state, 'DONE');
await assert.rejects(client.request('/api/v1/agents/codex/heartbeat', {
  method: 'POST', body: {}, headers: agentAuth,
}), error => error.status === 401);
for (const path of ['/panel/', '/simulator/?device=integration-tv', '/simulator/preview.mjs', '/simulator/notifications.mjs', '/shared/site-theme.mjs', '/shared/site-theme.css']) {
  assert.equal((await fetcher(path)).status, 200);
}
await client.logout(session.csrf_token);
await assert.rejects(readNotifications(client, 'integration-tv', inbox.cursor), error => error.status === 401);
inbox.reset();
try { await client.settings('integration-tv'); assert.fail('Session should be revoked'); }
catch (error) { assert.equal(error.status, 401); preview.fail(error.status); }
assert.equal(preview.state, 'signed-out');
assert.equal(preview.settings.theme, 'BlackArch Red');
console.log('HTTP integration passed: authentication, SQLite, simulator preview, device enrollment, agent heartbeat/events/revocation and logout.');
