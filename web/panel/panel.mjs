import { ApiClient, ApiError, changesFrom, hasDraftChanges } from './api.mjs';

const $ = selector => document.querySelector(selector);
const state = { session: null, devices: [], selected: null, settings: null, busy: false, more: false };
let telegramCodeTimer;

function clearTelegramCode() {
  clearTimeout(telegramCodeTimer);
  $('#telegram-secret').textContent = '';
  $('#telegram-secret').hidden = true;
}

async function loadTelegramStatus() {
  const result = await api.telegramStatus();
  if (typeof result?.linked !== 'boolean') throw new ApiError(502, 'protocol');
  $('#telegram-status').textContent = result.linked ? 'Telegram подключён к аккаунту.' : 'Telegram ещё не подключён.';
  $('#telegram-code').hidden = result.linked;
  $('#telegram-cancel').hidden = result.linked;
  $('#telegram-unlink').hidden = !result.linked;
  if (result.linked) clearTelegramCode();
}
const api = new ApiClient({ onConnection(online) {
  $('#connection').textContent = online ? 'Последний запрос: сервер доступен' : 'Нет связи с сервером';
  $('#connection').dataset.state = online ? 'online' : 'offline';
} });

function draft() {
  return { brightness: $('#brightness').value, theme: $('#theme').value, start_screen: $('#start-screen').value };
}

function dirty() {
  return !$('#settings-form').hidden && hasDraftChanges(state.settings, draft());
}

function canLeaveSettings() {
  if (!dirty()) return true;
  message('Есть несохранённые настройки. Сначала сохраните их или нажмите «Отменить изменения».');
  return false;
}

function message(text = '') {
  $('#message').textContent = text;
  $('#message').hidden = !text;
}

function setBusy(busy) {
  state.busy = busy;
  $('#login-fields').disabled = busy;
  $('#settings-fields').disabled = busy;
  $('#weather-city').disabled = busy;
  $('#weather-form button').disabled = busy;
  for (const button of document.querySelectorAll('#telegram-code, #telegram-cancel, #telegram-unlink, #telegram-refresh')) button.disabled = busy;
  for (const button of document.querySelectorAll('#logout, #refresh, #load-more, #retry, .device-button')) button.disabled = busy;
}

function loginView(text = '') {
  clearTelegramCode();
  state.session = null;
  state.devices = [];
  state.selected = null;
  state.settings = null;
  $('#device-list').replaceChildren();
  $('#settings-form').reset();
  $('#settings-form').hidden = true;
  $('#device-preview').hidden = true;
  $('#account-name').textContent = '';
  $('#account-role').textContent = '';
  $('#save-result').textContent = '';
  $('#weather-city').value = '';
  $('#weather-result').textContent = '';
  $('#dashboard').hidden = true;
  $('#boot').hidden = true;
  $('#login-view').hidden = false;
  $('#password').value = '';
  message(text);
}

function handleError(error, context = '') {
  if (error instanceof ApiError && error.status === 401) {
    if (context === 'login' && !state.session) message('Неверный логин или пароль.');
    else loginView('Сессия завершилась. Войдите снова.');
  } else if (error instanceof ApiError && error.status === 429) {
    message('Слишком много запросов. Подождите минуту и повторите.');
  } else if (error instanceof ApiError && error.status === 403) {
    message('Не удалось подтвердить действие. Обновите страницу и повторите.');
  } else if (error instanceof ApiError && error.status === 404) {
    state.settings = null;
    $('#settings-form').hidden = true;
    $('#device-preview').hidden = true;
    message('Устройство или его настройки недоступны. Обновите список устройств.');
  } else if (error instanceof ApiError && error.status === 422) {
    message('Проверьте введённые значения и повторите.');
  } else {
    message(context === 'save'
      ? 'Не удалось подтвердить сохранение. Проверьте связь и перечитайте настройки с сервера.'
      : context === 'logout'
        ? 'Выход не подтверждён. Проверьте связь и повторите выход.'
        : 'Сервер недоступен или ответил некорректно. Проверьте связь и повторите.');
  }
}

