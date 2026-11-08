// Bounded in-memory overlays. No task text, tokens, storage or device heartbeat.
const MESSAGES = Object.freeze({
  DONE: 'Codex: ответ завершён\nРезультат требует проверки',
  ERROR: 'Codex: ошибка задания',
  INTERRUPTED: 'Codex: задание прервано',
});
const PRIORITY = Object.freeze({ DONE: 200, ERROR: 300, INTERRUPTED: 150 });
const terminal = kind => Object.hasOwn(MESSAGES, kind);

export class Notifications {
  constructor(deviceId) {
    this.deviceId = deviceId;
    this.reset();
  }

  reset() {
    this.cursor = 0;
    this.queue = [];
    this.active = null;
    this.seen = new Map();
    this.order = 0;
  }

  enqueue(item, now) {
    for (const [id, expiry] of this.seen) if (expiry <= now) this.seen.delete(id);
    if (item.expiresAt <= now || this.seen.has(item.id)) return;
    this.seen.set(item.id, item.expiresAt);
    if (this.seen.size > 128) this.seen.delete(this.seen.keys().next().value);
    this.queue.push({ ...item, order: this.order++, duration: 4000 });
    this.queue.sort((a, b) => b.priority - a.priority || a.order - b.order);
    this.queue = this.queue.slice(0, 8);
  }

  apply(page, now, requestStartedAt = now) {
    // Validate the whole response before accepting a cursor or a single overlay.
    const integer = value => Number.isSafeInteger(value) && value >= 0;
    if (!page || page.device_id !== this.deviceId || !integer(page.server_time)
        || !integer(page.next_cursor) || !Array.isArray(page.events) || page.events.length > 20
        || !Number.isFinite(now) || !Number.isFinite(requestStartedAt) || requestStartedAt > now) {
      throw new Error('Invalid notification page');
    }
    let previous = 0;
    const ids = new Set();
    for (const event of page.events) {
      if (!event || typeof event.id !== 'string' || !event.id || event.id.length > 160 || ids.has(event.id)
          || !terminal(event.kind) || !integer(event.cursor) || event.cursor <= previous
          || event.cursor > page.next_cursor || !Number.isInteger(event.ttl_seconds)
          || event.ttl_seconds < 1 || event.ttl_seconds > 60) throw new Error('Invalid notification event');
      previous = event.cursor;
      ids.add(event.id);
    }
    for (const event of page.events) {
      this.enqueue({ id: event.id, text: MESSAGES[event.kind], priority: PRIORITY[event.kind],
                     expiresAt: requestStartedAt + event.ttl_seconds * 1000 }, now);
    }
    this.cursor = page.next_cursor;
  }

  local(text, offline, now) {
    this.enqueue({ id: `connection:${this.order}`, text, priority: offline ? 400 : 100,
                   expiresAt: now + 10000 }, now);
  }

  tick(now) {
    this.queue = this.queue.filter(item => item.expiresAt > now);
    if (this.active && this.active.until <= now) this.active = null;
    if (this.active && this.queue[0]?.priority > this.active.priority) {
      this.queue.push({ ...this.active, duration: this.active.until - now });
      this.active = null;
      this.queue.sort((a, b) => b.priority - a.priority || a.order - b.order);
      this.queue = this.queue.slice(0, 8);
    }
    if (!this.active && this.queue.length) {
      const item = this.queue.shift();
      this.active = { ...item, until: Math.min(now + item.duration, item.expiresAt) };
    }
    return this.active?.text ?? null;
  }
}

export function readNotifications(api, deviceId, cursor) {
  return api.request(`/api/v1/devices/${encodeURIComponent(deviceId)}/notifications?after=${cursor}`);
}
