"""Actual weather renderer in isolated Chrome; no account, API or browser profile."""
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

ROOT=Path(__file__).resolve().parents[1]

def test_weather_all_styles_palettes_and_conditions_fit(tmp_path):
    chrome=shutil.which('google-chrome') or shutil.which('chromium') or shutil.which('chrome')
    if not chrome and os.name=='nt':
        candidate=Path('C:/Program Files/Google/Chrome/Application/chrome.exe')
        if candidate.is_file():chrome=str(candidate)
    if not chrome:pytest.skip('Chrome/Chromium required')
    source=(ROOT/'web/simulator/index.html').read_text(encoding='utf-8')
    display=re.search(r'<section class="display"[\s\S]*?</section>',source).group()
    logo=base64.b64encode((ROOT/'web/simulator/storm-logo.png').read_bytes()).decode()
    display=display.replace('src="storm-logo.png"','src="data:image/png;base64,'+logo+'"')
    css=(ROOT/'web/simulator/style.css').read_text(encoding='utf-8')
    renderer=(ROOT/'web/simulator/weather-render.mjs').read_text(encoding='utf-8').replace('export ','')
    script=r'''
const failures=[],samples=[];
for(const code of [0,63,71,95]) for(const style of ['classic','minimal','terminal','orbit']) for(const theme of ['red','blue','purple','light']) {
 const cell=document.createElement('article');cell.innerHTML=DISPLAY;
 document.querySelector('main').append(cell);
 const screen=cell.querySelector('.display');screen.dataset.screen='weather';screen.dataset.deviceStyle=style;screen.dataset.theme=theme;
 cell.querySelector('#clock-content').hidden=true;cell.querySelector('#weather-content').hidden=false;
 const data={available:true,city:'Петропавловск-Камчатский',timezone:'Europe/Moscow',today:{date:'2026-11-11',min_c:-18,max_c:-3,sunrise:'07:12',sunset:'16:30'},tomorrow:{condition:'Сильный ледяной дождь',min_c:-20,max_c:-2,feels_min_c:-27,feels_max_c:-10,wind_max_ms:18.6},temperature_c:-8,feels_c:-15,wind_ms:8.5,weather_code:code,condition:'Сильный ледяной дождь',observed_at:'2026-11-11T12:15',stale:theme==='purple'};
 renderWeather(cell,data,new Date('2026-11-11T12:00:00Z'));
 const box=screen.getBoundingClientRect();let bottom=box.top;
 for(const key of ['header','.wx-city','.wx-hero','.wx-metrics','.wx-solar','.wx-next','#wx-freshness']) {
  const node=cell.querySelector(key),r=node.getBoundingClientRect();
  if(r.left<box.left-1||r.right>box.right+1||r.bottom>box.bottom-3)failures.push(`${style}/${theme}/${code}: bounds ${key}`);
  if(r.top<bottom-1)failures.push(`${style}/${theme}/${code}: overlap ${key}`);
  bottom=r.bottom;
 }
 samples.push({style,theme,code});
}
const cell=document.querySelector('article');
const original=cell.innerHTML;
renderWeather(cell,{available:false,city:'Казань'});
if(!cell.querySelector('#wx-details').hidden)failures.push('unavailable weather leaked old values');
renderWeather(cell,{available:true,today:{date:'2026-11-10'},timezone:'Europe/Moscow'} ,new Date('2026-11-11T12:00:00Z'));
if(!cell.querySelector('#wx-details').hidden)failures.push('old calendar date shown as today');
cell.innerHTML=original;
document.querySelector('#report').textContent=JSON.stringify({count:samples.length,failures});
'''
    with tempfile.TemporaryDirectory(prefix='storm-weather-browser-',ignore_cleanup_errors=True) as directory:
        work=Path(directory)
        page=work/'weather.html'
        page.write_text('<!doctype html><meta charset="utf-8"><style>'+css+'body{padding:20px}main{max-width:none;display:grid;grid-template-columns:repeat(4,240px);gap:20px}</style><main></main><pre id="report"></pre><script>'+renderer+'\nconst DISPLAY='+json.dumps(display)+';\n'+script+'</script>',encoding='utf-8')
        screenshot=tmp_path/'weather-styles.png'
        result=capture(chrome,work,page,screenshot,'#report',1080,1120)
        assert result.returncode==0
        match=re.search(r'<pre id="report">([^<]+)</pre>',result.stdout)
        assert match,'Weather browser report missing'
        report=json.loads(html.unescape(match[1]))
        (tmp_path/'weather-report.json').write_text(json.dumps(report,ensure_ascii=False),encoding='utf-8')
        assert report['count']==64
        assert not report['failures'],'\n'.join(report['failures'])
