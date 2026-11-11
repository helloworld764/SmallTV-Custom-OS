import test from 'node:test';
import assert from 'node:assert/strict';
import { Connection, checkHealth } from '../web/simulator/connection.mjs';
import { PreviewSettings } from '../web/simulator/preview.mjs';
import { Notifications, readNotifications } from '../web/simulator/notifications.mjs';
import { CLOCK_DEFAULTS, CLOCK_STYLES, formatClock } from '../web/shared/clock.mjs';
import { renderClock } from '../web/simulator/clock-render.mjs';

test('fast settings refresh never overlaps and unlocks after failures', async () => {
  const preview = new PreviewSettings('tv');
  let finish, calls=0;
  const load=() => {calls++;return new Promise(resolve => {finish=resolve;});};
  const first=preview.refresh(load,()=>1000);
  assert.equal(await preview.refresh(load),false);
  assert.equal(calls,1);
  finish({device_id:'tv',start_screen:'clock',current_screen:'weather'});
  assert.equal(await first,true);
  assert.equal(preview.screen,'weather');
  await assert.rejects(preview.refresh(async () => {throw new Error('offline');}));
  assert.equal(await preview.refresh(async () => ({device_id:'tv',start_screen:'clock',current_screen:'markets'})),true);
  assert.equal(preview.screen,'markets');
});

test('quick control changes active screen independently and home follows startup setting', () => {
  const preview = new PreviewSettings('tv');
  preview.apply({device_id:'tv',start_screen:'clock',current_screen:'weather'},1000);
  assert.equal(preview.screen,'weather');
  assert.equal(preview.settings.start_screen,'clock');
  assert.equal(preview.screenTitle,'Погода');
  preview.fail();
  assert.equal(preview.screen,'weather');
  preview.apply({device_id:'tv',start_screen:'tamagotchi',current_screen:null},2000);
  assert.equal(preview.screen,'tamagotchi');
  preview.fail(401);
  assert.equal(preview.screen,'clock');
});

test('device style is independent of stacked and analog clock faces', () => {
  const hands = Object.fromEntries(['hour','minute','second'].map(name => ['.'+name,{style:{}}]));
  const analog = { querySelector: key => hands[key], setAttribute() {} };
  const nodes = { '.display':{dataset:{}}, '#clock':{}, '#clock-period':{}, '#analog-clock':analog };
  const root = {querySelector:key => nodes[key]};
  const now = new Date(2026,10,11,12,15,30);
  renderClock(root, {device_style:'orbit',clock_layout:'stacked',clock_seconds:false}, now);
  assert.equal(nodes['#clock'].textContent,'12\n15');
  assert.equal(nodes['.display'].dataset.deviceStyle,'orbit');
  renderClock(root, {device_style:'terminal',clock_layout:'analog',clock_seconds:false}, now);
  assert.equal(analog.hidden,false);
  assert.equal(nodes['#clock'].hidden,true);
  assert.equal(hands['.hour'].style.transform,'rotate(7.5deg)');
  assert.equal(hands['.minute'].style.transform,'rotate(90deg)');
  assert.equal(hands['.second'].hidden,true);
  renderClock(root, {device_style:'minimal',clock_layout:'regular'}, now);
  assert.equal(analog.hidden,true);
  assert.equal(nodes['#clock'].hidden,false);
});

test('clock settings immediately update the rendered DOM and restore previous styles cleanly', () => {
  const nodes = { '.display': { dataset: {} }, '#clock': {}, '#clock-period': {} };
  const root = { querySelector: selector => nodes[selector] };
  const now = new Date(2026, 10, 10, 13, 5, 9);
  for (const style of ['classic', 'minimal', 'terminal', 'classic']) {
    renderClock(root, { clock_style: style, clock_format: '12h', clock_seconds: false }, now);
    assert.deepEqual(nodes['.display'].dataset, { clockStyle: style, clockSeconds: 'false', deviceStyle:style, clockLayout:'regular' });
    assert.equal(nodes['#clock'].textContent, '01:05');
    assert.equal(nodes['#clock-period'].textContent, 'PM');
    assert.equal(nodes['#clock-period'].hidden, false);
  }
  renderClock(root, CLOCK_DEFAULTS, now);
  assert.equal(nodes['#clock'].textContent, '13:05:09');
  assert.equal(nodes['#clock-period'].hidden, true);
  assert.equal(nodes['.display'].dataset.clockSeconds, 'true');
});

