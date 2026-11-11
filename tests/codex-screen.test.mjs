import test from 'node:test';import assert from 'node:assert/strict';
import {CodexScreen,durationText} from '../web/simulator/codex-screen.mjs';
const snapshot=()=>({device_id:'tv',server_time:1000,state:'WORKING',presence:'ONLINE',last_seen_at:1000,stale_after_seconds:30,offline_after_seconds:90,agent_count:1,agent_name:'Windows',reason:null,run:{run_id:'run',last_reported_state:'WORKING',started_at:990,updated_at:1000,duration_seconds:10}});
test('local age never turns lost working into done and API failures invalidate status',()=>{
 const s=new CodexScreen('tv');s.apply(snapshot(),0);
 assert.equal(s.view(5000).duration,15);assert.equal(s.view(30000).state,'UNKNOWN');assert.equal(s.view(90000).state,'OFFLINE');
 assert.equal(s.view(90000).duration,null);
 s.apply(snapshot(),100000);s.fail();assert.equal(s.view(100001).state,'UNKNOWN');
 s.fail(401);assert.equal(s.view(100002).agent,'Windows-мост не добавлен');
});
test('terminal history stays distinct from presence and duration does not keep increasing',()=>{
 const s=new CodexScreen('tv');const d=snapshot();d.state='DONE';d.run.last_reported_state='DONE';d.run.duration_seconds=8;
 s.apply(d,0);assert.equal(s.view(10000).duration,8);assert.equal(s.view(90000).state,'OFFLINE');assert.match(s.view(90000).hint,/Последний отчёт/);
 assert.equal(durationText(3661),'01:01:01');assert.equal(durationText(null),'—');
});
test('foreign and unconfirmed completion snapshots are rejected',()=>{
 const s=new CodexScreen('tv');const d=snapshot();d.device_id='other';assert.throws(()=>s.apply(d,0));
 d.device_id='tv';d.state='DONE';assert.throws(()=>s.apply(d,0));d.run.last_reported_state='DONE';d.presence='OFFLINE';assert.throws(()=>s.apply(d,0));
});
