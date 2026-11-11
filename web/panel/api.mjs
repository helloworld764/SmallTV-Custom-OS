import { clockSettings } from '../shared/clock.mjs';

export class ApiError extends Error {
  constructor(status, kind = 'http') {
    super(`API ${kind} (${status})`);
    this.status = status;
    this.kind = kind;
  }
}

export class ApiClient {
  constructor({ fetcher = (url, options) => fetch(url, options), timeout = 10000, onConnection = () => {} } = {}) {
    this.fetcher = fetcher;
    this.timeout = timeout;
    this.onConnection = onConnection;
  }

  async request(path, { method = 'GET', body, headers = {} } = {}) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), this.timeout);
    try {
      const response = await this.fetcher(path, {
        method, credentials: 'same-origin', cache: 'no-store', signal: controller.signal,
        headers: { ...headers, ...(body === undefined ? {} : { 'Content-Type': 'application/json' }) },
        ...(body === undefined ? {} : { body: JSON.stringify(body) }),
      });
      this.onConnection(true);
      if (!response.ok) throw new ApiError(response.status);
      if (response.status === 204) return null;
      try { return await response.json(); }
      catch { throw new ApiError(502, 'protocol'); }
    } catch (error) {
      if (error instanceof ApiError) throw error;
      this.onConnection(false);
      throw new ApiError(0, controller.signal.aborted ? 'timeout' : 'network');
    } finally {
      clearTimeout(timeout);
    }
  }

  async session() { return sessionData(await this.request('/api/v1/auth/session')); }
  async login(username, password) {
    return sessionData(await this.request('/api/v1/auth/login', {
      method: 'POST', body: { username, password }, headers: { 'X-SmallTV-Client': 'web' },
    }));
  }
  logout(csrf) {
    return this.request('/api/v1/auth/logout', { method: 'POST', headers: { 'X-CSRF-Token': csrf } });
  }
  async devices(offset = 0) {
    const rows = await this.request(`/api/v1/devices?limit=50&offset=${offset}`);
    if (!Array.isArray(rows) || rows.some(row => !row || typeof row.id !== 'string' || typeof row.name !== 'string')) {
      throw new ApiError(502, 'protocol');
    }
    return rows;
  }
  async settings(id) {
    return settingsData(await this.request(`/api/v1/devices/${encodeURIComponent(id)}/settings`), id);
  }
  async device(id) {
    return deviceData(await this.request(`/api/v1/devices/${encodeURIComponent(id)}`), id);
  }
  async renameDevice(id, name, csrf) {
    return deviceData(await this.request(`/api/v1/devices/${encodeURIComponent(id)}`, {
      method: 'PATCH', body: { name }, headers: { 'X-CSRF-Token': csrf },
    }), id);
  }
  async presence(id) { return this.request(`/api/v1/devices/${encodeURIComponent(id)}/presence`); }
  async createPreview(name, requestId, csrf) {
    const value = await this.request('/api/v1/devices/previews', {
      method: 'POST', body: { name, request_id: requestId }, headers: { 'X-CSRF-Token': csrf },
    });
    if (!value || typeof value.id !== 'string' || typeof value.name !== 'string' || value.is_preview !== true) throw new ApiError(502, 'protocol');
    return value;
  }
  async enrollmentCode(name, csrf) {
    const value = await this.request('/api/v1/devices/enrollment-codes', {
      method: 'POST', body: { name }, headers: { 'X-CSRF-Token': csrf },
    });
    if (!value || typeof value.id !== 'string' || !/^smalltv_enroll_[A-Za-z0-9_-]{32}$/.test(value.code)
        || value.expires_in !== 300 || !Number.isInteger(value.expires_at)) throw new ApiError(502, 'protocol');
    return value;
  }
  async enrollmentStatus(id) {
    const value = await this.request(`/api/v1/devices/enrollment-codes/${encodeURIComponent(id)}`);
    if (!value || value.id !== id || !['pending', 'enrolled', 'expired', 'closed'].includes(value.state)
        || !Number.isInteger(value.expires_in) || value.expires_in < 0 || value.expires_in > 300
        || (value.state === 'enrolled' && typeof value.device_id !== 'string')) throw new ApiError(502, 'protocol');
    return value;
  }
  cancelEnrollment(id, csrf) {
    return this.request(`/api/v1/devices/enrollment-codes/${encodeURIComponent(id)}`, {
      method: 'DELETE', headers: { 'X-CSRF-Token': csrf },
    });
  }
  async currentPet() { return this.request('/api/v1/pets/current'); }
  async weatherPreference() {
    const value = await this.request('/api/v1/preferences/weather');
    if (!value || typeof value.city !== 'string' || typeof value.timezone !== 'string') throw new ApiError(502, 'protocol');
    return value;
  }
  async saveWeatherPreference(city, csrf) {
    const value = await this.request('/api/v1/preferences/weather', {
      method: 'PATCH', body: { city }, headers: { 'X-CSRF-Token': csrf },
    });
    if (!value || typeof value.city !== 'string' || typeof value.timezone !== 'string') throw new ApiError(502, 'protocol');
    return value;
  }
  async weather() { return this.request('/api/v1/weather'); }
  async telegramStatus() { return this.request('/api/v1/telegram/status'); }
  async telegramCode(csrf) {
    return this.request('/api/v1/telegram/link-code', { method: 'POST', headers: { 'X-CSRF-Token': csrf } });
  }
  async cancelTelegramCode(csrf) {
    return this.request('/api/v1/telegram/link-code', { method: 'DELETE', headers: { 'X-CSRF-Token': csrf } });
  }
  async unlinkTelegram(csrf) {
    return this.request('/api/v1/telegram/link', { method: 'DELETE', headers: { 'X-CSRF-Token': csrf } });
  }
  async save(id, changes, csrf) {
    return settingsData(await this.request(`/api/v1/devices/${encodeURIComponent(id)}/settings`, {
      method: 'PATCH', body: changes, headers: { 'X-CSRF-Token': csrf },
    }), id);
  }
}