test('clock formats midnight, noon and evening correctly in 12 and 24 hour modes', () => {
  for (const [hour, expected, period] of [[0, '12', 'AM'], [12, '12', 'PM'], [23, '11', 'PM'], [9, '09', 'AM']]) {
    const date = new Date(2026, 10, 10, hour, 5, 7);
    assert.deepEqual(formatClock(date, { clock_format: '12h' }), { time: `${expected}:05:07`, period });
    assert.deepEqual(formatClock(date), { time: `${String(hour).padStart(2, '0')}:05:07`, period: '' });
  }
});

test('clock decoration reports the real local format, date and day progress without fake sync', () => {
  const properties = {};
  const nodes = { '.display': { dataset: {} }, '#clock': {}, '#clock-period': {}, '#clock-format-label': {},
    '#date': {}, '#clock-content': { style: { setProperty: (key, value) => { properties[key] = value; } } } };
  const root = { querySelector: selector => nodes[selector] };
  renderClock(root, CLOCK_DEFAULTS, new Date(2026, 10, 10, 12, 0, 0));
  assert.equal(nodes['#clock-format-label'].textContent, '24H');
  assert.equal(properties['--day-progress'], '50.00%');
  assert.match(nodes['#date'].textContent, /10/);
  renderClock(root, { clock_format: '12h' }, new Date(2026, 10, 10, 0, 0, 0));
  assert.equal(nodes['#clock-format-label'].textContent, '12H · AM/PM');
  assert.equal(properties['--day-progress'], '0.00%');
});

test('all clock styles support seconds on and off without changing the date or time zone', () => {
  const date = new Date(2026, 10, 10, 13, 2, 59);
  for (const clock_style of Object.keys(CLOCK_STYLES)) {
    assert.equal(formatClock(date, { clock_style, clock_seconds: false }).time, '13:02');
    assert.equal(formatClock(date, { clock_style, clock_seconds: true }).time, '13:02:59');
  }
});

test('clock settings survive a connection outage and clear after denied access', () => {
  const preview = new PreviewSettings('tv');
  preview.apply({ device_id: 'tv', ...CLOCK_DEFAULTS, clock_style: 'terminal', clock_seconds: false }, 1000);
  preview.fail();
  assert.equal(preview.settings.clock_seconds, false);
  assert.equal(preview.settings.clock_style, 'terminal');
  preview.fail(401);
  assert.equal(preview.settings.clock_seconds, true);
  assert.equal(preview.settings.clock_style, 'classic');
});

function notificationPage(events = [{ id: 'event-one', cursor: 1, kind: 'DONE', ttl_seconds: 60 }]) {
  return { device_id: 'device-one', server_time: 1000, next_cursor: events.at(-1)?.cursor ?? 0, events };
}

test('Codex notification replay does not duplicate or extend a completed overlay', () => {
  const inbox = new Notifications('device-one');
  inbox.apply(notificationPage(), 0);
  assert.match(inbox.tick(0), /ответ завершён/);
  inbox.apply(notificationPage(), 3000);
  assert.match(inbox.tick(3000), /Результат требует проверки/);
  assert.equal(inbox.tick(4000), null);
  assert.equal(inbox.queue.length, 0);
  assert.equal(inbox.cursor, 1);
});

test('errors have priority and overlays never change the underlying preview screen', () => {
  const inbox = new Notifications('device-one');
  const preview = new PreviewSettings('device-one');
  preview.apply({ device_id: 'device-one', theme: 'Dark Blue', brightness: 17, start_screen: 'weather' }, 0);
  const before = structuredClone(preview.settings);
  inbox.apply(notificationPage([
    { id: 'done', cursor: 1, kind: 'DONE', ttl_seconds: 60 },
    { id: 'error', cursor: 2, kind: 'ERROR', ttl_seconds: 60 },
  ]), 0);
  assert.match(inbox.tick(0), /ошибка/);
  assert.match(inbox.tick(4000), /ответ завершён/);
  assert.equal(inbox.tick(8000), null);
  assert.deepEqual(preview.settings, before);
});

