// Called by pytest with an isolated Windows/local test server and runtime-only input.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { ApiClient, ApiError } from '../web/panel/api.mjs';
import { PreviewSettings } from '../web/simulator/preview.mjs';
import { checkHealth } from '../web/simulator/connection.mjs';

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
assert.deepEqual(await client.request('/api/v1/agents/codex/identity', { headers: agentAuth }),
  { agent_id: agentCredential.agent_id, kind: 'codex_windows' });
await assert.rejects(client.request('/api/v1/agents/codex/identity', { headers: deviceAuth }), error => error.status === 401);
await assert.rejects(client.request(statePath, { headers: agentAuth }), error => error.status === 401);
await client.request(`/api/v1/agents/codex/${agentCredential.agent_id}/credentials/revoke`, {
  method: 'POST', headers: { 'X-CSRF-Token': session.csrf_token },
});
await assert.rejects(client.request('/api/v1/agents/codex/identity', { headers: agentAuth }), error => error.status === 401);
for (const path of ['/panel/', '/simulator/?device=integration-tv', '/simulator/preview.mjs']) {
  assert.equal((await fetcher(path)).status, 200);
}
await client.logout(session.csrf_token);
try { await client.settings('integration-tv'); assert.fail('Session should be revoked'); }
catch (error) { assert.equal(error.status, 401); preview.fail(error.status); }
assert.equal(preview.state, 'signed-out');
assert.equal(preview.settings.theme, 'BlackArch Red');
console.log('HTTP integration passed: authentication, SQLite, simulator preview, device enrollment/revocation and logout.');
