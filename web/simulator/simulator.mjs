import { Connection, checkHealth } from './connection.mjs';
import { ApiClient } from '../panel/api.mjs';
import { PreviewSettings, THEMES } from './preview.mjs';
import { Notifications, readNotifications } from './notifications.mjs';

const connection = new Connection();
const deviceId = new URLSearchParams(window.location.search).get('device');
const preview = new PreviewSettings(deviceId);
const notifications = new Notifications(deviceId);
const api = new ApiClient({ timeout: 2500 });
const indicator = document.querySelector('#indicator');
const status = document.querySelector('#status');
const checked = document.querySelector('#checked');
const notice = document.querySelector('#notice');
const labels = {
  unknown: 'Проверка связи…', online: 'Backend доступен',
  stale: 'Данные устарели · проверяем', offline: 'Связь с backend потеряна',
};
const eventStatus = document.querySelector('#event-status');

function renderPreview() {
  const settings = preview.settings;
  document.querySelector('.display').dataset.theme = preview.theme;
  document.querySelector('#screen-content').style.opacity = preview.canDisplaySettings ? settings.brightness / 100 : 0;
  const clock = settings.start_screen === 'clock';
  document.querySelector('#clock-content').hidden = !clock;
  document.querySelector('#placeholder').hidden = clock;
  document.querySelector('#screen-title').textContent = preview.screenTitle;
  const labels = {
    standalone: 'Автономное превью часов. Откройте превью из настроек устройства, чтобы читать их с сервера.',
    loading: 'Читаем сохранённые настройки устройства…',
    fresh: `Настройки с сервера: ${settings.theme}, яркость ${settings.brightness}%, стартовый экран «${preview.screenTitle}».`,
    stale: preview.updatedAt === null ? 'Настройки не загружены. Сервер временно недоступен.' : 'Настройки устарели: показана последняя полученная версия.',
    'signed-out': 'Для просмотра настроек войдите в панель управления. Локальные часы и проверка backend продолжают работать.',
    unavailable: 'Устройство или его настройки недоступны этому аккаунту.',
  };
  document.querySelector('#preview-status').textContent = labels[preview.state]
    + (preview.state === 'fresh' && !THEMES[settings.theme] ? ' Тема пока не поддержана в превью; использована BlackArch Red.' : '');
}

function render() {
  indicator.dataset.state = connection.state;
  indicator.setAttribute('aria-label', labels[connection.state]);
  status.textContent = labels[connection.state];
  if (connection.lastSuccess !== null) {
    checked.textContent = `Последняя успешная проверка: ${new Date(connection.lastSuccess).toLocaleTimeString('ru-RU')}`;
  }
}

async function poll() {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 2500);
  let ok = false;
  try {
    await checkHealth(fetch, controller.signal);
    ok = true;
  } catch {
    // Network errors, timeout and malformed responses all invalidate freshness.
  } finally {
    clearTimeout(timeout);
  }
  const message = connection.record(ok, Date.now());
  if (message) {
    notifications.local(message, connection.state === 'offline', performance.now());
  }
  render();
  if (deviceId) {
    if (ok) {
      try {
        preview.apply(await api.settings(deviceId), Date.now());
        const startedAt = performance.now();
        const page = await readNotifications(api, deviceId, notifications.cursor);
        notifications.apply(page, performance.now(), startedAt);
        eventStatus.textContent = 'Уведомления Codex подключены. DONE означает завершение ответа, а не проверку результата.';
      } catch (error) {
        if ([401, 403, 404].includes(error.status)) {
          preview.fail(error.status);
          notifications.reset();
          notice.hidden = true;
          notice.textContent = '';
          eventStatus.textContent = 'Для уведомлений нужен доступ к выбранному устройству.';
        } else {
          preview.fail();
          eventStatus.textContent = 'Уведомления временно недоступны; повторим проверку.';
        }
      }
    } else {
      preview.fail();
      eventStatus.textContent = 'Уведомления временно недоступны; повторим проверку.';
    }
    renderPreview();
  }
  // Schedule only after completion: requests cannot overlap.
  setTimeout(poll, connection.delay);
}

function tick() {
  const now = new Date();
  document.querySelector('#clock').textContent = now.toLocaleTimeString('ru-RU');
  document.querySelector('#date').textContent = now.toLocaleDateString('ru-RU', {
    weekday: 'short', day: 'numeric', month: 'long',
  });
  connection.age(now.getTime());
  const message = notifications.tick(performance.now());
  notice.textContent = message ?? '';
  notice.hidden = message === null;
  render();
}

tick();
renderPreview();
setInterval(tick, 1000);
poll();
