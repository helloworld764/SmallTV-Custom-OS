import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import { drawPetFrame, PET_SHEETS, petPresentation } from '../web/simulator/pet-display.mjs';

const pet = (changes = {}) => ({
  id: 'pet-1', kind: 'Pixel', name: 'Мур', lifecycle: 'ACTIVE',
  emotion: 'content', server_time: 1000, reaction: null, ...changes,
});

test('only the three approved catalogue sprites are configured for public preview', () => {
  assert.deepEqual(Object.keys(PET_SHEETS).sort(), ['Byte', 'Glitch', 'Pixel']);
  assert.equal(PET_SHEETS.Null, undefined);
  for (const sheet of Object.values(PET_SHEETS)) {
    assert.equal(sheet.columns, 3);
    assert.equal(sheet.rows, 3);
    assert.match(sheet.src, /^\.\/pets\/(Pixel|Byte|Glitch)-states\.png$/);
  }
});

test('Tamagotchi uses most of the 240 by 240 display area', () => {
  const html = readFileSync(new URL('../web/simulator/index.html', import.meta.url), 'utf8');
  const css = readFileSync(new URL('../web/simulator/style.css', import.meta.url), 'utf8');
  assert.match(html, /id="pet-art" width="240" height="180"/);
  assert.match(css, /data-screen=tamagotchi\] #screen-content \{ width: calc\(100% \+ 32px\); height: 208px;/);
  assert.match(css, /#pet-art \{[^}]*width: 240px; height: 180px;/);
  assert.match(css, /#pet-content \{[^}]*align-items: center; justify-content: space-between;/);
  assert.match(css, /#pet-labels \{[^}]*flex: 0 0 28px;[^}]*text-align: center;/);
});

test('accepted pet actions select their corresponding short-lived reaction frames', () => {
  const cases = [
    ['ADOPT', 1001, 'happy'], ['FEED', 1001, 'eating'], ['PLAY', 1001, 'playing'],
    ['SLEEP', 1001, 'sleeping'], ['WAKE', 1001, 'waking'], ['RETURN', 1001, 'happy'],
  ];
  for (const [kind, expiresAt, state] of cases) {
    const view = pet({ reaction: { id: `reaction-${kind}`, kind, expires_at: expiresAt } });
    assert.equal(petPresentation(view, view.server_time).state, state);
  }
  const expired = pet({ reaction: { id: 'old', kind: 'FEED', expires_at: 999 } });
  assert.equal(petPresentation(expired, expired.server_time).state, 'neutral');
});

test('emotion and missing lifecycle are shown without inventing a successful action', () => {
  assert.equal(petPresentation(pet({ emotion: 'hungry' }), 1000).state, 'hungry_sad');
  assert.equal(petPresentation(pet({ emotion: 'tired' }), 1000).state, 'tired');
  assert.equal(petPresentation(pet({ emotion: 'sleepy' }), 1000).state, 'sleeping');
  assert.equal(petPresentation(pet({ lifecycle: 'ABSENT', emotion: 'missing' }), 1000).label, 'Питомец пропал');
  assert.equal(petPresentation(pet({ lifecycle: 'RETURN_AVAILABLE' }), 1000).label, 'Можно начать заново');
  assert.equal(petPresentation(pet({ lifecycle: 'UNKNOWN' }), 1000).label, 'Состояние пока неизвестно');
  assert.equal(petPresentation(pet({ lifecycle: 'ACTIVE', reaction: { id: 'expired', kind: 'FEED', expires_at: 999 } }), 1000).label, 'Всё хорошо');
  assert.equal(petPresentation(null, 1000).available, false);
});

test('draw uses source cells from the approved 3 by 3 sheet without changing the PNG', () => {
  const calls = [];
  const context = {
    clearRect: (...args) => calls.push(['clearRect', ...args]),
    drawImage: (...args) => calls.push(['drawImage', ...args]),
    imageSmoothingEnabled: true,
  };
  const image = { naturalWidth: 900, naturalHeight: 600 };
  assert.equal(drawPetFrame(context, image, 'Pixel', 'playing', 120, 80), true);
  assert.equal(context.imageSmoothingEnabled, false);
  assert.deepEqual(calls[0], ['clearRect', 0, 0, 120, 80]);
  const [, , sx, sy, sw, sh, dx, dy, dw, dh] = calls[1];
  assert.ok(sx >= 0 && sy >= 200 && sy < 400);
  assert.ok(sw > 0 && sh > 0 && sw <= 300 && sh <= 200);
  assert.ok(Math.abs(dw / sw - dh / sh) < 1e-9, 'one proportional scale');
  assert.ok(Math.abs(dx * 2 + dw - 120) < 1e-9, 'centered horizontally');
  assert.ok(Math.abs(dy * 2 + dh - 80) < 1e-9, 'centered vertically');
  assert.ok(Math.abs(dw - 116) < 1e-9 || Math.abs(dh - 76) < 1e-9, 'fills area with a two-pixel safety gap');
  assert.equal(drawPetFrame(context, image, 'Null', 'neutral', 120, 80), false);
  assert.equal(drawPetFrame(context, image, 'Pixel', 'unknown-state', 120, 80), true);
  assert.equal(drawPetFrame(context, null, 'Pixel', 'neutral', 120, 80), false);
});
