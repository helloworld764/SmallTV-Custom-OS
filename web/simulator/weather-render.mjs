const CLOUD = '<path d="M15 39h32a10 10 0 0 0 0-20 15 15 0 0 0-28-3 12 12 0 0 0-4 23Z"/>';
export function weatherArt(code) {
  let art;
  if (code <= 1) art = '<circle cx="32" cy="29" r="12"/><path d="M32 5v6m0 36v6M8 29h6m36 0h6M15 12l5 5m24 24 5 5M15 46l5-5m24-24 5-5"/>';
  else if (code === 2) art = '<circle cx="20" cy="17" r="9"/>'+CLOUD;
  else if ([71,73,75,77,85,86].includes(code)) art = CLOUD+'<path d="M20 44v12m-5-9 10 6m-10 0 10-6m14-3v12m-5-9 10 6m-10 0 10-6"/>';
  else if (code >= 95) art = CLOUD+'<path d="m33 39-9 12h9l-6 10 17-16H33l5-6"/>';
  else if ([45,48].includes(code)) art = CLOUD+'<path d="M9 46h46M15 53h34"/>';
  else if (code >= 51) art = CLOUD+'<path d="m21 45-4 10m17-10-4 10m17-10-4 10"/>';
  else art = CLOUD;
  return '<svg viewBox="0 0 64 64" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'+art+'</svg>';
}

function localDay(now, timezone) {
  const parts = Object.fromEntries(new Intl.DateTimeFormat('en', {timeZone:timezone,year:'numeric',month:'2-digit',day:'2-digit'}).formatToParts(now).map(p=>[p.type,p.value]));
  return `${parts.year}-${parts.month}-${parts.day}`;
}

export function renderWeather(root, data, now = new Date()) {
  const node = id => root.querySelector(id);
  const text = (id,value) => { node(id).textContent=value; };
  text('#wx-city',data?.city || 'Погода');
  let valid = false;
  try { valid = data?.available === true && data.today.date === localDay(now,data.timezone)
      && typeof data.tomorrow.condition === 'string' && typeof data.observed_at === 'string'
      && [data.temperature_c,data.feels_c,data.wind_ms,data.today.min_c,data.today.max_c,
        data.tomorrow.min_c,data.tomorrow.max_c,data.tomorrow.feels_min_c,data.tomorrow.feels_max_c,data.tomorrow.wind_max_ms].every(Number.isFinite); } catch { /* unavailable */ }
  node('#wx-details').hidden = !valid;
  node('#wx-error').hidden = valid;
  if (!valid) {
    text('#wx-error',data?.available ? 'Прогноз устарел. Обновляем…' : 'Погода временно недоступна');
    return;
  }
  const temp = value => `${Math.round(value)}°`;
  node('#wx-art').innerHTML=weatherArt(data.weather_code);
  text('#wx-temp',temp(data.temperature_c));
  text('#wx-condition',data.condition);
  text('#wx-feels',temp(data.feels_c));
  text('#wx-wind',`${data.wind_ms} м/с`);
  text('#wx-range',`${temp(data.today.min_c)} / ${temp(data.today.max_c)}`);
  text('#wx-sunrise',data.today.sunrise || '—');
  text('#wx-sunset',data.today.sunset || '—');
  text('#wx-tomorrow',`${data.tomorrow.condition} · ${temp(data.tomorrow.min_c)}…${temp(data.tomorrow.max_c)}. Ощущ. ${temp(data.tomorrow.feels_min_c)}…${temp(data.tomorrow.feels_max_c)} · ветер до ${data.tomorrow.wind_max_ms} м/с.`);
  text('#wx-freshness',data.stale ? 'Устаревшие данные · Open-Meteo' : `Обновлено ${data.observed_at.slice(11,16)} · Open-Meteo`);
}
