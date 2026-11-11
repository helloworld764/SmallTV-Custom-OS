export const DISPLAY_DEFAULTS=Object.freeze({custom_colors:false,background:'#080809',text:'#f4f4f6',accent:'#ff5264',palettes:[],backdrop:'solid',screens:['clock','weather','tamagotchi','markets','codex'],auto_rotate:false,rotate_seconds:30,night_enabled:false,night_start:'22:00',night_end:'08:00',night_brightness:20,timezone:'local',date_format:'long',weekday:true,day_progress:true,notification_seconds:4,quiet_enabled:false,quiet_start:'23:00',quiet_end:'08:00',pet_phrases:true,pet_motion:'normal'});
export const ZONES=['local','UTC','Europe/Moscow','Europe/London','Europe/Berlin','Asia/Dubai','Asia/Tokyo','America/New_York'];
export function displayOptions(value={}) {
  const o={...structuredClone(DISPLAY_DEFAULTS),...value};
  const color=v=>typeof v==='string'&&/^#[0-9a-f]{6}$/i.test(v);
  if(![o.background,o.text,o.accent].every(color)||!ZONES.includes(o.timezone)||!['solid','gradient','grid','stars'].includes(o.backdrop)||!['long','short','iso'].includes(o.date_format)||!['normal','reduced','off'].includes(o.pet_motion))throw new Error('Invalid display options');
  for(const key of ['custom_colors','auto_rotate','night_enabled','weekday','day_progress','quiet_enabled','pet_phrases'])if(typeof o[key]!=='boolean')throw new Error('Invalid toggle');
  for(const [key,min,max] of [['rotate_seconds',10,600],['night_brightness',0,100],['notification_seconds',2,10]])if(!Number.isInteger(o[key])||o[key]<min||o[key]>max)throw new Error('Invalid range');
  for(const key of ['night_start','night_end','quiet_start','quiet_end'])if(!/^(?:[01][0-9]|2[0-3]):[0-5][0-9]$/.test(o[key]))throw new Error('Invalid time');
  if((o.night_enabled&&o.night_start===o.night_end)||(o.quiet_enabled&&o.quiet_start===o.quiet_end))throw new Error('Schedule endpoints must differ');
  if(!Array.isArray(o.screens)||!o.screens.length||o.screens.length>5||new Set(o.screens).size!==o.screens.length||o.screens.some(s=>!['clock','weather','tamagotchi','markets','codex'].includes(s)))throw new Error('Invalid screen order');
  if(!Array.isArray(o.palettes)||o.palettes.length>8||o.palettes.some(p=>typeof p.name!=='string'||!p.name.trim()||p.name.length>24||![p.background,p.text,p.accent].every(color)))throw new Error('Invalid palettes');
  return o;
}
export function clockParts(now,zone='local') {
  if(zone==='local')return {hour:now.getHours(),minute:now.getMinutes(),second:now.getSeconds()};
  const p=Object.fromEntries(new Intl.DateTimeFormat('en',{timeZone:zone,hourCycle:'h23',hour:'2-digit',minute:'2-digit',second:'2-digit'}).formatToParts(now).map(v=>[v.type,v.value]));
  return {hour:Number(p.hour),minute:Number(p.minute),second:Number(p.second)};
}
export function withinSchedule(now,zone,start,end) {
  const {hour,minute}=clockParts(now,zone),t=hour*60+minute;
  const mins=s=>Number(s.slice(0,2))*60+Number(s.slice(3));const a=mins(start),b=mins(end);
  return a===b?false:a<b?t>=a&&t<b:t>=a||t<b;
}
export function resolveScreen(settings,now=Date.now()) {
  const o=displayOptions(settings.advanced||{});let base=settings.current_screen||settings.start_screen;
  if(base==='null')base='tamagotchi';
  if(!o.screens.includes(base)&&!['monitor'].includes(base))base=o.screens[0];
  if(o.auto_rotate){const index=Math.max(0,o.screens.indexOf(base));const steps=Math.floor(Math.max(0,now/1000-(settings.screen_changed_at??now/1000))/o.rotate_seconds);return o.screens[(index+steps)%o.screens.length];}
  return base;
}
export function contrast(a,b) {
  const lum=h=>h.slice(1).match(/../g).map(v=>parseInt(v,16)/255).map(v=>v<=.04045?v/12.92:((v+.055)/1.055)**2.4).reduce((n,v,i)=>n+v*[.2126,.7152,.0722][i],0);
  const x=lum(a),y=lum(b);return (Math.max(x,y)+.05)/(Math.min(x,y)+.05);
}
