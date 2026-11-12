"""End-to-end panel/iframe preview using an isolated app, DB and Chrome profile."""
import html,json,os,re,secrets,shutil,socket,subprocess,tempfile,threading,time
from pathlib import Path
import pytest
import uvicorn
from fastapi.responses import HTMLResponse
from backend.app.main import create_app
from backend.app.config import Settings
from backend.app.database import Database
from backend.app.security import create_account
from backend.app import weather

ROOT=Path(__file__).resolve().parents[1]

@pytest.mark.parametrize('width',[1280,390])
def test_advanced_panel_palette_preview_save_and_remote(tmp_path,monkeypatch,width):
    chrome=shutil.which('chrome') or shutil.which('chromium') or shutil.which('google-chrome')
    if not chrome and Path('C:/Program Files/Google/Chrome/Application/chrome.exe').is_file():chrome='C:/Program Files/Google/Chrome/Application/chrome.exe'
    if not chrome:pytest.skip('Chrome required')
    database=Database(tmp_path/'browser.sqlite3');database.initialize();password=secrets.token_urlsafe(24)
    owner=create_account(database,'preview-owner',password,'Preview')
    with database.transaction() as c:
        c.execute("INSERT INTO devices(id,owner_id,name) VALUES ('qa-tv',?,'Preview TV')",(owner,))
        c.execute("INSERT INTO device_settings(device_id) VALUES ('qa-tv')")
    monkeypatch.setattr(weather,'_json_get',lambda *args:{'current':{'temperature_2m':5,'weather_code':0,'time':'2026-11-11T12:00'}})
    app=create_app(Settings(_env_file=None,db_path=database.path))
    script=r'''
const errors=[];window.addEventListener('error',e=>errors.push(e.message));window.addEventListener('unhandledrejection',e=>errors.push(String(e.reason)));
const pause=ms=>new Promise(r=>setTimeout(r,ms));
let step='login';const wait=async fn=>{for(let i=0;i<400;i++){if(fn())return;await pause(50);}const doc=document.querySelector('iframe')?.contentDocument;throw new Error('QA wait timeout: '+step+'; '+document.querySelector('.wallpaper-editor [role="status"]')?.textContent+'; '+document.querySelector('#save-result')?.textContent+'; frame '+doc?.querySelector('#preview-status')?.textContent+'; '+doc?.querySelector('#draft-label')?.textContent+'; '+doc?.querySelector('.display')?.outerHTML.slice(0,800));};
const input=(selector,value)=>{const n=document.querySelector(selector);if(!n)throw new Error('Missing '+selector);if(n.type==='checkbox')n.checked=value;else n.value=value;n.dispatchEvent(new Event('input',{bubbles:true}));};
const check=(value,label)=>{if(!value)throw new Error(label);};
(async()=>{
 await wait(()=>document.querySelector('#login-view')&&!document.querySelector('#login-view').hidden&&!document.querySelector('#login-fields').disabled);
 input('#username','preview-owner');input('#password',PASSWORD);document.querySelector('#login-form').requestSubmit();
 await wait(()=>document.querySelector('#settings-form')&&!document.querySelector('#settings-form').hidden);
 check(document.querySelector('#dirty-note').hidden,'Default options must not dirty form');
 await wait(()=>document.querySelector('.device-draft-preview').contentDocument?.querySelector('#screen-content')?.style.opacity==='0.7');
 input('[data-hex="text"]','#080809');input('[data-advanced="custom_colors"]',true);
 check(document.querySelector('#advanced-editor [role="status"]').textContent.includes('Низкий контраст'),'Contrast warning missing');
 input('[data-hex="text"]','#ffffff');
 const weatherRow=document.querySelector('.screen-order-row[data-screen="weather"]');
 weatherRow.querySelector('button').click();check(weatherRow.parentElement.firstElementChild===weatherRow,'Screen order up failed');
 weatherRow.querySelectorAll('button')[1].click();
 input('[data-advanced="custom_colors"]',true);input('[data-hex="accent"]','#38d9aa');
 const frame=document.querySelector('.device-draft-preview');
 await wait(()=>frame.contentDocument?.querySelector('.display')?.style.getPropertyValue('--screen-accent')==='#38d9aa');
 const session=await(await fetch('/api/v1/auth/session')).json();
 let settings=await(await fetch('/api/v1/devices/qa-tv/settings')).json();check(!settings.advanced.custom_colors,'Draft persisted before save');
 const paletteName=document.querySelector('input[aria-label="Имя палитры"]');input('input[aria-label="Имя палитры"]','Лагуна');
 [...document.querySelectorAll('#advanced-editor button')].find(n=>n.textContent==='Сохранить мою палитру').click();
 input('[data-advanced="backdrop"]','stars');input('[data-advanced="pet_motion"]','off');
 input('[data-advanced="timezone"]','Europe/Moscow');input('[data-advanced="day_progress"]',false);
 document.querySelector('#settings-form').requestSubmit();
 await wait(()=>document.querySelector('#save-result').textContent.includes('сохранены'));
 settings=await(await fetch('/api/v1/devices/qa-tv/settings')).json();
 check(settings.advanced.accent==='#38d9aa'&&settings.advanced.palettes[0].name==='Лагуна','Palette not saved');
 check(settings.advanced.backdrop==='stars'&&settings.advanced.pet_motion==='off','Advanced settings missing');
 document.querySelector('[data-control="next"]').click();
 await wait(()=>document.querySelector('#web-remote-status').textContent.includes('Команда сохранена'));
 settings=await(await fetch('/api/v1/devices/qa-tv/settings')).json();check(settings.current_screen==='weather','Web remote not applied');
 // Upload a synthetic local image: no original file or user data is used.
 step='wallpaper upload';
 const canvas=document.createElement('canvas');canvas.width=400;canvas.height=300;
 const ctx=canvas.getContext('2d');ctx.fillStyle='#283d77';ctx.fillRect(0,0,400,300);ctx.fillStyle='#56c6ff';ctx.fillRect(120,80,100,100);
 const blob=await new Promise(resolve=>canvas.toBlob(resolve,'image/png'));
 const transfer=new DataTransfer();transfer.items.add(new File([blob],'qa.png',{type:'image/png'}));
 document.querySelector('input[aria-label="Файл обоев"]').files=transfer.files;
 input('input[aria-label="Название обоев"]','Мой фон');
 const mediaButton=text=>[...document.querySelectorAll('.wallpaper-editor button')].find(n=>n.textContent===text);
 mediaButton('Загрузить обои').click();
 await wait(()=>document.querySelector('.wallpaper-editor [role="status"]').textContent.includes('Обои загружены'));
 step='wallpaper preview';
 await wait(()=>frame.contentDocument.querySelector('.display').style.getPropertyValue('--wallpaper').includes('blob:'));
 settings=await(await fetch('/api/v1/devices/qa-tv/settings')).json();check(settings.advanced.backdrop==='stars','Upload activated wallpaper before save');
 check(document.querySelector('#dirty-note').hidden===false,'Uploaded selection must be a draft');
 for(const style of ['classic','minimal','terminal','orbit']){
   input('#device-style',style);await pause(80);
   check(frame.contentDocument.querySelector('.display').dataset.backdrop==='wallpaper','Wallpaper missing for '+style);
   check(frame.contentWindow.getComputedStyle(frame.contentDocument.querySelector('.display')).backgroundImage.includes('blob:'),'Image not rendered for '+style);
 }
 step='wallpaper save';document.querySelector('#save-result').textContent='';document.querySelector('#settings-form').requestSubmit();
 await wait(()=>document.querySelector('#save-result').textContent.includes('сохранены'));
 settings=await(await fetch('/api/v1/devices/qa-tv/settings')).json();check(settings.advanced.backdrop==='wallpaper'&&settings.advanced.wallpaper_id,'Wallpaper reference not saved');
 await wait(()=>document.querySelector('select[aria-label="Мои обои"]').value===settings.advanced.wallpaper_id);
 step='active delete';window.confirm=()=>true;mediaButton('Удалить выбранные обои').click();
 await wait(()=>document.querySelector('.wallpaper-editor [role="status"]').textContent.includes('используются'));
 step='save other background';input('[data-advanced="backdrop"]','grid');document.querySelector('#save-result').textContent='';document.querySelector('#settings-form').requestSubmit();
 await wait(()=>document.querySelector('#save-result').textContent.includes('сохранены'));
 await wait(()=>document.querySelector('select[aria-label="Мои обои"]').value===settings.advanced.wallpaper_id);
 step='delete unused';mediaButton('Удалить выбранные обои').click();await wait(()=>document.querySelector('.wallpaper-editor [role="status"]').textContent.includes('Обои удалены'));
 check((await(await fetch('/api/v1/devices/qa-tv/wallpapers')).json()).length===0,'Delete did not remove wallpaper');
 step='custom pet upload';
 const custom=document.querySelector('#custom-pet-section');custom.open=true;
 const petTransfer=new DataTransfer();petTransfer.items.add(new File([blob],'pet.png',{type:'image/png'}));
 document.querySelector('#custom-pet-file').files=petTransfer.files;document.querySelector('#custom-pet-upload').click();
 await wait(()=>document.querySelector('#custom-pet-result').textContent.includes('Превью готово'));
 check((await fetch('/api/v1/pets/current')).status===404,'Draft created a pet prematurely');
 input('#custom-pet-name','Феникс');document.querySelector('#custom-pet-form').requestSubmit();
 await wait(()=>document.querySelector('#custom-pet-result').textContent.includes('образ и имя сохранены'));
 const ownPet=await(await fetch('/api/v1/pets/current')).json();check(ownPet.image_id&&ownPet.name==='Феникс','Custom pet not saved');
 await fetch('/api/v1/devices/qa-tv/control',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':session.csrf_token},body:JSON.stringify({request_id:crypto.randomUUID(),action:'screen',screen:'tamagotchi'})});
 step='custom pet simulator';
 await wait(()=>frame.contentDocument.querySelector('#pet-name').textContent==='Феникс');
 await wait(()=>frame.contentDocument.querySelector('#pet-art').getContext('2d').getImageData(120,90,1,1).data[3]>0);
 check(errors.length===0,'Browser errors: '+errors.join(';'));
 document.querySelectorAll('.advanced-group').forEach(n=>n.open=false);
 document.querySelector('.advanced-group').open=true;
 frame.src='about:blank';
 document.querySelector('#advanced-editor').scrollIntoView({block:'start'});
 await document.fonts.ready;
 check(document.documentElement.scrollWidth<=window.innerWidth+1,'Horizontal panel overflow');
 for(const n of document.querySelectorAll('#advanced-editor input')){n.setAttribute('value',n.value);if(n.type==='checkbox'){if(n.checked)n.setAttribute('checked','');else n.removeAttribute('checked');}}
 for(const n of document.querySelectorAll('#advanced-editor option'))n.toggleAttribute('selected',n.selected);
 document.querySelector('#qa-report').textContent=JSON.stringify({ok:true,errors,markup:document.querySelector('#advanced-editor').outerHTML,rect:document.querySelector('#advanced-editor').getBoundingClientRect().toJSON(),scrollY});
})().catch(error=>{document.querySelector('#qa-report').textContent=JSON.stringify({ok:false,error:String(error),errors});});
'''.replace('PASSWORD',json.dumps(password))
    media_requests=[]
    @app.middleware('http')
    async def instrument(request,call_next):
        if request.url.path=='/panel/':
            page=(ROOT/'web/panel/index.html').read_text(encoding='utf-8')
            return HTMLResponse(page.replace('</body>','<pre id="qa-report"></pre><script>'+script+'</script></body>'))
        response=await call_next(request)
        if '/wallpapers' in request.url.path:media_requests.append((request.method,request.url.path,response.status_code,response.headers.get('content-type')))
        return response
    sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    server=uvicorn.Server(uvicorn.Config(app,log_level='error',lifespan='on'))
    thread=threading.Thread(target=lambda:server.run(sockets=[sock]),daemon=True);thread.start()
    try:
        for _ in range(100):
            if server.started:break
            time.sleep(.05)
        assert server.started
        with tempfile.TemporaryDirectory(prefix='storm-panel-browser-',ignore_cleanup_errors=True) as work:
            result=subprocess.run([chrome,'--headless=new','--no-first-run','--disable-background-networking','--disable-component-update','--disable-extensions','--disable-features=PaintHolding','--run-all-compositor-stages-before-draw','--hide-scrollbars',f'--window-size={width},1000','--virtual-time-budget=40000','--user-data-dir='+str(Path(work)/'profile'),'--screenshot='+str(tmp_path/'advanced-panel.png'),'--dump-dom',f'http://127.0.0.1:{port}/panel/'],capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=90,**({'creationflags':subprocess.CREATE_NO_WINDOW} if os.name=='nt' else {}))
            match=re.search(r'<pre id="qa-report">([^<]+)</pre>',result.stdout)
            assert match,'Browser report missing'
            report=json.loads(html.unescape(match[1]));(tmp_path/'panel-report.json').write_text(json.dumps(report),encoding='utf-8');assert report.get('ok'),(report,media_requests)
            # Static component image avoids Chrome's blank CLI capture after a live-page scroll.
            css=(ROOT/'web/panel/style.css').read_text(encoding='utf-8')+(ROOT/'web/shared/site-theme.css').read_text(encoding='utf-8')
            page=Path(work)/'component.html';page.write_text('<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><style>'+css+f'body{{padding:16px;width:{min(width,760)}px;margin:0}}</style>'+report['markup'],encoding='utf-8')
            shot=subprocess.run([chrome,'--headless=new','--no-first-run','--disable-background-networking','--disable-extensions','--hide-scrollbars',f'--window-size={width},1000','--virtual-time-budget=1000','--user-data-dir='+str(Path(work)/'component-profile'),'--screenshot='+str(tmp_path/'palette-editor.png'),page.as_uri()],capture_output=True,timeout=90,**({'creationflags':subprocess.CREATE_NO_WINDOW} if os.name=='nt' else {}))
            assert shot.returncode==0
    finally:
        server.should_exit=True;thread.join(10);sock.close()
