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

ROOT=Path(__file__).resolve().parents[1]

def test_codex_all_styles_palettes_and_states_fit(tmp_path):
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
    renderer=(ROOT/'web/simulator/codex-screen.mjs').read_text(encoding='utf-8').replace('export ','')
    script=r'''
const failures=[],samples=[];
for(const state of ['WORKING','IDLE','DONE','ERROR','INTERRUPTED','UNKNOWN','OFFLINE']) for(const style of ['classic','minimal','terminal','orbit']) for(const theme of ['red','blue','purple','light']) {
 const cell=document.createElement('article');cell.innerHTML=DISPLAY;
 document.querySelector('main').append(cell);
 const screen=cell.querySelector('.display');screen.dataset.screen='codex';screen.dataset.deviceStyle=style;screen.dataset.theme=theme;
 cell.querySelector('#clock-content').hidden=true;cell.querySelector('#codex-content').hidden=false;
 const presence=state==='UNKNOWN'?'STALE':state==='OFFLINE'?'OFFLINE':'ONLINE';
 const reported=['UNKNOWN','OFFLINE'].includes(state)?'WORKING':state;
 const data={device_id:'tv',server_time:1000,stale_after_seconds:30,offline_after_seconds:90,agent_count:1,agent_name:'Windows — очень длинное имя устройства',last_seen_at:presence==='ONLINE'?1000:presence==='STALE'?950:900,presence,state,reason:null,run:state==='IDLE'?null:{run_id:'run',last_reported_state:reported,started_at:900,updated_at:990,duration_seconds:['UNKNOWN','OFFLINE'].includes(state)?null:state==='WORKING'?100:90}};
 const model=new CodexScreen('tv');model.apply(data,0);renderCodex(cell,model.view(0),'UTC');
 const box=screen.getBoundingClientRect();let bottom=box.top;
 for(const key of ['header','.codex-top','.codex-hero','.codex-clock','.codex-meta','#codex-hint']) {
  const node=cell.querySelector(key),r=node.getBoundingClientRect();
  if(r.left<box.left-1||r.right>box.right+1||r.bottom>box.bottom-2)failures.push(`${style}/${theme}/${state}: bounds ${key}`);
  if(r.top<bottom-1)failures.push(`${style}/${theme}/${state}: overlap ${key}`);
  bottom=r.bottom;
 }
 const status=cell.querySelector('#codex-status');if(status.scrollWidth>status.clientWidth+1)failures.push(`${style}/${theme}/${state}: status clipped`);
 samples.push({style,theme,state});
}
document.querySelector('#report').textContent=JSON.stringify({count:samples.length,failures});
'''
    with tempfile.TemporaryDirectory(prefix='storm-codex-browser-',ignore_cleanup_errors=True) as directory:
        work=Path(directory)
        page=work/'codex.html'
        page.write_text('<!doctype html><meta charset="utf-8"><style>'+css+'body{padding:20px}main{max-width:none;display:grid;grid-template-columns:repeat(4,240px);gap:20px}</style><main></main><pre id="report"></pre><script>'+renderer+'\nconst DISPLAY='+json.dumps(display)+';\n'+script+'</script>',encoding='utf-8')
        screenshot=tmp_path/'codex-styles.png'
        result=subprocess.run([chrome,'--headless=new','--no-first-run','--disable-background-networking','--disable-component-update','--disable-extensions','--disable-gpu','--hide-scrollbars','--window-size=1080,1120','--virtual-time-budget=1500','--user-data-dir='+str(work/'profile'),'--screenshot='+str(screenshot),'--dump-dom',page.as_uri()],capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=90,**({'creationflags':subprocess.CREATE_NO_WINDOW} if os.name=='nt' else {}))
        assert result.returncode==0
        match=re.search(r'<pre id="report">([^<]+)</pre>',result.stdout)
        assert match,'Codex browser report missing'
        report=json.loads(html.unescape(match[1]))
        (tmp_path/'codex-report.json').write_text(json.dumps(report,ensure_ascii=False),encoding='utf-8')
        assert report['count']==112
        assert not report['failures'],'\n'.join(report['failures'])
