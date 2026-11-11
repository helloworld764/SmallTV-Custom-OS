export const CLOCK_DEFAULTS = Object.freeze({ clock_format: '24h', clock_seconds: true, clock_style: 'classic' });
export const CLOCK_STYLES = Object.freeze({ classic: 'Классический', minimal: 'Минимализм', terminal: 'Терминал' });

export function clockSettings(value = {}) {
  const result = Object.fromEntries(Object.entries(CLOCK_DEFAULTS).map(([key, fallback]) => [key,
    Object.hasOwn(value, key) ? value[key] : fallback]));
  if (!['12h', '24h'].includes(result.clock_format) || typeof result.clock_seconds !== 'boolean'
      || !Object.hasOwn(CLOCK_STYLES, result.clock_style)) throw new Error('Invalid clock settings');
  result.device_style = Object.hasOwn(value, 'device_style') ? value.device_style : result.clock_style;
  result.clock_layout = Object.hasOwn(value, 'clock_layout') ? value.clock_layout : 'regular';
  if (!['classic','minimal','terminal','orbit'].includes(result.device_style) || !['regular','tall','stacked','analog'].includes(result.clock_layout)) throw new Error('Invalid appearance');
  return result;
}

export function formatClock(date, settings = {}) {
  const options = clockSettings(settings);
  const hour = date.getHours();
  const pad = value => String(value).padStart(2, '0');
  return {
    time: `${pad(options.clock_format === '12h' ? hour % 12 || 12 : hour)}:${pad(date.getMinutes())}`
      + (options.clock_seconds ? `:${pad(date.getSeconds())}` : ''),
    period: options.clock_format === '12h' ? hour < 12 ? 'AM' : 'PM' : '',
  };
}