function sessionData(value) {
  if (!value?.user || typeof value.user.id !== 'string' || typeof value.user.display_name !== 'string'
      || !['user', 'super_admin'].includes(value.user.role) || !/^[0-9a-f]{64}$/.test(value.csrf_token)) {
    throw new ApiError(502, 'protocol');
  }
  return value;
}

function deviceData(value, expectedId) {
  if (!value || value.id !== expectedId || typeof value.name !== 'string' || !value.name.trim()
      || typeof value.is_preview !== 'boolean') throw new ApiError(502, 'protocol');
  return value;
}

function settingsData(value, expectedId) {
  if (value?.current_screen != null && !['clock','weather','monitor','codex','tamagotchi','markets'].includes(value.current_screen)) {
    throw new ApiError(502, 'protocol');
  }
  if (!value || !Number.isInteger(value.brightness) || value.brightness < 0 || value.brightness > 100
      || value.device_id !== expectedId || typeof value.theme !== 'string' || typeof value.start_screen !== 'string') {
    throw new ApiError(502, 'protocol');
  }
  try { return { ...value, ...clockSettings(value) }; }
  catch { throw new ApiError(502, 'protocol'); }
}

export function changesFrom(previous, draft) {
  return Object.fromEntries(['brightness', 'theme', 'start_screen', 'clock_format', 'clock_seconds', 'clock_style', 'device_style', 'clock_layout']
    .filter(key => draft[key] !== previous[key]).map(key => [key, draft[key]]));
}

export function hasDraftChanges(previous, draft) {
  if (!previous) return false;
  const brightness = Number(draft.brightness);
  return String(draft.brightness).trim() === '' || !Number.isFinite(brightness)
    || Object.keys(changesFrom(previous, { ...draft, brightness })).length !== 0;
}