async function run(action, context = '', clearMessage = true) {
  if (state.busy) return;
  setBusy(true);
  if (clearMessage) message();
  try { await action(); }
  catch (error) { handleError(error, context); }
  finally { setBusy(false); }
}

function renderList() {
  $('#device-list').replaceChildren();
  for (const device of state.devices) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'device-button';
    button.setAttribute('aria-pressed', String(device.id === state.selected));
    const name = document.createElement('strong');
    name.textContent = device.name;
    const id = document.createElement('small');
    id.textContent = device.id;
    button.append(name, id);
    button.addEventListener('click', () => { if (canLeaveSettings()) run(() => selectDevice(device.id)); });
    $('#device-list').append(button);
  }
  $('#device-count').textContent = `Загружено устройств: ${state.devices.length}`;
  $('#empty').hidden = state.devices.length !== 0;
  $('#device-workspace').hidden = state.devices.length === 0;
  $('#load-more').hidden = !state.more;
}

function selectValue(select, value) {
  for (const option of select.querySelectorAll('[data-current]')) option.remove();
  if (![...select.options].some(option => option.value === value)) {
    const option = document.createElement('option');
    option.value = value;
    option.textContent = `${value} (текущее значение)`;
    option.dataset.current = 'true';
    select.append(option);
  }
  select.value = value;
}

function renderSettings() {
  $('#device-preview').href = `/simulator/?device=${encodeURIComponent(state.selected)}`;
  $('#device-preview').hidden = false;
  $('#brightness').value = state.settings.brightness;
  $('#brightness-range').value = state.settings.brightness;
  selectValue($('#theme'), state.settings.theme);
  selectValue($('#start-screen'), state.settings.start_screen);
  $('#settings-form').hidden = false;
  $('#dirty-note').hidden = true;
}

async function selectDevice(id) {
  state.selected = id;
  state.settings = null;
  $('#settings-form').hidden = true;
  $('#device-preview').hidden = true;
  $('#save-result').textContent = '';
  $('#settings-title').textContent = state.devices.find(device => device.id === id)?.name || 'Настройки устройства';
  $('#settings-loading').hidden = false;
  renderList();
  setBusy(true);
  try {
    state.settings = await api.settings(id);
    renderSettings();
  } finally { $('#settings-loading').hidden = true; }
}

async function loadDevices(append = false) {
  const previousSelection = state.selected;
  const rows = await api.devices(append ? state.devices.length : 0);
  state.devices = append ? [...state.devices, ...rows] : rows;
  state.more = rows.length === 50;
  renderList();
  if (!append) {
    state.selected = null;
    state.settings = null;
    $('#settings-form').hidden = true;
    $('#device-preview').hidden = true;
    $('#save-result').textContent = '';
    if (rows.length) await selectDevice(rows.some(device => device.id === previousSelection) ? previousSelection : rows[0].id);
  }
}

async function dashboard(session) {
  state.session = session;
  state.devices = [];
  state.settings = null;
  state.selected = null;
  state.more = false;
  renderList();
  $('#settings-form').hidden = true;
  $('#device-preview').hidden = true;
  $('#boot').hidden = true;
  $('#login-view').hidden = true;
  $('#dashboard').hidden = false;
  $('#account-name').textContent = session.user.display_name;
  $('#account-role').textContent = session.user.role === 'super_admin' ? 'Администратор' : 'Пользователь';
  $('#devices-title').textContent = session.user.role === 'super_admin' ? 'Все устройства' : 'Мои устройства';
  const weatherPreference = await api.weatherPreference();
  $('#weather-city').value = weatherPreference.city;
  clearTelegramCode();
  await loadTelegramStatus();
  await loadDevices();
}

async function bootstrap() {
  $('#retry').hidden = true;
  try { await dashboard(await api.session()); }
  catch (error) {
    if (error instanceof ApiError && error.status === 401) loginView();
    else { $('#retry').hidden = false; throw error; }
  }
}

