import { clockSettings, formatClock } from '../shared/clock.mjs';

// Apply settings when a response arrives as well as on each local clock tick.
export function renderClock(root, settings, now = new Date()) {
  const options = clockSettings(settings);
  const clock = formatClock(now, options);
  const display = root.querySelector('.display');
  display.dataset.clockStyle = options.clock_style;
  display.dataset.deviceStyle = options.device_style;
  display.dataset.clockLayout = options.clock_layout;
  display.dataset.clockSeconds = String(options.clock_seconds);
  root.querySelector('#clock').textContent = clock.time;
  const analog = root.querySelector('#analog-clock');
  if (analog) {
    analog.hidden = options.clock_layout !== 'analog';
    analog.setAttribute('aria-label', clock.time);
    for (const [hand,angle] of [['hour',(now.getHours()%12)*30+now.getMinutes()/2],['minute',now.getMinutes()*6],['second',now.getSeconds()*6]]) {
      const node = analog.querySelector('.'+hand);
      node.style.transform = `rotate(${angle}deg)`;
      node.hidden = hand === 'second' && !options.clock_seconds;
    }
  }
  const digits = root.querySelector('#clock');
  digits.setAttribute?.('data-seconds', '');
  digits.hidden = options.clock_layout === 'analog';
  if (options.clock_layout === 'stacked') {
    const [hours,minutes,seconds] = clock.time.split(':');
    digits.textContent = hours + '\n' + minutes;
    digits.setAttribute?.('data-seconds', seconds || '');
  }
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
