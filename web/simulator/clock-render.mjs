import { clockSettings, formatClock } from '../shared/clock.mjs';

// Apply settings when a response arrives as well as on each local clock tick.
export function renderClock(root, settings, now = new Date()) {
  const options = clockSettings(settings);
  const clock = formatClock(now, options);
  const display = root.querySelector('.display');
  display.dataset.clockStyle = options.clock_style;
  display.dataset.clockSeconds = String(options.clock_seconds);
  root.querySelector('#clock').textContent = clock.time;
  const period = root.querySelector('#clock-period');
  period.textContent = clock.period;
  period.hidden = !clock.period;
  const mode = root.querySelector('#clock-format-label');
  if (mode) mode.textContent = options.clock_format === '12h' ? '12H · AM/PM' : '24H';
  const scene = root.querySelector('#clock-content');
  if (scene) scene.style.setProperty('--day-progress', `${((now.getHours() * 60 + now.getMinutes()) / 1440 * 100).toFixed(2)}%`);
  const date = root.querySelector('#date');
  if (date) date.textContent = now.toLocaleDateString('ru-RU', { weekday: 'short', day: 'numeric', month: 'long' });
}