test('connection outage preempts a Codex overlay then resumes only its remaining duration', () => {
  const inbox = new Notifications('device-one');
  inbox.apply(notificationPage(), 0);
  inbox.tick(0);
  inbox.local('Связь потеряна', true, 1000);
  assert.equal(inbox.tick(1000), 'Связь потеряна');
  assert.match(inbox.tick(5000), /ответ завершён/);
  assert.equal(inbox.tick(8000), null);
});

test('TTL expires during display and network delay cannot extend notification lifetime', () => {
  const page = notificationPage([{ id: 'short', cursor: 1, kind: 'ERROR', ttl_seconds: 1 }]);
  const inbox = new Notifications('device-one');
  inbox.apply(page, 0);
  assert.match(inbox.tick(0), /ошибка/);
  assert.equal(inbox.tick(1000), null);
  inbox.reset();
  inbox.apply(page, 2000, 0);
  assert.equal(inbox.tick(2000), null);
  assert.equal(inbox.cursor, 1);
});

test('invalid and wrong-device responses cannot partially enqueue or advance cursor', () => {
  const invalid = [
    { ...notificationPage(), device_id: 'foreign' },
    { ...notificationPage(), next_cursor: Number.MAX_SAFE_INTEGER + 1 },
    { ...notificationPage(), events: null },
    notificationPage([{ id: 'bad', cursor: 1, kind: 'START', ttl_seconds: 60 }]),
    notificationPage([{ id: 'bad', cursor: 1, kind: 'DONE', ttl_seconds: 61 }]),
    notificationPage([{ id: 'same', cursor: 1, kind: 'DONE', ttl_seconds: 60 },
                      { id: 'same', cursor: 2, kind: 'ERROR', ttl_seconds: 60 }]),
    notificationPage([{ id: 'one', cursor: 2, kind: 'DONE', ttl_seconds: 60 },
                      { id: 'two', cursor: 1, kind: 'ERROR', ttl_seconds: 60 }]),
  ];
  for (const page of invalid) {
    const inbox = new Notifications('device-one');
    assert.throws(() => inbox.apply(page, 0));
    assert.equal(inbox.cursor, 0);
    assert.equal(inbox.tick(0), null);
  }
});

test('notification buffers are bounded and highest priority survives overflow', () => {
  const inbox = new Notifications('device-one');
  for (let page = 0; page < 8; page++) {
    inbox.apply(notificationPage(Array.from({ length: 20 }, (_, i) => ({
      id: `event-${page * 20 + i}`, cursor: page * 20 + i + 1,
      kind: i === 19 ? 'ERROR' : 'DONE', ttl_seconds: 60,
    }))), 0);
  }
  assert.equal(inbox.queue.length, 8);
  assert.equal(inbox.seen.size, 128);
  assert.match(inbox.tick(0), /ошибка/);
  assert.equal(inbox.tick(61000), null);
});

test('session denial reset clears private overlays and permits a fresh cursor after DB restore', () => {
  const inbox = new Notifications('device-one');
  inbox.apply(notificationPage(), 0);
  inbox.tick(0);
  inbox.apply({ ...notificationPage([]), next_cursor: 0 }, 1000);
  assert.equal(inbox.cursor, 0); // Rewind does not erase deduplication of live IDs.
  inbox.apply(notificationPage(), 1000);
  assert.equal(inbox.queue.length, 0);
  inbox.reset();
  assert.equal(inbox.tick(1000), null);
  assert.equal(inbox.cursor, 0);
  assert.equal(inbox.seen.size, 0);
});

test('notification API request uses the device scope and resumes after the accepted cursor', async () => {
  const calls = [];
  const api = { request: async path => { calls.push(path); return notificationPage(); } };
  await readNotifications(api, 'device/with space', 12);
  assert.deepEqual(calls, ['/api/v1/devices/device%2Fwith%20space/notifications?after=12']);
});

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
  assert.deepEqual(preview.settings, { ...initial, ...CLOCK_DEFAULTS, device_style:'classic', clock_layout:'regular' });
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
