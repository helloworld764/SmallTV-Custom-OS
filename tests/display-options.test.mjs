import test from 'node:test';
import assert from 'node:assert/strict';
import {displayOptions,withinSchedule,resolveScreen,contrast,clockParts} from '../web/shared/display-options.mjs';
import {Notifications} from '../web/simulator/notifications.mjs';
import {renderClock} from '../web/simulator/clock-render.mjs';
import {applyOptions} from '../web/simulator/options-render.mjs';
test('night brightness changes content only and custom colors stay declarative',()=>{
 const props={};const display={dataset:{},style:{setProperty:(k,v)=>props[k]=v,removeProperty:k=>delete props[k]}};
 const content={style:{}};const root={querySelector:k=>k==='.display'?display:content};
 const settings={brightness:70,advanced:{custom_colors:true,background:'#102030',text:'#ffffff',accent:'#55ccaa',night_enabled:true,timezone:'UTC',night_start:'22:00',night_end:'08:00',night_brightness:10,pet_motion:'off'}};
 applyOptions(root,settings,new Date('2026-11-11T23:00:00Z'));assert.equal(content.style.opacity,.1);assert.equal(props['--screen-accent'],'#55ccaa');assert.equal(display.dataset.petMotion,'off');
 applyOptions(root,settings,new Date('2026-11-11T12:00:00Z'));assert.equal(content.style.opacity,.7);
});
test('cross-midnight schedules and timezones use city wall clock with DST',()=>{
 assert.equal(withinSchedule(new Date('2026-11-11T21:30:00Z'),'Europe/Moscow','22:00','08:00'),true);
 assert.equal(withinSchedule(new Date('2026-11-11T10:30:00Z'),'Europe/Moscow','22:00','08:00'),false);
 assert.equal(clockParts(new Date('2026-11-01T05:30:00Z'),'America/New_York').hour,1);
 assert.equal(clockParts(new Date('2026-11-01T06:30:00Z'),'America/New_York').hour,1);
});
test('rotation and hidden screens agree with server formula',()=>{
 const s={start_screen:'clock',current_screen:null,screen_changed_at:1000,advanced:{screens:['clock','weather'],auto_rotate:true,rotate_seconds:30}};
 assert.equal(resolveScreen(s,1030000),'weather');assert.equal(resolveScreen(s,1060000),'clock');
 s.advanced.auto_rotate=false;s.advanced.screens=['weather'];assert.equal(resolveScreen(s,1000000),'weather');
 assert.throws(()=>displayOptions({screens:[]}));assert.throws(()=>displayOptions({accent:'url(evil)'}));
 assert.equal(contrast('#000000','#ffffff'),21);
});
test('quiet hours suppress info without replay and retain critical errors',()=>{
 const n=new Notifications('tv');n.quiet=true;n.duration=8000;
 n.enqueue({id:'info',text:'Info',priority:100,expiresAt:20000},0);
 n.enqueue({id:'error',text:'Error',priority:300,expiresAt:20000},0);
 assert.equal(n.tick(0),'Error');assert.equal(n.tick(7000),'Error');assert.equal(n.tick(8000),null);
 n.quiet=false;n.enqueue({id:'info',text:'Info',priority:100,expiresAt:20000},9000);assert.equal(n.tick(9000),null);
});
test('clock timezone, ISO date and hidden day progress are independent',()=>{
 const nodes={'.display':{dataset:{}},'#clock':{},'#clock-period':{},'#date':{},'.clock-day-track':{}};
 renderClock({querySelector:k=>nodes[k]}, {advanced:{timezone:'Asia/Tokyo',date_format:'iso',weekday:false,day_progress:false}}, new Date('2026-11-11T18:05:07Z'));
 assert.equal(nodes['#clock'].textContent,'03:05:07');assert.equal(nodes['#date'].textContent,'2026-11-12');assert.equal(nodes['.clock-day-track'].hidden,true);
});
