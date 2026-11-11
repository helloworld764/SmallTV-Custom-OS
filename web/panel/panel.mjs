import { ApiClient, ApiError, changesFrom, hasDraftChanges } from './api.mjs';
import { EnrollmentFlow } from './enrollment.mjs';
import { DevicePresence, presenceText } from './device-presence.mjs';

const $ = selector => document.querySelector(selector);
const state = { session: null, devices: [], selected: null, settings: null, busy: false, more: false, loaded: 0, presence: null };
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
const enrollment = new EnrollmentFlow(api);
let enrollmentTimer;

function clearEnrollment() {
  clearInterval(enrollmentTimer);
  enrollment.reset();
  $('#enrollment-secret').textContent = '';
  $('#enrollment-result').textContent = '';
  $('#enrollment-time').textContent = '';
  $('#enrollment-code-area').hidden = true;
  $('#enrollment-form').reset();
  $('#enrollment-mode').dispatchEvent(new Event('change'));
  $('#enrollment-dialog').close();
}

function renderEnrollment() {
  const hasCode = Boolean(enrollment.code);
  $('#enrollment-fields').disabled = state.busy || hasCode || Boolean(enrollment.preview);
  // An ambiguous preview request may be explicitly retried with the same id/name.
  $('#enrollment-submit').disabled = state.busy || hasCode;
  if (enrollment.preview) {
    $('#enrollment-fields').disabled = state.busy;
    $('#enrollment-name').readOnly = true;
    $('#enrollment-mode').disabled = true;
  } else {
    $('#enrollment-name').readOnly = false;
    $('#enrollment-mode').disabled = state.busy || hasCode;
  }
  $('#enrollment-code-area').hidden = !hasCode;
  $('#enrollment-check').disabled = state.busy;
  $('#enrollment-close').disabled = state.busy;
  $('#enrollment-abandon').hidden = !hasCode;
  $('#enrollment-abandon').disabled = state.busy;
  $('#enrollment-close').textContent = hasCode ? 'Отменить код и закрыть' : 'Закрыть';
  if (hasCode) {
    const seconds = enrollment.remaining();
    $('#enrollment-secret').textContent = enrollment.code.code;
    $('#enrollment-secret').hidden = !enrollment.code.code;
    $('#enrollment-time').textContent = seconds ? `Срок действия: ещё ${seconds} с.` : 'Код больше не отображается. Проверьте результат подключения.';
  }
}

async function showAddedDevice(deviceId) {
  await loadDevices();
  // A new UUID may be beyond the first page. Fetch it without loading all devices.
  if (!state.devices.some(device => device.id === deviceId)) {
    const added = await api.device(deviceId);
    state.devices.unshift(added);
  }
  await selectDevice(deviceId);
}

async function enrollmentAction(action) {
  if (state.busy) return;
  await run(async () => {
    try { await action(); }
    catch (error) {
      if (error instanceof ApiError && error.status === 401) throw error;
      if (!$('#enrollment-dialog').open) {
        message('Устройство создано, но загрузить его настройки не удалось. Обновите список устройств.');
        return;
      }
      $('#enrollment-result').textContent = error.status === 429
        ? 'Достигнут лимит. Для кодов подождите до 5 минут, для превью — минуту.'
        : error.status === 403 ? 'Действие не подтверждено. Закройте мастер и обновите страницу.'
          : error.status === 422 ? 'Проверьте имя устройства (1–80 символов).'
            : error.status === 404 ? 'Код недоступен. Закройте мастер и начните заново.'
              : 'Результат запроса не подтверждён. Повторите действие. Повтор создания превью не создаст дубликат; потерянный код истечёт через 5 минут.';
    }
  });
  renderEnrollment();
}

function draft() {
  return { brightness: $('#brightness').value, theme: $('#theme').value, start_screen: $('#start-screen').value,
    clock_format: $('#clock-format').value, clock_seconds: $('#clock-seconds').checked, clock_style: state.settings.clock_style, device_style: $('#device-style').value, clock_layout: $('#clock-style').value };
}

function settingsDirty() {
  return !$('#settings-form').hidden && hasDraftChanges(state.settings, draft());
}

function selectedDevice() { return state.devices.find(device => device.id === state.selected); }
function nameDirty() {
  return !$('#device-details').hidden && selectedDevice() && $('#device-name').value !== selectedDevice().name;
}
function dirty() { return settingsDirty() || nameDirty(); }

function clearDeviceDetails() {
  state.presence = null;
  $('#device-details').hidden = true;
  $('#device-name').value = '';
  $('#rename-result').textContent = '';
  $('#device-presence').textContent = '';
  $('#device-last-seen').textContent = '';
}

function renderPresence() {
  if ($('#device-details').hidden) return;
  const preview = selectedDevice()?.is_preview === true;
  const view = presenceText(state.presence, preview);
  if ($('#device-presence').textContent !== view.text) $('#device-presence').textContent = view.text;
  $('#device-presence').dataset.state = view.state;
  if ($('#device-last-seen').textContent !== view.lastSeen) $('#device-last-seen').textContent = view.lastSeen;
  $('#presence-refresh').hidden = preview;
  $('#presence-refresh').disabled = state.busy || Boolean(state.presence?.busy);
}

