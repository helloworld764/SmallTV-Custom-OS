export const EVENT_LABELS=Object.freeze({'device.renamed':'Имя устройства изменено','settings.changed':'Настройки сохранены','screen.changed':'Выбран экран','connection.online':'Получен первый heartbeat','connection.stale':'Heartbeat устарел','connection.offline':'Нет heartbeat — связь недоступна','connection.restored':'Heartbeat восстановлен','connection.unknown':'Время сервера изменилось','connection.revoked':'Доступ устройства отозван','connection.reset':'Ключ устройства обновлён'});
const FIELDS={brightness:'яркость',theme:'тема',start_screen:'главный экран',clock_format:'формат часов',clock_seconds:'секунды',clock_style:'стиль часов',device_style:'стиль устройства',clock_layout:'вид часов',advanced:'расширенные параметры'};
const SCREENS={clock:'Часы',weather:'Погода',monitor:'Мониторинг',codex:'Codex',null:'Tamagotchi',tamagotchi:'Tamagotchi',markets:'Курсы'};
export function eventText(event){
  let text=EVENT_LABELS[event.kind]||'Событие устройства';const details=event.details||{};
  if(event.kind==='screen.changed'&&Object.hasOwn(SCREENS,details.screen))text+=': '+SCREENS[details.screen];
  if(event.kind==='settings.changed'&&Array.isArray(details.fields)){
    const fields=Object.keys(FIELDS).filter(key=>details.fields.includes(key)).map(key=>FIELDS[key]);
    if(fields.length)text+=': '+fields.join(', ');
    if(Number.isInteger(details.brightness)&&details.brightness>=0&&details.brightness<=100)text+=` (${details.brightness}%)`;
  }
  return text;
}
export function timeRange(since,until){
  const convert=value=>{if(!value)return null;const ms=new Date(value).getTime();if(!Number.isFinite(ms)||ms<0)throw new Error('Проверьте дату.');return Math.floor(ms/1000);};
  const from=convert(since),to=convert(until);if(from!==null&&to!==null&&from>to)throw new Error('Начало периода позже окончания.');return {since:from,until:to};
}
export class JournalState {
  constructor(id){this.id=id;this.items=[];this.cursor=null;this.serverTime=null;this.pages=0;}
  apply(page,append=false){
    if(page?.device_id!==this.id||!Number.isSafeInteger(page.server_time)||page.server_time<0||page.server_time>253402300799||!Array.isArray(page.items)||page.items.length>100||(page.next_cursor!==null&&(typeof page.next_cursor!=='string'||!page.next_cursor.length||page.next_cursor.length>256)))throw new Error('Invalid journal response');
    const entries=page.items.map(item=>{
      if(typeof item.id!=='string'||!item.id.length||item.id.length>128||!Object.hasOwn(EVENT_LABELS,item.kind)||!Number.isSafeInteger(item.occurred_at)||item.occurred_at<0||item.occurred_at>253402300799||!item.details||typeof item.details!=='object'||Array.isArray(item.details))throw new Error('Invalid journal event');
      return {id:item.id,kind:item.kind,occurred_at:item.occurred_at,text:eventText(item)};
    });
    if(append&&page.next_cursor!==null&&page.next_cursor===this.cursor)throw new Error('Journal cursor did not advance');
    this.items=[...new Map([...(append?this.items:[]),...entries].map(item=>[item.id,item])).values()];
    this.cursor=page.next_cursor;this.serverTime=page.server_time;this.pages=append?this.pages+1:1;
  }
}
