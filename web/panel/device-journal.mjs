import {JournalState,timeRange} from './journal-state.mjs';
export class DeviceJournal {
  constructor(api,onDenied){
    this.api=api;this.onDenied=onDenied;this.generation=0;this.model=null;this.busy=false;this.loadedAt=0;
    const root=document.createElement('details');root.id='device-journal';root.className='device-journal';root.hidden=true;
    root.innerHTML=`<summary>Журнал событий</summary><p class="hint">История ведётся с обновления этой версии. Команды подтверждают сохранение на сервере, а не показ на SmallTV. Связь определяется по heartbeat, а не питанию устройства.</p>
      <form id="journal-filter" class="journal-filter"><label>Тип события<select id="journal-group"><option value="all">Все события</option><option value="name">Имя устройства</option><option value="settings">Настройки</option><option value="screen">Экраны</option><option value="connection">Связь</option></select></label>
      <label>С<input id="journal-since" type="datetime-local"></label><label>По<input id="journal-until" type="datetime-local"></label>
      <div class="journal-actions"><button class="secondary" type="submit">Показать</button><button id="journal-reset" class="text-button" type="button">Сбросить фильтры</button><button id="journal-refresh" class="text-button" type="button">Обновить</button></div></form>
      <p id="journal-status" class="hint" role="status" aria-live="polite"></p><ol id="journal-items" class="journal-items"></ol><button id="journal-more" type="button" class="secondary" hidden>Показать старые события</button>`;
    document.querySelector('#save-result').after(root);this.root=root;this.get=id=>root.querySelector('#journal-'+id);
    root.addEventListener('toggle',()=>{if(root.open&&this.model&&!this.model.pages)void this.load();});
    this.get('filter').onsubmit=event=>{event.preventDefault();void this.load();};
    this.get('reset').onclick=()=>{this.get('filter').reset();void this.load();};
    this.get('refresh').onclick=()=>void this.load();this.get('more').onclick=()=>void this.load(true);
    setInterval(()=>{if(this.root.open&&this.model?.pages===1&&!this.busy&&!document.hidden&&!this.get('filter').contains(document.activeElement)&&Date.now()-this.loadedAt>=15000)void this.load();},15000);
  }
  clear(){this.generation++;this.model=null;this.busy=false;this.loadedAt=0;this.filterKey=null;this.root.hidden=true;this.get('items').replaceChildren();this.get('status').textContent='';this.get('more').hidden=true;this.get('filter').reset();this.buttons(false);}
  show(id){this.clear();this.model=new JournalState(id);this.root.hidden=false;this.get('status').textContent='Откройте журнал, чтобы загрузить события.';if(this.root.open)void this.load();}
  refresh(){if(this.root.open&&this.model&&!this.busy)return this.load();}
  buttons(busy){for(const control of this.root.querySelectorAll('button,input,select'))control.disabled=busy;}
  async load(append=false){
    if(!this.model||this.busy)return;
    const model=this.model,generation=this.generation;let query,filterKey;
    try{const range=timeRange(this.get('since').value,this.get('until').value);query=new URLSearchParams({group:this.get('group').value,limit:'25'});for(const key of ['since','until'])if(range[key]!==null)query.set(key,String(range[key]));filterKey=query.toString();if(append&&filterKey!==this.filterKey)append=false;if(append&&model.cursor)query.set('cursor',model.cursor);}
    catch(error){this.get('status').textContent=error.message;return;}
    this.busy=true;this.buttons(true);this.get('status').textContent='Загружаем события…';
    try{
      const data=await this.api.request(`/api/v1/devices/${encodeURIComponent(model.id)}/journal?${query}`);
      if(generation!==this.generation||model!==this.model)return;
      model.apply(data,append);this.filterKey=filterKey;this.loadedAt=Date.now();this.render();
    }catch(error){
      if(generation!==this.generation||model!==this.model)return;
      if([401,403,404].includes(error.status)){this.clear();this.onDenied?.(error.status);return;}
      this.get('status').textContent=model.items.length?'Не удалось обновить журнал. Показаны ранее загруженные записи.':'Журнал временно недоступен. Нажмите «Обновить».';
    }finally{if(generation===this.generation&&model===this.model){this.busy=false;this.buttons(false);}}
  }
  render(){
    const model=this.model;if(!model)return;const list=this.get('items');list.replaceChildren();
    for(const event of model.items){const row=document.createElement('li'),time=document.createElement('time'),text=document.createElement('span');time.dateTime=new Date(event.occurred_at*1000).toISOString();time.textContent=new Date(event.occurred_at*1000).toLocaleString('ru-RU');text.textContent=event.text;row.append(time,text);list.append(row);}
    this.get('status').textContent=model.items.length?`Показано событий: ${model.items.length}. Загружено: ${new Date(this.loadedAt).toLocaleTimeString('ru-RU')}. Время событий — по часовому поясу браузера.`:'В выбранном периоде событий пока нет.';
    this.get('more').hidden=!model.cursor;
  }
}
