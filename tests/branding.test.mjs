import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

for (const [path, title] of [
  ['panel/index.html', 'Storm Control Panel'],
  ['panel/help.html', 'Storm Control Panel — первый вход'],
  ['simulator/index.html', 'Storm Simulator'],
]) {
  test(`${path} presents the official Storm title`, () => {
    const html = readFileSync(new URL(`../web/${path}`, import.meta.url), 'utf8');
    assert.ok(html.includes(`<title>${title}</title>`));
    assert.ok(html.includes('Storm OS'));
    assert.ok(!/SmallTV Custom OS|SMALLTV CUSTOM OS|<title>SmallTV/i.test(html));
  });
}

test('display and navigation use Storm names while URLs stay compatible', () => {
  const simulator = readFileSync(new URL('../web/simulator/index.html', import.meta.url), 'utf8');
  const panel = readFileSync(new URL('../web/panel/index.html', import.meta.url), 'utf8');
  assert.ok(simulator.includes('<header>Storm OS '));
  assert.ok(simulator.includes('<footer>Storm Simulator</footer>'));
  assert.ok(simulator.includes('href="/panel/">Storm Control Panel'));
  assert.ok(panel.includes('href="/simulator/"'));
  assert.ok(panel.includes('Storm Simulator'));
});
