"""Optional real-browser layout check; isolated Chrome profile, no live API or cookies."""

import base64
import html
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

import pytest
from browser_harness import capture


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('device_style', ['classic','minimal','terminal','orbit'])
def test_clock_scenes_fit_all_combinations_in_real_chrome(tmp_path, device_style):
    # Chrome uses its conventional OS-temp profile location, not the source checkout.
    temporary_root = Path(tempfile.gettempdir()).resolve()
    with tempfile.TemporaryDirectory(dir=temporary_root, prefix='storm-clock-browser-', ignore_cleanup_errors=True) as directory:
        work = Path(directory).resolve()
        assert work.parent == temporary_root and work.name.startswith('storm-clock-browser-')
        run_clock_layout(work, tmp_path, device_style)


def run_clock_layout(work, tmp_path, device_style):
    chrome = shutil.which('google-chrome') or shutil.which('chromium') or shutil.which('chrome')
    if not chrome and os.name == 'nt':
        candidate = Path('C:/Program Files/Google/Chrome/Application/chrome.exe')
        if candidate.is_file():
            chrome = str(candidate)
    if not chrome:
        pytest.skip('Chrome/Chromium is required for the optional headless layout check')
    source = (ROOT / 'web/simulator/index.html').read_text(encoding='utf-8')
    display = re.search(r'<section class="display"[\s\S]*?</section>', source).group()
    logo = base64.b64encode((ROOT / 'web/simulator/storm-logo.png').read_bytes()).decode()
    display = display.replace('src="storm-logo.png"', 'src="data:image/png;base64,' + logo + '"')
    css = (ROOT / 'web/simulator/style.css').read_text(encoding='utf-8')
    # Execute the production pure renderer without file:// module CORS or a test server.
    shared = (ROOT / 'web/shared/clock.mjs').read_text(encoding='utf-8').replace('export ', '')
    renderer = (ROOT / 'web/simulator/clock-render.mjs').read_text(encoding='utf-8')
    renderer = re.sub(r'^import .*?;\s*', '', renderer).replace('export ', '')
    script = r'''
const themes = ['red', 'blue', 'purple', 'light'];
const styles = [STYLE];
const cases = [];
for (const style of styles) for (const theme of themes) for (const layout of ['regular','tall','stacked','analog']) for (const format of ['12h','24h']) for (const seconds of [false,true]) cases.push({style,theme,layout,format,seconds});
const failures = [], fonts = {}, metrics = [];
for (const entry of cases) {
  const cell = document.createElement('article');
  cell.className = 'qa-cell'; cell.innerHTML = '<div class="case">' + DISPLAY + '</div>';
  const screen = cell.querySelector('.display');
  screen.dataset.screen = 'clock'; screen.dataset.theme = entry.theme;
  cell.querySelector('#placeholder').hidden = true; cell.querySelector('#pet-content').hidden = true;
  cell.querySelector('#indicator').dataset.state = 'online';
  cell.querySelector('#weather-icon').textContent = '☁';
  cell.querySelector('#weather-summary').textContent = 'Петропавловск-Камчатский · −18° · Небольшой снег';
  document.querySelector('.qa-grid').append(cell);
  renderClock(cell, {device_style:entry.style,clock_layout:entry.layout,clock_format:entry.format,clock_seconds:entry.seconds}, new Date(2026,8,28,17,42,8));
  const caption = document.createElement('div'); caption.className = 'qa-caption';
  caption.textContent = `${entry.style} / ${entry.theme} / ${entry.format} / ${entry.seconds ? 'sec' : 'no sec'}`;
  cell.append(caption);
}
document.fonts.ready.then(() => {
  document.querySelectorAll('.qa-cell').forEach((cell, index) => {
    const entry = cases[index], screen = cell.querySelector('.display'), outer = screen.getBoundingClientRect();
    const label = JSON.stringify(entry);
    if (outer.width !== 240 || outer.height !== 240) failures.push(label + ': screen size');
    const parts = ['header','.clock-topline','.terminal-chrome','.terminal-command','.clock-face','.clock-date-row','#weather','.clock-day-track','.terminal-foot'];
    let bottom = outer.top;
    for (const selector of parts) {
      const node = cell.querySelector(selector);
      if (!node.getClientRects().length) continue;
      const box = node.getBoundingClientRect();
      if (box.left < outer.left-1 || box.right > outer.right+1 || box.bottom > outer.bottom+1) failures.push(label + ': bounds ' + selector);
      if (box.top < bottom-1) failures.push(label + ': overlap ' + selector);
      bottom = box.bottom;
    }
    const clock = cell.querySelector('#clock');
    if (entry.layout === 'stacked') {
      const text = clock.firstChild;
      const hours = document.createRange(), minutes = document.createRange();
      hours.setStart(text,0); hours.setEnd(text,2);
      minutes.setStart(text,3); minutes.setEnd(text,5);
      const h = hours.getBoundingClientRect(), m = minutes.getBoundingClientRect();
      if (Math.abs((h.left+h.right)-(m.left+m.right)) > 1) failures.push(label + ': minutes not centered under hours');
      const seconds = getComputedStyle(clock,'::after');
      if (parseFloat(seconds.fontSize) >= parseFloat(getComputedStyle(clock).fontSize)/2) failures.push(label + ': seconds too large');
      if (clock.dataset.seconds !== (entry.seconds ? '08' : '')) failures.push(label + ': incorrect small seconds');
    }
    if (entry.layout !== 'analog' && clock.scrollWidth > clock.clientWidth+1) failures.push(label + ': time clipped');
    const clockBox = clock.getBoundingClientRect(), dateBox = cell.querySelector('#date').getBoundingClientRect();
    if (entry.layout !== 'analog' && clockBox.bottom > dateBox.top) failures.push(label + ': time/date overlap');
    const computed = getComputedStyle(clock);
    fonts[entry.style] = [computed.fontFamily,computed.fontSize,computed.fontWeight,computed.lineHeight].join(' / ');
    const chrome = getComputedStyle(cell.querySelector('.terminal-chrome')).display;
    if ((entry.style === 'terminal') !== (chrome !== 'none')) failures.push(label + ': wrong terminal decoration');
    metrics.push({entry,time:clock.textContent,clockHeight:clockBox.height,weatherBottom:cell.querySelector('#weather').getBoundingClientRect().bottom-outer.top});
  });
  document.querySelector('#qa-report').textContent = JSON.stringify({count:cases.length,failures,fonts,metrics});
});
'''
    script = script.replace('STYLE', json.dumps(device_style))
    script = shared + '\n' + renderer + '\nconst DISPLAY = ' + json.dumps(display) + ';\n' + script
    page = work / 'clocks.html'
    page.write_text('<!doctype html><meta charset="utf-8"><style>' + css + '''
body { padding:24px; } main { max-width:none; margin:0; }
.qa-grid { display:grid; grid-template-columns:repeat(4,278px); gap:14px; }
.qa-cell .case { margin:0; } .qa-caption { font:11px/24px system-ui; color:#b7b7c2; }
#qa-report { white-space:pre-wrap; }
</style><main><h1>Storm OS · Clock scenes</h1><div class="qa-grid"></div><pre id="qa-report"></pre></main><script>'''
                    + script + '</script>', encoding='utf-8')
    screenshot = tmp_path / 'clock-scenes.png'
    result = capture(chrome,work,page,screenshot,'#qa-report',1220,1120)
    assert result.returncode == 0, 'Isolated headless Chrome did not complete'
    match = re.search(r'<pre id="qa-report">([^<]+)</pre>', result.stdout)
    assert match, 'Browser did not produce a layout report'
    report = json.loads(html.unescape(match[1]))
    (tmp_path / 'layout-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    assert report['count'] == 64
    assert not report['failures'], '\n'.join(report['failures'])
    assert screenshot.is_file()
