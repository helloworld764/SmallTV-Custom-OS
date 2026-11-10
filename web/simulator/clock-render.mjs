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
}
