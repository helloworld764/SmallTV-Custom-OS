import test from 'node:test';import assert from 'node:assert/strict';
import {JournalState,eventText,timeRange} from '../web/panel/journal-state.mjs';
const event=(id='1')=>({id,kind:'device.renamed',occurred_at:1000,details:{}});
const page=(items,cursor=null)=>({device_id:'tv',server_time:1001,items,next_cursor:cursor});
test('journal paging deduplicates stable IDs and resets on refresh',()=>{
 const model=new JournalState('tv');model.apply(page([event('3'),event('2')],'older'));model.apply(page([event('2'),event('1')]),true);
 assert.deepEqual(model.items.map(v=>v.id),['3','2','1']);assert.equal(model.pages,2);assert.equal(model.cursor,null);
 model.apply(page([event('4')]));assert.equal(model.items.length,1);assert.equal(model.pages,1);
});
test('wrong device, malformed entries and stuck cursor cannot poison history',()=>{
 const model=new JournalState('tv');model.apply(page([event()],'old'));
 for(const invalid of [{...page([]),device_id:'other'},page([{...event(),kind:'private.token'}]),page([{...event(),occurred_at:-1}]),page([{...event(),occurred_at:Number.MAX_SAFE_INTEGER}])])assert.throws(()=>model.apply(invalid));
 assert.throws(()=>model.apply(page([event()],'old'),true));assert.equal(model.items.length,1);
});
test('only known field labels and screen names appear in event text',()=>{
 assert.equal(eventText({kind:'settings.changed',details:{fields:['theme','<script>'],token:'sensitive'}}),'Настройки сохранены: тема');
 assert.equal(eventText({kind:'screen.changed',details:{screen:'weather'}}),'Выбран экран: Погода');
 assert.ok(!eventText({kind:'screen.changed',details:{screen:'sensitive'}}).includes('sensitive'));
});
test('time range is optional, inclusive and rejects invalid/reversed dates',()=>{
 assert.deepEqual(timeRange('',''),{since:null,until:null});assert.throws(()=>timeRange('invalid',''));
 assert.throws(()=>timeRange('2026-11-12T12:00','2026-11-11T12:00'));
 const value=timeRange('2026-11-12T12:00','2026-11-12T12:00');assert.equal(value.since,value.until);
});
