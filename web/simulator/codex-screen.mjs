const STATES=['UNKNOWN','OFFLINE','IDLE','WORKING','DONE','ERROR','INTERRUPTED'];
const LABELS={UNKNOWN:'Нет данных',OFFLINE:'Мост не в сети',IDLE:'Ждёт запуска',WORKING:'В работе',DONE:'Ответ завершён',ERROR:'Ошибка запуска',INTERRUPTED:'Запуск прерван'};
export class CodexScreen {
 constructor(deviceId){this.deviceId=deviceId;this.reset();}
 reset(){this.data=null;this.received=0;this.failed=false;}
 apply(data,now){
  const integer=v=>Number.isSafeInteger(v)&&v>=0;
  if(!data||data.device_id!==this.deviceId||!STATES.includes(data.state)||!['ONLINE','STALE','OFFLINE','UNKNOWN'].includes(data.presence)
   ||!integer(data.server_time)||!integer(data.stale_after_seconds)||data.stale_after_seconds<1||!integer(data.offline_after_seconds)||data.offline_after_seconds<=data.stale_after_seconds
   ||!integer(data.agent_count)||(data.agent_name!==null&&typeof data.agent_name!=='string')||(data.last_seen_at!==null&&!integer(data.last_seen_at)))throw new Error('Invalid Codex snapshot');
  if(data.run&&(!['WORKING','DONE','ERROR','INTERRUPTED'].includes(data.run.last_reported_state)||!integer(data.run.started_at)||!integer(data.run.updated_at)
   ||data.run.started_at>data.run.updated_at||data.run.updated_at>data.server_time
   ||(data.run.duration_seconds!==null&&!integer(data.run.duration_seconds))))throw new Error('Invalid Codex run');
  if(data.presence==='ONLINE'&&(data.last_seen_at===null||data.last_seen_at>data.server_time))throw new Error('Unconfirmed agent presence');
  if(data.state==='IDLE'&&(data.presence!=='ONLINE'||data.run!==null))throw new Error('Unconfirmed idle state');
  if(data.state==='WORKING'&&(data.presence!=='ONLINE'||data.run?.last_reported_state!=='WORKING'||data.run.duration_seconds===null))throw new Error('Unconfirmed working state');
  if(['DONE','ERROR','INTERRUPTED'].includes(data.state)&&(data.presence!=='ONLINE'||data.run?.last_reported_state!==data.state||data.run.duration_seconds===null))throw new Error('Unconfirmed terminal state');
  this.data=data;this.received=now;this.failed=false;
 }
 fail(status){if([401,403,404].includes(status))this.reset();this.failed=true;}
 view(now){
  const d=this.data;
  if(!d)return {state:'UNKNOWN',label:'Нет данных',presence:'UNKNOWN',duration:null,lastEvent:null,agent:'Windows-мост не добавлен',hint:'Запустите Windows-мост Codex'};
  const elapsed=Math.max(0,(now-this.received)/1000),age=d.last_seen_at===null?Infinity:d.server_time-d.last_seen_at+elapsed;
  let presence=d.presence,state=d.state;
  if(this.failed){presence='UNKNOWN';state='UNKNOWN';}
  else if(presence==='ONLINE'||presence==='STALE'){
   if(age>=d.offline_after_seconds)presence='OFFLINE';else if(age>=d.stale_after_seconds)presence='STALE';
   if(presence!=='ONLINE')state=presence==='OFFLINE'?'OFFLINE':'UNKNOWN';
  }
  let duration=d.run?.duration_seconds??null;
  if(d.run?.last_reported_state==='WORKING')duration=state==='WORKING'?duration+Math.floor(elapsed):null;
  const historical=d.run&&state!==d.run.last_reported_state;
  return {state,presence,label:LABELS[state],duration,lastEvent:d.run?.updated_at??null,agent:d.agent_name||'Windows-мост не добавлен',
   hint:this.failed?'Не удалось обновить данные':d.reason==='no_agent'?'Добавьте Windows-мост Codex':historical?'Последний отчёт: '+LABELS[d.run.last_reported_state].toLowerCase():state==='DONE'?'Результат требует проверки':state==='WORKING'?'Статус из Windows-моста':state==='IDLE'?'Мост доступен · ждём команду':state==='ERROR'?'Подробности на Windows':state==='INTERRUPTED'?'Завершение не подтверждено':'Проверяем связь с мостом'};
 }
}
export function durationText(seconds){if(seconds===null)return '—';const h=Math.floor(seconds/3600),m=Math.floor(seconds/60)%60,s=seconds%60;if(h>=100)return `${Math.floor(h/24)}д ${h%24}ч`;return [h,m,s].map(v=>String(v).padStart(2,'0')).join(':');}
export function renderCodex(root,view,timezone='local'){
 const text=(id,value)=>{root.querySelector(id).textContent=value;};
 root.querySelector('#codex-content').dataset.state=view.state;
 text('#codex-status',view.label);text('#codex-duration',durationText(view.duration));text('#codex-hint',view.hint);text('#codex-agent',view.agent);
 text('#codex-link',({ONLINE:'Мост в сети',STALE:'Устарело',OFFLINE:'Нет связи',UNKNOWN:'Неизвестно'})[view.presence]);
 text('#codex-last',view.lastEvent===null?'—':new Date(view.lastEvent*1000).toLocaleTimeString('ru-RU',{hour12:false,...(timezone==='local'?{}:{timeZone:timezone})}));
}
