// Browser-only site preference; separate from device settings and auth data.
export const SITE_THEME_KEY = 'storm.site-theme';

function browserStorage() {
  try { return globalThis.localStorage; } catch { return null; }
}

export function initSiteTheme({ root = document.documentElement,
  button = document.querySelector('[data-site-theme-toggle]'),
  storage = browserStorage(), eventTarget = globalThis.window } = {}) {
  if (!button) return () => {};
  function apply(value) {
    const theme = value === 'light' ? 'light' : 'dark';
    root.dataset.siteTheme = theme;
    button.textContent = theme === 'dark' ? '☀' : '☾';
    button.title = theme === 'dark' ? 'Включить светлую тему сайта' : 'Включить тёмную тему сайта';
    button.setAttribute('aria-label', button.title);
    button.setAttribute('aria-pressed', String(theme === 'light'));
    button.hidden = false;
  }
  let saved;
  try { saved = storage?.getItem(SITE_THEME_KEY); } catch { /* Private browsing may deny storage. */ }
  apply(saved);
  const toggle = () => {
    apply(root.dataset.siteTheme === 'dark' ? 'light' : 'dark');
    try { storage?.setItem(SITE_THEME_KEY, root.dataset.siteTheme); } catch { /* Toggle still works. */ }
  };
  const sync = event => {
    if (event.storageArea && storage && event.storageArea !== storage) return;
    if (event.key === SITE_THEME_KEY || event.key === null) apply(event.newValue);
  };
  button.addEventListener('click', toggle);
  eventTarget?.addEventListener('storage', sync);
  return () => {
    button.removeEventListener('click', toggle);
    eventTarget?.removeEventListener('storage', sync);
  };
}

if (typeof document !== 'undefined') initSiteTheme();