async function refreshDevicePresence() {
  const model = state.presence;
  if (!model || model.busy || selectedDevice()?.is_preview || !state.session) return;
  model.busy = true;
  renderPresence();
  const started = model.clock();
  try {
    const value = await api.presence(model.id);
    if (state.presence === model) model.apply(value, started);
  } catch (error) {
    if (state.presence !== model) return;
    model.fail(error.status);
    if (error.status === 401) loginView('Сессия завершилась. Войдите снова.');
    else if ([403, 404].includes(error.status)) {
      clearDeviceDetails();
      handleError(error);
    }
  } finally {
    model.busy = false;
    if (state.presence === model) renderPresence();
  }
}

function applyDeviceName(device) {
  state.devices = state.devices.map(row => row.id === device.id ? device : row);
  $('#device-name').value = device.name;
  $('#settings-title').textContent = device.name;
  renderList();
  renderPresence();
}

function canLeaveSettings() {
  if (!dirty()) return true;
  message('Есть несохранённые настройки или имя устройства. Сохраните их или отмените ввод в соответствующей форме.');
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
  $('#rename-fields').disabled = busy;
  $('#weather-city').disabled = busy;
  $('#weather-form button').disabled = busy;
  for (const button of document.querySelectorAll('#telegram-code, #telegram-cancel, #telegram-unlink, #telegram-refresh')) button.disabled = busy;
  for (const button of document.querySelectorAll('#logout, #refresh, #load-more, #retry, #add-device, .device-button')) button.disabled = busy;
  renderEnrollment();
  renderPresence();
}

function loginView(text = '') {
  clearDeviceDetails();
  clearEnrollment();
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
    clearDeviceDetails();
    state.settings = null;
    $('#settings-form').hidden = true;
    $('#device-preview').hidden = true;
    message('Устройство или его настройки недоступны. Обновите список устройств.');
  } else if (error instanceof ApiError && error.status === 422) {
    message('Проверьте введённые значения и повторите.');
  } else {
    message(context === 'rename' ? 'Результат переименования не подтверждён. Проверьте связь и нажмите «Перечитать имя».'
      : context === 'save'
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
    id.textContent = device.is_preview ? `Локальное превью · ${device.id}` : device.id;
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
  $('#clock-format').value = state.settings.clock_format;
  $('#clock-seconds').checked = state.settings.clock_seconds;
  $('#clock-style').value = state.settings.clock_layout;
  $('#device-style').value = state.settings.device_style;
  $('#settings-form').hidden = false;
  $('#dirty-note').hidden = true;
}

async function selectDevice(id) {
  clearDeviceDetails();
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
    $('#device-name').value = selectedDevice().name;
    $('#device-details').hidden = false;
    state.presence = new DevicePresence(id);
    renderPresence();
    void refreshDevicePresence();
  } finally { $('#settings-loading').hidden = true; }
}

async function loadDevices(append = false) {
  const previousSelection = state.selected;
  const rows = await api.devices(append ? state.loaded : 0);
  state.loaded = (append ? state.loaded : 0) + rows.length;
  state.devices = append ? [...new Map([...state.devices, ...rows].map(device => [device.id, device])).values()] : rows;
  state.more = rows.length === 50;
  renderList();
  if (!append) {
    clearDeviceDetails();
    state.selected = null;
    state.settings = null;
    $('#settings-form').hidden = true;
    $('#device-preview').hidden = true;
    $('#save-result').textContent = '';
    if (rows.length) await selectDevice(rows.some(device => device.id === previousSelection) ? previousSelection : rows[0].id);
  }
}

