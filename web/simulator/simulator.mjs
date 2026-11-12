import {CodexScreen,renderCodex} from './codex-screen.mjs';
import {Wallpaper} from './wallpaper.mjs';
import {PrivatePet,drawPrivatePet,imageId} from './private-pet.mjs';
import {applyOptions} from './options-render.mjs';
import {displayOptions,withinSchedule} from '../shared/display-options.mjs';
import { Connection, checkHealth } from './connection.mjs';
import { CLOCK_STYLES } from '../shared/clock.mjs';
import { renderWeather } from './weather-render.mjs';
import { renderClock } from './clock-render.mjs';
import { ApiClient } from '../panel/api.mjs';
import { PreviewSettings, THEMES } from './preview.mjs';
import { Notifications, readNotifications } from './notifications.mjs';
import { drawPetFrame, petPresentation, PET_SHEETS } from './pet-display.mjs';

const connection = new Connection();
const wallpaper=new Wallpaper(document.querySelector('.display'));
window.addEventListener('pagehide',()=>wallpaper.clear());
const embedded=new URLSearchParams(location.search).get('embedded')==='1' && window.parent!==window;
let draftOptions=null,draftBasic={};
const liveSettings=()=>({...preview.settings,...draftBasic,brightness:preview.canDisplaySettings?(draftBasic.brightness??preview.settings.brightness):0,advanced:draftOptions||preview.settings.advanced||{}});
const deviceId = new URLSearchParams(window.location.search).get('device');
const preview = new PreviewSettings(deviceId);
const notifications = new Notifications(deviceId);
const codexScreen=new CodexScreen(deviceId);
let codexBusy=false,codexAttempt=-Infinity;
const api = new ApiClient({ timeout: 2500 });
const indicator = document.querySelector('#indicator');
const checked = document.querySelector('#checked');
const notice = document.querySelector('#notice');
const labels = {
  unknown: 'Проверка связи…', online: 'Backend доступен',
  stale: 'Данные устарели · проверяем', offline: 'Связь с backend потеряна',
};
const eventStatus = document.querySelector('#event-status');
const petCanvas = document.querySelector('#pet-art');
const petContext = petCanvas.getContext('2d');
const petImages = new Map();
const privatePet=new PrivatePet(()=>drawCurrentPet());
window.addEventListener('pagehide',()=>privatePet.clear());
let currentPet = null;
let marketAttempt = 0, marketBusy = false;
let weatherLastAttempt = 0;
let forecastAttempt = 0, forecastBusy = false;
let forecastData = null;
let petError = 'Питомец ещё не загружен.';
let petTimer = null;
const petScreenIsReady = () => preview.state === 'fresh' && preview.screen === 'tamagotchi';

function setText(id, value) {
  const element = document.querySelector(id);
  if (element.textContent !== value) element.textContent = value;
}

function schedulePetPoll(delay = 1000) {
  if (petTimer !== null || !petScreenIsReady()) return;
  petTimer = setTimeout(() => { petTimer = null; void pollPet(); }, delay);
}

async function pollPet() {
  if (!petScreenIsReady()) return;
  try {
    const pet = await api.currentPet();
    if (!pet || typeof pet.name !== 'string' || !PET_SHEETS[pet.kind]
        || (pet.image_id!=null&&!imageId(pet.image_id))
        || !['ACTIVE', 'ABSENT', 'RETURN_AVAILABLE', 'UNKNOWN'].includes(pet.lifecycle)
        || !Number.isSafeInteger(pet.server_time)) throw new Error('Invalid pet response');
    currentPet = pet;
    petError = '';
    setText('#pet-preview-status', 'Состояние питомца загружено из аккаунта текущей сессии.');
  } catch (error) {
    currentPet = null;
    privatePet.clear();
    petError = error.status === 404 ? 'Питомец ещё не создан.'
      : error.status === 401 ? 'Войдите в панель, чтобы увидеть питомца.'
        : 'Состояние питомца временно недоступно.';
    setText('#pet-preview-status', petError);
  }
  drawCurrentPet();
  schedulePetPoll(currentPet?.lifecycle === 'ACTIVE' ? 1000 : 5000);
}