$('#retry').addEventListener('click', () => run(bootstrap));
$('#telegram-refresh').addEventListener('click', () => run(loadTelegramStatus));
$('#telegram-code').addEventListener('click', () => run(async () => {
  clearTelegramCode();
  const result = await api.telegramCode(state.session.csrf_token);
  if (!/^[A-Za-z0-9_-]{24}$/.test(result?.code) || result.expires_in !== 300) throw new ApiError(502, 'protocol');
  $('#telegram-secret').textContent = `Отправьте боту: /link ${result.code}`;
  $('#telegram-secret').hidden = false;
  telegramCodeTimer = setTimeout(clearTelegramCode, result.expires_in * 1000);
}));
$('#telegram-cancel').addEventListener('click', () => run(async () => {
  clearTelegramCode();
  await api.cancelTelegramCode(state.session.csrf_token);
  $('#telegram-status').textContent = 'Код отменён.';
}));
$('#telegram-unlink').addEventListener('click', () => {
  if (!window.confirm('Отключить Telegram от этого аккаунта?')) return;
  run(async () => { await api.unlinkTelegram(state.session.csrf_token); clearTelegramCode(); await loadTelegramStatus(); });
});
$('#login-form').addEventListener('submit', event => {
  event.preventDefault();
  if (state.busy) return;
  const username = $('#username').value;
  const password = $('#password').value;
  $('#password').value = '';
  run(async () => { await dashboard(await api.login(username, password)); }, 'login');
});
$('#weather-form').addEventListener('submit', event => {
  event.preventDefault();
  const city = $('#weather-city').value.trim();
  if (!city || !state.session) return;
  $('#weather-result').textContent = '';
  run(async () => {
    const saved = await api.saveWeatherPreference(city, state.session.csrf_token);
    $('#weather-city').value = saved.city;
    $('#weather-result').textContent = `Город сохранён: ${saved.city} (${saved.timezone}).`;
  }, 'weather-save');
});
$('#logout').addEventListener('click', () => {
  if (!canLeaveSettings()) return;
  run(async () => {
    await api.logout(state.session.csrf_token);
    loginView();
    $('#username').focus();
  }, 'logout');
});
$('#refresh').addEventListener('click', () => { if (canLeaveSettings()) run(() => loadDevices()); });
$('#load-more').addEventListener('click', () => run(() => loadDevices(true)));
$('#reload-settings').addEventListener('click', () => { if (canLeaveSettings()) run(() => selectDevice(state.selected)); });
$('#discard-settings').addEventListener('click', () => {
  if (!state.settings) return;
  renderSettings();
  message();
  $('#save-result').textContent = 'Несохранённые изменения отменены.';
});
$('#settings-form').addEventListener('input', () => {
  $('#save-result').textContent = '';
  $('#dirty-note').hidden = !dirty();
});
$('#brightness').addEventListener('input', () => { $('#brightness-range').value = $('#brightness').value; });
$('#brightness-range').addEventListener('input', () => { $('#brightness').value = $('#brightness-range').value; });
$('#settings-form').addEventListener('submit', event => {
  event.preventDefault();
  if (!state.settings || !$('#settings-form').reportValidity()) return;
  run(async () => {
    $('#save-result').textContent = '';
    const changes = changesFrom(state.settings, {
      brightness: Number($('#brightness').value), theme: $('#theme').value, start_screen: $('#start-screen').value,
    });
    if (!Object.keys(changes).length) { $('#save-result').textContent = 'Изменений нет.'; return; }
    state.settings = await api.save(state.selected, changes, state.session.csrf_token);
    renderSettings();
    $('#save-result').textContent = 'Настройки сохранены на сервере.';
  }, 'save');
});

// Check expiry while the page is active; never silently replay a mutation.
setInterval(() => {
  if (!state.session || state.busy || document.hidden || document.activeElement?.closest('form')) return;
  run(async () => {
    const session = await api.session();
    if (session.user.id !== state.session.user.id || session.user.role !== state.session.user.role) await dashboard(session);
    else state.session = session;
  }, '', false);
}, 30000);
window.addEventListener('pageshow', event => { if (event.persisted) run(bootstrap); });
window.addEventListener('beforeunload', event => {
  if (dirty()) { event.preventDefault(); event.returnValue = ''; }
});
run(bootstrap);
