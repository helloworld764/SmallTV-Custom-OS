// Pure preview state. Cached settings survive a network outage, never an access denial.
import { CLOCK_DEFAULTS, clockSettings } from '../shared/clock.mjs';
export const DEFAULT_SETTINGS = Object.freeze({ brightness: 70, theme: 'BlackArch Red', start_screen: 'clock', ...CLOCK_DEFAULTS });
export const THEMES = Object.freeze({
  'BlackArch Red': 'red', 'Dark Blue': 'blue', Purple: 'purple', Light: 'light',
});
export const SCREENS = Object.freeze({
  clock: 'Часы', weather: 'Погода', monitor: 'Мониторинг', codex: 'Codex', null: 'Null', tamagotchi: 'Tamagotchi',
});

export class PreviewSettings {
  constructor(deviceId = null) {
    this.deviceId = deviceId;
    this.settings = { ...DEFAULT_SETTINGS };
    this.state = deviceId ? 'loading' : 'standalone';
    this.updatedAt = null;
  }

  apply(settings, now) {
    if (settings.device_id !== this.deviceId) throw new Error('Unexpected preview device');
    this.settings = { ...settings, ...clockSettings(settings) };
    this.state = 'fresh';
    this.updatedAt = now;
  }

  fail(status = 0) {
    if ([401, 403, 404].includes(status)) {
      this.settings = { ...DEFAULT_SETTINGS };
      this.updatedAt = null;
      this.state = status === 401 ? 'signed-out' : 'unavailable';
    } else {
      this.state = 'stale';
    }
  }

  get theme() { return THEMES[this.settings.theme] || 'red'; }
  get screenTitle() { return SCREENS[this.settings.start_screen] || 'Неизвестный экран'; }
  get canDisplaySettings() { return this.state !== 'loading'; }
}