function drawCurrentPet() {
  const showingPet = preview.screen === 'tamagotchi' && Boolean(deviceId)
    && preview.canDisplaySettings;
  document.querySelector('#pet-content').hidden = !showingPet;
  if (!showingPet) {privatePet.clear();return;}
  if (!currentPet) {
    privatePet.clear();
    petContext.clearRect(0, 0, petCanvas.width, petCanvas.height);
    setText('#pet-name', '');
    setText('#pet-caption', petError);
    return;
  }
  const presentation = petPresentation(currentPet, currentPet.server_time);
  setText('#pet-name', currentPet.name);
  setText('#pet-caption', presentation.label);
  const ariaLabel = `Питомец ${currentPet.name}: ${presentation.label}`;
  if (petCanvas.getAttribute('aria-label') !== ariaLabel) petCanvas.setAttribute('aria-label', ariaLabel);
  if(currentPet.image_id){
    void privatePet.load(currentPet.image_id);
    petContext.clearRect(0,0,petCanvas.width,petCanvas.height);
    if(privatePet.id===currentPet.image_id&&privatePet.image)drawPrivatePet(petContext,privatePet.image,presentation.state,petCanvas.width,petCanvas.height);
    else if(privatePet.retry>Date.now())setText('#pet-caption','Образ временно недоступен');
    return;
  }
  privatePet.clear();
  let image = petImages.get(currentPet.kind);
  if (!image) {
    const kind = currentPet.kind;
    image = new Image();
    petImages.set(kind, image);
    image.onload = () => drawCurrentPet();
    image.onerror = () => {
      petImages.delete(kind);
      if (currentPet?.kind !== kind) return;
      petError = 'Не удалось загрузить изображение питомца.';
      currentPet = null;
      drawCurrentPet();
    };
    image.src = PET_SHEETS[currentPet.kind].src;
  }
  if (image.complete && image.naturalWidth) {
    drawPetFrame(petContext, image, currentPet.kind, presentation.state,
      petCanvas.width, petCanvas.height);
  }
}

function renderPreview() {
  const settings = preview.settings;
  renderClock(document, liveSettings());
  const options=applyOptions(document,liveSettings());
  void wallpaper.sync(deviceId,options.wallpaper_id,options.backdrop==='wallpaper'&&['fresh','stale'].includes(preview.state));
  notifications.duration=options.notification_seconds*1000;
  notifications.quiet=options.quiet_enabled&&withinSchedule(new Date(),options.timezone,options.quiet_start,options.quiet_end);
  document.querySelector('.display').dataset.theme = THEMES[liveSettings().theme]||preview.theme;
  document.querySelector('.display').dataset.screen = preview.screen;
  const clock = preview.screen === 'clock';
  document.querySelector('#weather-content').hidden = preview.screen !== 'weather';
  if (preview.screen === 'weather') {
    if (preview.state === 'signed-out' || preview.state === 'unavailable') forecastData = null;
    renderWeather(document, forecastData && preview.state !== 'fresh' ? {...forecastData,stale:true} : forecastData);
    if (preview.state === 'fresh') void pollForecast();
  }
  document.querySelector('#clock-content').hidden = !clock;
  document.querySelector('#codex-content').hidden = preview.screen !== 'codex';
  if(preview.screen==='codex'){
    if(['signed-out','unavailable'].includes(preview.state))codexScreen.fail(401);
    renderCodex(document,codexScreen.view(performance.now()),options.timezone);
    if(preview.state==='fresh')void pollCodex();
  }
  document.querySelector('#placeholder').hidden = clock || ['tamagotchi','markets','weather','codex'].includes(preview.screen);
  document.querySelector('#markets-content').hidden = preview.screen !== 'markets';
  if (preview.screen === 'markets' && preview.state === 'fresh') void pollMarkets();
  document.querySelector('#screen-title').textContent = preview.screenTitle;
  document.querySelector('.codex-symbol').hidden = preview.screen !== 'codex';
  drawCurrentPet();
  const labels = {
    standalone: 'Автономное превью часов. Откройте превью из настроек устройства, чтобы читать их с сервера.',
    loading: 'Читаем сохранённые настройки устройства…',
    fresh: `Настройки с сервера: ${settings.theme}, яркость ${settings.brightness}%, экран «${preview.screenTitle}». Часы: ${CLOCK_STYLES[settings.clock_style] || CLOCK_STYLES.classic}, ${settings.clock_format === '12h' ? '12' : '24'} ч, ${settings.clock_seconds === false ? 'без секунд' : 'с секундами'}.`,
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
  if (connection.lastSuccess !== null) {
    checked.textContent = `Последняя успешная проверка: ${new Date(connection.lastSuccess).toLocaleTimeString('ru-RU')}`;
  }
}

async function pollCodex() {
  if(codexBusy||document.hidden||performance.now()-codexAttempt<3000||!deviceId)return;
  codexAttempt=performance.now();codexBusy=true;
  try{codexScreen.apply(await api.request(`/api/v1/devices/${encodeURIComponent(deviceId)}/codex`),performance.now());}
  catch(error){codexScreen.fail(error.status);}
  finally{codexBusy=false;}
  if(preview.screen==='codex')renderCodex(document,codexScreen.view(performance.now()),displayOptions(liveSettings().advanced).timezone);
}

async function pollMarkets() {
  if (marketBusy || Date.now() - marketAttempt < 60_000) return;
  marketAttempt = Date.now(); marketBusy = true;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 8000);
  try {
    const response = await fetch('/api/v1/markets', {signal:controller.signal, cache:'no-store'});
    if (!response.ok) throw new Error('Market unavailable');
    const data = await response.json();
    if (!Array.isArray(data.rows) || data.rows.length !== 5) throw new Error('Invalid rates');
    const container = document.querySelector('#market-rows'); container.replaceChildren();
    for (const row of data.rows) {
      const line = document.createElement('div'); line.className = 'market-row';
      const label = document.createElement('span'), value = document.createElement('strong');
      label.textContent = row.symbol;
      value.textContent = row.price === null ? '—' : Number(row.price).toLocaleString('ru-RU', {maximumFractionDigits:2}) + (row.unit === 'RUB' ? ' ₽' : ' $') + (row.stale ? ' *' : '');
      line.append(label,value);container.append(line);
    }
    document.querySelector('#market-status').textContent = data.rows.some(row => row.price === null) ? 'Часть курсов недоступна' : data.rows.some(row => row.stale) ? '* Устаревшие данные' : 'Крипто: USD · Валюты: RUB';
    const fiat = data.rows.find(row => row.symbol === 'USD');
    document.querySelector('.market-source').textContent = 'CoinGecko · ЦБ ' + (fiat.source_date || 'РФ');
  } catch {
    document.querySelector('#market-rows').replaceChildren();
    document.querySelector('#market-status').textContent = 'Курсы временно недоступны';
  } finally { clearTimeout(timer); marketBusy = false; }
}

