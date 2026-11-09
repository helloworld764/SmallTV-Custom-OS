import { ApiError } from './api.mjs';

// API reachability is distinct from the device's last server-observed heartbeat.
export class DevicePresence {
  constructor(id, { clock = () => performance.now() } = {}) {
    this.id = id;
    this.clock = clock;
    this.data = null;
    this.failed = false;
    this.busy = false;
    this.failures = 0;
    this.nextCheck = 0;
  }
  apply(value, started = this.clock()) {
    const integer = number => Number.isSafeInteger(number) && number >= 0;
    if (!value || value.device_id !== this.id || !['UNKNOWN', 'ONLINE', 'STALE', 'OFFLINE'].includes(value.state)
        || !integer(value.server_time) || !(value.last_seen_at === null || integer(value.last_seen_at))
        || !integer(value.stale_after_seconds) || value.stale_after_seconds === 0
        || !integer(value.offline_after_seconds) || value.offline_after_seconds <= value.stale_after_seconds
        || (['ONLINE', 'STALE'].includes(value.state) && (value.last_seen_at === null || value.last_seen_at > value.server_time))) {
      throw new ApiError(502, 'protocol');
    }
    this.data = { ...value };
    this.checked = started;
    this.failed = false;
    this.failures = 0;
    this.nextCheck = this.clock() + 10000;
  }
  fail(status = 0) {
    this.failed = true;
    this.failures++;
    this.nextCheck = this.clock() + Math.min(60000, 10000 * 2 ** Math.min(this.failures, 3));
    if ([401, 403, 404].includes(status)) this.data = null;
  }
  view() {
    const data = this.data;
    if (!data) return { state: this.failed ? 'UNAVAILABLE' : 'CHECKING', last_seen_at: null };
    const elapsed = Math.max(0, this.clock() - this.checked);
    // Don't display cached ONLINE indefinitely while the tab is suspended/offline.
    if (this.failed || elapsed >= 15000) return { state: 'UNAVAILABLE', last_seen_at: data.last_seen_at };
    let state = data.state;
    if (state === 'ONLINE' || state === 'STALE') {
      const age = data.server_time - data.last_seen_at + elapsed / 1000;
      if (age >= data.offline_after_seconds) state = 'OFFLINE';
      else if (age >= data.stale_after_seconds) state = 'STALE';
    }
    return { state, last_seen_at: data.last_seen_at };
  }
}

export function presenceText(model, isPreview = false) {
  if (isPreview) return { state: 'PREVIEW', text: 'Локальное превью — без физического устройства', lastSeen: 'Heartbeat устройства не отправляется.' };
  const value = model?.view() || { state: 'CHECKING', last_seen_at: null };
  const labels = {
    CHECKING: 'Проверяем связь устройства…', UNKNOWN: 'UNKNOWN — связь ещё не подтверждена',
    ONLINE: 'ONLINE — недавний heartbeat', STALE: 'STALE — heartbeat устарел',
    OFFLINE: 'OFFLINE — устройство не на связи или доступ отозван',
    UNAVAILABLE: 'Статус устройства не подтверждён — нет свежего ответа API',
  };
  return { state: value.state, text: labels[value.state], lastSeen: value.last_seen_at === null
    ? 'Последний heartbeat: ещё не получен.'
    : `Последняя полученная отметка: ${new Date(value.last_seen_at * 1000).toLocaleString('ru-RU')} (время браузера).` };
}