async function dashboard(session) {
  clearDeviceDetails();
  clearEnrollment();
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
$('#presence-refresh').addEventListener('click', () => { if (!state.busy) void refreshDevicePresence(); });
$('#rename-form').addEventListener('submit', event => {
  event.preventDefault();
  const name = $('#device-name').value.trim();
  if (!name || !state.selected || !state.session) return;
  run(async () => {
    $('#rename-result').textContent = '';
    if (name === selectedDevice().name) {
      $('#device-name').value = name;
      $('#rename-result').textContent = 'Имя не изменилось.';
      return;
    }
    const session = state.session, id = state.selected;
    const device = await api.renameDevice(id, name, session.csrf_token);
    if (state.session !== session || state.selected !== id) return;
    applyDeviceName(device);
    $('#rename-result').textContent = 'Имя устройства сохранено.';
  }, 'rename');
});
$('#rename-discard').addEventListener('click', () => {
  if (!selectedDevice()) return;
  $('#device-name').value = selectedDevice().name;
  $('#rename-result').textContent = 'Ввод имени отменён.';
});
$('#rename-reload').addEventListener('click', () => run(async () => {
  const session = state.session, id = state.selected;
  const device = await api.device(id);
  if (state.session !== session || state.selected !== id) return;
  applyDeviceName(device);
  $('#rename-result').textContent = 'Имя перечитано с сервера.';
}));
$('#device-name').addEventListener('input', () => { $('#rename-result').textContent = ''; });
$('#add-device').addEventListener('click', () => {
  if (state.busy || !state.session || !canLeaveSettings()) return;
  renderEnrollment();
  $('#enrollment-dialog').showModal();
  $('#enrollment-name').focus();
});
$('#enrollment-mode').addEventListener('change', () => {
  const preview = $('#enrollment-mode').value === 'preview';
  $('#enrollment-submit').textContent = preview ? 'Создать превью' : 'Получить одноразовый код';
  $('#enrollment-help').textContent = preview
    ? 'Создаст запись в вашем аккаунте для Storm Simulator. Превью использует сессию браузера и не отправляет heartbeat устройства.'
    : 'Для тестового программного клиента. Настройка Wi-Fi и показ кода на физическом SmallTV пока недоступны.';
});
$('#enrollment-form').addEventListener('submit', event => {
  event.preventDefault();
  const name = $('#enrollment-name').value.trim();
  if (!name || !state.session || enrollment.code) return;
  enrollmentAction(async () => {
    $('#enrollment-result').textContent = '';
    if ($('#enrollment-mode').value === 'preview') {
      const device = await enrollment.createPreview(name, state.session.csrf_token);
      if (!device) return;
      // Keep successful creation separate from the potentially failing list refresh.
      clearEnrollment();
      await showAddedDevice(device.id);
      message('Локальное превью создано. Откройте его по кнопке «Открыть превью устройства». Физическое устройство не подключалось.');
    } else {
      await enrollment.issue(name, state.session.csrf_token);
      renderEnrollment();
      clearInterval(enrollmentTimer);
      enrollmentTimer = setInterval(renderEnrollment, 1000);
    }
  });
});
$('#enrollment-check').addEventListener('click', () => enrollmentAction(async () => {
  const result = await enrollment.check();
  if (!result) return;
  if (result.state === 'enrolled') {
    clearEnrollment();
    await showAddedDevice(result.device_id);
    message('Программный клиент зарегистрирован. Это не проверка совместимости физического SmallTV.');
  } else {
    $('#enrollment-result').textContent = result.state === 'pending' ? 'Клиент ещё не использовал код.'
      : result.state === 'expired' ? 'Срок действия истёк. Закройте мастер и получите новый код.'
        : 'Код больше не активен. Для старых кодов результат регистрации ищите в списке устройств.';
  }
}));
function closeEnrollment() {
  enrollmentAction(async () => {
    if (enrollment.code) {
      const cancelling = enrollment.cancel(state.session.csrf_token);
      renderEnrollment();
      await cancelling;
    }
    clearInterval(enrollmentTimer);
    $('#enrollment-secret').textContent = '';
    $('#enrollment-dialog').close();
    // Preserve an ambiguous preview request for retry when reopening the wizard.
    if (!enrollment.preview) clearEnrollment();
  });
}
$('#enrollment-close').addEventListener('click', closeEnrollment);
$('#enrollment-abandon').addEventListener('click', () => {
  if (!state.busy) {
    clearEnrollment();
    message('Код скрыт, но его отмена не запрашивалась. Он истечёт не позднее чем через 5 минут после выдачи.');
  }
});
$('#enrollment-dialog').addEventListener('cancel', event => { event.preventDefault(); if (!state.busy) closeEnrollment(); });
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
  $('#dirty-note').hidden = !settingsDirty();
});
$('#brightness').addEventListener('input', () => { $('#brightness-range').value = $('#brightness').value; });
$('#brightness-range').addEventListener('input', () => { $('#brightness').value = $('#brightness-range').value; });
$('#settings-form').addEventListener('submit', event => {
  event.preventDefault();
  if (!state.settings || !$('#settings-form').reportValidity()) return;
  run(async () => {
    $('#save-result').textContent = '';
    const changes = changesFrom(state.settings, { ...draft(), brightness: Number($('#brightness').value) });
    if (!Object.keys(changes).length) { $('#save-result').textContent = 'Изменений нет.'; return; }
    state.settings = await api.save(state.selected, changes, state.session.csrf_token);
    renderSettings();
    $('#save-result').textContent = 'Настройки сохранены на сервере.';
  }, 'save');
});

// Check expiry while the page is active; never silently replay a mutation.
setInterval(() => {
  if (!state.session || document.hidden) return;
  renderPresence();
  if (!state.busy && !$('#enrollment-dialog').open && state.presence && state.presence.clock() >= state.presence.nextCheck) {
    void refreshDevicePresence();
  }
}, 1000);
setInterval(() => {
  if (!state.session || state.busy || document.hidden || $('#enrollment-dialog').open || document.activeElement?.closest('form')) return;
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