async function pollForecast() {
  if (forecastBusy || Date.now() - forecastAttempt < 60_000) return;
  forecastAttempt = Date.now(); forecastBusy = true;
  const controller = new AbortController(); const timer = setTimeout(() => controller.abort(), 6500);
  try {
    const response = await fetch('/api/v1/weather/forecast', {signal:controller.signal,cache:'no-store'});
    if (!response.ok) throw new Error('Weather unavailable');
    forecastData = await response.json();
    renderWeather(document, forecastData);
  } catch {
    // Preserve only explicitly stale cached data; a lost request is never fresh.
    if (forecastData?.available) forecastData = {...forecastData,stale:true};
    renderWeather(document, forecastData);
  } finally { clearTimeout(timer); forecastBusy = false; }
}

async function pollWeather() {
  weatherLastAttempt = Date.now();
  const summary = document.querySelector('#weather-summary');
  const icon = document.querySelector('#weather-icon');
  if (preview.screen !== 'clock') return;
  try {
    const data = await api.weather();
    if (!data || typeof data.available !== 'boolean') throw new Error('Invalid weather response');
    if (!data.available) {
      icon.textContent = '\u00b7';
      summary.textContent = `${data.city || '\u041f\u043e\u0433\u043e\u0434\u0430'}: \u043d\u0435\u0442 \u0434\u0430\u043d\u043d\u044b\u0445`;
      return;
    }
    icon.textContent = data.icon;
    summary.textContent = `${data.city} \u00b7 ${data.temperature_c}\u00b0 \u00b7 ${data.condition}${data.stale ? ' \u00b7 \u0443\u0441\u0442\u0430\u0440\u0435\u043b\u043e' : ''}`;
  } catch {
    icon.textContent = '\u00b7';
    summary.textContent = '\u041f\u043e\u0433\u043e\u0434\u0430 \u0432\u0440\u0435\u043c\u0435\u043d\u043d\u043e \u043d\u0435\u0434\u043e\u0441\u0442\u0443\u043f\u043d\u0430';
  }
}

async function refreshPreviewSettings() {
  const wasPetReady = petScreenIsReady();
  if (await preview.refresh(() => api.settings(deviceId))) {
    renderPreview();
    if (!wasPetReady && petScreenIsReady()) schedulePetPoll(0);
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
        await refreshPreviewSettings();
        if (preview.screen === 'clock' && Date.now() - weatherLastAttempt >= 60_000) void pollWeather();
        const startedAt = performance.now();
        const page = await readNotifications(api, deviceId, notifications.cursor);
        notifications.apply(page, performance.now(), startedAt);
        eventStatus.textContent = 'Уведомления Codex подключены. DONE означает завершение ответа, а не проверку результата.';
      } catch (error) {
        if ([401, 403, 404].includes(error.status)) {
          preview.fail(error.status);
          notifications.reset();
          codexScreen.fail(error.status);
          notice.hidden = true;
          notice.textContent = '';
          eventStatus.textContent = 'Для уведомлений нужен доступ к выбранному устройству.';
        } else {
          preview.fail();
          eventStatus.textContent = 'Уведомления временно недоступны; повторим проверку.';
        }
      }
      if (petScreenIsReady()) {
        schedulePetPoll(0);
      } else {
        if (petTimer !== null) clearTimeout(petTimer);
        petTimer = null;
        currentPet = null;
        petError = preview.state === 'signed-out' ? 'Войдите в панель, чтобы увидеть питомца.'
          : 'Выберите экран Tamagotchi в настройках устройства.';
      }
    } else {
      preview.fail();
      codexScreen.fail();
      if (petTimer !== null) clearTimeout(petTimer);
      petTimer = null;
      currentPet = null;
      petError = 'Связь с backend потеряна.';
      eventStatus.textContent = 'Уведомления временно недоступны; повторим проверку.';
    }
    renderPreview();
  } else {
    setText('#pet-preview-status', 'Откройте превью из панели управления, чтобы загрузить питомца.');
    drawCurrentPet();
  }
  // Schedule only after completion: requests cannot overlap.
  setTimeout(poll, connection.delay);
}

