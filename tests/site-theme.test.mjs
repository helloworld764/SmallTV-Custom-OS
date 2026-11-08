import test from 'node:test';
import assert from 'node:assert/strict';
import { initSiteTheme, SITE_THEME_KEY } from '../web/shared/site-theme.mjs';

class Button extends EventTarget {
  hidden = true;
  attributes = new Map();
  setAttribute(key, value) { this.attributes.set(key, value); }
  click() { this.dispatchEvent(new Event('click')); }
}

function setup(storage) {
  const root = { dataset: {} }, button = new Button(), eventTarget = new EventTarget();
  const dispose = initSiteTheme({ root, button, storage, eventTarget });
  return { root, button, eventTarget, dispose };
}

test('site preference restores across pages and toggles independently of server settings', () => {
  const values = new Map([[SITE_THEME_KEY, 'light']]);
  const storage = { getItem: key => values.get(key), setItem: (key, value) => values.set(key, value) };
  const page = setup(storage);
  assert.equal(page.root.dataset.siteTheme, 'light');
  assert.equal(page.button.hidden, false);
  assert.equal(page.button.attributes.get('aria-pressed'), 'true');
  assert.match(page.button.title, /тёмную тему сайта/);
  page.button.click();
  assert.equal(page.root.dataset.siteTheme, 'dark');
  assert.equal(values.get(SITE_THEME_KEY), 'dark');
  assert.equal(setup(storage).root.dataset.siteTheme, 'dark');
  assert.deepEqual([...values.keys()], [SITE_THEME_KEY]);
});

test('storage denial and corrupt saved preference cannot break the toggle', () => {
  for (const storage of [null,
    { getItem() { throw new Error('denied'); }, setItem() { throw new Error('denied'); } },
    { getItem() { return '<invalid-theme>'; }, setItem() {} }]) {
    const page = setup(storage);
    assert.equal(page.root.dataset.siteTheme, 'dark');
    page.button.click();
    assert.equal(page.root.dataset.siteTheme, 'light');
    assert.equal(page.button.attributes.get('aria-pressed'), 'true');
  }
});

test('tabs synchronize only the site preference and listeners can be removed', () => {
  const storage = { getItem() { return 'dark'; }, setItem() {} };
  const page = setup(storage);
  const notify = (key, newValue, storageArea = storage) => page.eventTarget.dispatchEvent(
    Object.assign(new Event('storage'), { key, newValue, storageArea }));
  notify('another-key', 'light');
  assert.equal(page.root.dataset.siteTheme, 'dark');
  notify(SITE_THEME_KEY, 'light', {});
  assert.equal(page.root.dataset.siteTheme, 'dark');
  notify(SITE_THEME_KEY, 'light');
  assert.equal(page.root.dataset.siteTheme, 'light');
  notify(null, null);
  assert.equal(page.root.dataset.siteTheme, 'dark');
  page.dispose();
  page.button.click();
  notify(SITE_THEME_KEY, 'light');
  assert.equal(page.root.dataset.siteTheme, 'dark');
});
