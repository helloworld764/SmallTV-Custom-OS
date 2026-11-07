import { Connection, checkHealth } from './connection.mjs';

const connection = new Connection();
const indicator = document.querySelector('#indicator');
const status = document.querySelector('#status');
const checked = document.querySelector('#checked');
const notice = document.querySelector('#notice');
const labels = {
  unknown: 'Проверка связи…', online: 'Backend доступен',
  stale: 'Данные устарели · проверяем', offline: 'Связь с backend потеряна',
};
let noticeTimer;

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
    clearTimeout(noticeTimer);
    notice.textContent = message;
    notice.hidden = false;
    noticeTimer = setTimeout(() => { notice.hidden = true; }, 4000);
  }
  render();
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
  render();
}

tick();
setInterval(tick, 1000);
poll();