function tick() {
  const now = new Date();
  const settings=liveSettings();
  const options=applyOptions(document,settings,now);
  renderClock(document,settings,now);
  notifications.duration=options.notification_seconds*1000;
  notifications.quiet=options.quiet_enabled && withinSchedule(now,options.timezone,options.quiet_start,options.quiet_end);
  if(preview.screen!==document.querySelector('.display').dataset.screen)renderPreview();
  const bubble=document.querySelector('#pet-bubble');
  const age=performance.now(); const showing=preview.screen==='tamagotchi'&&currentPet?.lifecycle==='ACTIVE'&&currentPet.emotion!=='sleepy'&&(!currentPet.reaction||currentPet.reaction.expires_at<=currentPet.server_time);
  bubble.hidden=!(options.pet_phrases&&showing&&age>=180000&&age%180000<4000);
  if(!bubble.hidden)bubble.textContent=['Ты справишься!','Рад тебя видеть','Сделаем маленький перерыв?','Шаг за шагом!'][Math.floor(age/180000)%4];
  if(preview.screen==='codex')renderCodex(document,codexScreen.view(performance.now()),options.timezone);
  connection.age(now.getTime());
  const message = notifications.tick(performance.now());
  notice.textContent = message ?? '';
  notice.hidden = message === null;
  render();
}

function renderNetwork() {
  const wifi = document.querySelector('#wifi');
  wifi.dataset.state = navigator.onLine ? 'online' : 'offline';
  wifi.setAttribute('aria-label', navigator.onLine ? 'Сеть браузера доступна; Wi-Fi устройства не проверен' : 'Браузер без сети');
}
window.addEventListener('online', renderNetwork);
window.addEventListener('offline', renderNetwork);
if(embedded)document.body.classList.add('embedded');
window.addEventListener('message',event=>{
  if(!embedded || event.origin!==location.origin || event.source!==window.parent || event.data?.deviceId!==deviceId)return;
  if(event.data.type==='storm-draft-options'){
    if(event.data.draft===false){draftOptions=null;draftBasic={};preview.draftAdvanced=null;preview.draftAnchor=null;document.querySelector('#draft-label').hidden=false;document.querySelector('#draft-label').textContent='Сохранённые настройки';renderPreview();return;}
    try{
      const basic=event.data.basic||{};
      if(!THEMES[basic.theme]||!['classic','minimal','terminal','orbit'].includes(basic.device_style)||!['regular','tall','stacked','analog'].includes(basic.clock_layout)||!['12h','24h'].includes(basic.clock_format)||typeof basic.clock_seconds!=='boolean'||!Number.isInteger(basic.brightness)||basic.brightness<0||basic.brightness>100)return;
      draftBasic=Object.fromEntries(['theme','device_style','clock_layout','clock_format','clock_seconds','brightness'].map(k=>[k,basic[k]]));
      draftOptions=displayOptions(event.data.options);preview.draftAdvanced=draftOptions;preview.draftAnchor=event.data.draft?Date.now()/1000:null;document.querySelector('#draft-label').hidden=false;document.querySelector('#draft-label').textContent=event.data.draft?'Черновик · не сохранён':'Сохранённые настройки';renderPreview();}catch{/* Invalid drafts never execute. */}
  }
  if(event.data.type==='storm-draft-notice')notifications.enqueue({id:'draft:'+performance.now(),text:'Пример уведомления',priority:400,expiresAt:performance.now()+10000},performance.now());
});
renderNetwork();
tick();
renderPreview();
setInterval(tick, 1000);
setInterval(() => {
  if (!deviceId || document.hidden || connection.state !== 'online' || preview.state !== 'fresh') return;
  void refreshPreviewSettings().catch(error => {
    preview.fail(error.status);
    if ([401,403,404].includes(error.status)) { notifications.reset(); currentPet = null; codexScreen.fail(error.status); }
    renderPreview();
  });
}, 1000);
setInterval(() => { if (deviceId && preview.canDisplaySettings && Date.now() - weatherLastAttempt >= 60_000) void pollWeather(); }, 60_000);
poll();
