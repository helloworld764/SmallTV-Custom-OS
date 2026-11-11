import {displayOptions,DISPLAY_DEFAULTS,ZONES,contrast} from '../shared/display-options.mjs';
const LABELS={clock:'Часы',weather:'Погода',tamagotchi:'Питомец',markets:'Курсы',codex:'Codex'};
const COLORS=['#ff5264','#ff8a50','#ffb547','#ffe66d','#d4ed50','#85e06f','#39d9aa','#38d9d9','#56c6ff','#70a7ff','#6486ff','#7c5cfc','#ac78ff','#cc96fa','#f086cc','#ff83ab','#ffffff','#d3dae6','#a4b3c8','#6e8aa6','#366b83','#297267','#a56d4d','#e1c89c'];
export class AdvancedEditor {
  constructor(form,onInput) {
    this.form=form;this.onInput=onInput;this.palettes=[];
    const root=document.createElement('div');root.id='advanced-editor';this.root=root;
    const title=document.createElement('h3');title.id='advanced-settings-title';title.textContent='Расширенные настройки';root.append(title);
    root.setAttribute('role','region');root.setAttribute('aria-labelledby',title.id);
    form.querySelector('#dirty-note').before(root);
    const group=(title)=>{const d=document.createElement('details');d.className='advanced-group';d.innerHTML=`<summary>${title}</summary>`;root.append(d);return d;};
    const field=(parent,key,label,type='checkbox',values=null)=>{
      const wrap=document.createElement('label');wrap.textContent=label;
      let input=document.createElement(values?'select':'input');input.dataset.advanced=key;
      if(values)for(const [value,text] of Object.entries(values)){const o=document.createElement('option');o.value=value;o.textContent=text;input.append(o);}
      else {input.type=type;if(type==='number'){input.min=key==='rotate_seconds'?10:key==='notification_seconds'?2:0;input.max=key==='rotate_seconds'?600:key==='notification_seconds'?10:100;input.step=1;} }
      if(type==='checkbox'&&!values){wrap.className='advanced-checkbox';wrap.replaceChildren(input,document.createTextNode(label));}else wrap.append(input);parent.append(wrap);return input;
    };
    const palette=group('Свои цвета и фоны');palette.open=true;
    field(palette,'custom_colors','Использовать мою палитру');
    for(const [key,label] of [['background','Фон'],['text','Текст'],['accent','Акцент']]){
      const row=document.createElement('div');row.className='color-row';
      const color=field(row,key,label,'color');const hex=document.createElement('input');hex.dataset.hex=key;hex.pattern='#[0-9a-fA-F]{6}';hex.maxLength=7;hex.setAttribute('aria-label',label+' HEX');row.append(hex);palette.append(row);
      color.addEventListener('input',()=>{hex.value=color.value;this.get('custom_colors').checked=true;});
      hex.addEventListener('input',()=>{if(/^#[a-f0-9]{6}$/i.test(hex.value)){color.value=hex.value;this.get('custom_colors').checked=true;}});
    }
    const swatches=document.createElement('div');swatches.className='palette-swatches';palette.append(swatches);
    for(const color of COLORS){const b=document.createElement('button');b.type='button';b.style.background=color;b.title=color;b.setAttribute('aria-label','Акцент '+color);b.onclick=()=>{this.setColor('accent',color);this.get('custom_colors').checked=true;this.changed();};swatches.append(b);}
    this.warning=document.createElement('p');this.warning.className='hint';this.warning.setAttribute('role','status');palette.append(this.warning);
    this.saved=document.createElement('select');this.saved.setAttribute('aria-label','Сохранённые палитры');palette.append(this.saved);
    this.saved.onchange=()=>{const p=this.palettes[Number(this.saved.value)];if(p){for(const k of ['background','text','accent'])this.setColor(k,p[k]);this.get('custom_colors').checked=true;this.changed();}};
    const name=document.createElement('input');name.placeholder='Имя моей палитры';name.maxLength=24;name.setAttribute('aria-label','Имя палитры');palette.append(name);
    this.button(palette,'Сохранить мою палитру',()=>{const value=name.value.trim();if(!value){this.warning.textContent='Введите имя палитры.';return;}const o=this.read();const p={name:value,background:o.background,text:o.text,accent:o.accent};const index=this.palettes.findIndex(p=>p.name.toLowerCase()===value.toLowerCase());if(index>=0)this.palettes[index]=p;else if(this.palettes.length<8)this.palettes.push(p);else{this.warning.textContent='Можно сохранить до 8 палитр.';return;}this.savedList();this.changed();});
    this.button(palette,'Удалить выбранную палитру',()=>{const i=Number(this.saved.value);if(this.saved.value!==''&&this.palettes[i]){this.palettes.splice(i,1);this.savedList();this.changed();}});
    this.button(palette,'Вернуть стандартные цвета',()=>{for(const k of ['background','text','accent'])this.setColor(k,DISPLAY_DEFAULTS[k]);this.get('custom_colors').checked=false;this.changed();});
    field(palette,'backdrop','Готовый фон','select',{solid:'Однотонный',gradient:'Градиент',grid:'Сетка',stars:'Звёздное небо'});
    const screens=group('Порядок экранов и автопереключение');this.screenList=document.createElement('div');screens.append(this.screenList);
    field(screens,'auto_rotate','Автоматически переключать экраны');field(screens,'rotate_seconds','Интервал, секунд','number');
    const clock=group('Часовой пояс и дата');field(clock,'timezone','Часовой пояс','select',Object.fromEntries(ZONES.map(z=>[z,z==='local'?'Как в браузере':z])));field(clock,'date_format','Формат даты','select',{long:'11 ноября',short:'11.11',iso:'2026-11-11'});field(clock,'weekday','Показывать день недели');field(clock,'day_progress','Полоса прошедшей части суток');
    const night=group('Ночная яркость');field(night,'night_enabled','Снижать яркость по расписанию');field(night,'night_start','Начало','time');field(night,'night_end','Окончание','time');field(night,'night_brightness','Ночная яркость, %','number');
    const alerts=group('Уведомления на дисплее');field(alerts,'notification_seconds','Длительность, секунд','number');field(alerts,'quiet_enabled','Тихие часы');field(alerts,'quiet_start','Начало','time');field(alerts,'quiet_end','Окончание','time');const hint=document.createElement('p');hint.className='hint';hint.textContent='Тихие часы скрывают обычные уведомления. Ошибки и потеря связи остаются видимыми; Telegram настраивается отдельно.';alerts.append(hint);this.button(alerts,'Предпросмотр уведомления',()=>this.send('notice'));
    const pet=group('Питомец на экране');field(pet,'pet_phrases','Редкие облачка с фразами');field(pet,'pet_motion','Движение','select',{normal:'Обычное',reduced:'Уменьшенное',off:'Выключено'});
    const preview=group('Живое превью черновика');preview.open=true;this.frame=document.createElement('iframe');this.frame.title='Предпросмотр черновика настроек';this.frame.className='device-draft-preview';preview.append(this.frame);this.frame.onload=()=>this.send();
    root.addEventListener('input',()=>this.preview());
  }
  get(k){return this.root.querySelector(`[data-advanced="${k}"]`);}
  button(parent,label,fn){const b=document.createElement('button');b.type='button';b.className='secondary';b.textContent=label;b.onclick=()=>{try{fn();}catch{this.warning.textContent='Проверьте значения настроек перед этим действием.';}};parent.append(b);}
  setColor(k,v){this.get(k).value=v;this.root.querySelector(`[data-hex="${k}"]`).value=v;}
  changed(){this.root.dispatchEvent(new Event('input',{bubbles:true}));}
  savedList(){this.saved.replaceChildren(new Option('Выбрать сохранённую палитру',''));for(const [i,p] of this.palettes.entries())this.saved.append(new Option(p.name,String(i)));}
  render(settings,deviceId){
    this.settings=settings;this.base=displayOptions(settings.advanced||{});this.palettes=structuredClone(this.base.palettes);this.deviceId=deviceId;
    for(const [key,value] of Object.entries(this.base)){const n=this.get(key);if(n){if(n.type==='checkbox')n.checked=value;else n.value=value;}}
    for(const k of ['background','text','accent'])this.setColor(k,this.base[k]);this.savedList();this.screenList.replaceChildren();
    for(const screen of [...this.base.screens,...Object.keys(LABELS).filter(s=>!this.base.screens.includes(s))]){
      const row=document.createElement('div');row.className='screen-order-row';row.dataset.screen=screen;const label=document.createElement('label');const check=document.createElement('input');check.type='checkbox';check.checked=this.base.screens.includes(screen);label.append(check,document.createTextNode(LABELS[screen]));row.append(label);
      this.button(row,'↑',()=>{const prev=row.previousElementSibling;if(prev){prev.before(row);this.changed();}});this.button(row,'↓',()=>{const next=row.nextElementSibling;if(next){next.after(row);this.changed();}});this.screenList.append(row);
    }
    const src=`/simulator/?device=${encodeURIComponent(deviceId)}&embedded=1`;if(this.frame.getAttribute('src')!==src)this.frame.src=src;this.preview();
  }
  read(validate=true){const o={...this.base,palettes:this.palettes};for(const n of this.root.querySelectorAll('[data-advanced]'))o[n.dataset.advanced]=n.type==='checkbox'?n.checked:n.type==='number'?Number(n.value):n.value;o.screens=[...this.screenList.children].filter(n=>n.querySelector('input').checked).map(n=>n.dataset.screen);return validate?displayOptions(o):o;}
  send(kind='options'){try{const options=this.read();const basic={theme:document.querySelector('#theme').value,device_style:document.querySelector('#device-style').value,clock_format:document.querySelector('#clock-format').value,clock_layout:document.querySelector('#clock-style').value,clock_seconds:document.querySelector('#clock-seconds').checked,brightness:Number(document.querySelector('#brightness').value)};this.frame.contentWindow?.postMessage({type:'storm-draft-'+kind,deviceId:this.deviceId,options,basic,draft:JSON.stringify(options)!==JSON.stringify(this.base)||Object.entries(basic).some(([k,v])=>this.settings[k]!==v)},location.origin);}catch{/* Invalid draft is never applied. */}}
  preview(){try{const o=this.read();const ratio=contrast(o.background,o.text);this.warning.textContent=o.custom_colors&&ratio<4.5?`Низкий контраст текста: ${ratio.toFixed(1)}. Лучше выбрать более контрастные цвета.`:'Черновик виден в превью. Для применения сохраните настройки.';this.send();}catch{this.warning.textContent='Проверьте значения. Должен быть включён хотя бы один экран.';}}
}
