import test from 'node:test';
import assert from 'node:assert/strict';
import {Wallpaper} from '../web/simulator/wallpaper.mjs';
import {displayOptions} from '../web/shared/display-options.mjs';
const id='12345678-1234-4123-8123-123456789012';
function fixture(fetcher){const values=new Map(),revoked=[];let count=0;const w=new Wallpaper({style:{setProperty:(k,v)=>values.set(k,v),removeProperty:k=>values.delete(k)}},{fetcher,urls:{createObjectURL:()=>`blob:test-${++count}`,revokeObjectURL:v=>revoked.push(v)},now:()=>1000});return {w,values,revoked};}
const response=()=>new Response(new Blob(['PNG'],{type:'image/png'}),{headers:{'Content-Type':'image/png'}});
test('wallpaper IDs validated, compatible defaults and references',()=>{
  assert.equal(displayOptions({}).wallpaper_id,null);
  assert.throws(()=>displayOptions({backdrop:'wallpaper'}));
  assert.throws(()=>displayOptions({wallpaper_id:'https://example.invalid'}));
  assert.equal(displayOptions({backdrop:'wallpaper',wallpaper_id:id}).wallpaper_id,id);
});
test('private wallpaper cached once and revoked on access denial',async()=>{
  let calls=0;const {w,values,revoked}=fixture(async(url,options)=>{calls++;assert.ok(url.endsWith('/wallpapers/'+id));assert.equal(options.credentials,'same-origin');return response();});
  await w.sync('tv',id,true);assert.equal(values.get('--wallpaper'),'url("blob:test-1")');
  await w.sync('tv',id,true);assert.equal(calls,1);
  await w.sync('tv',id,false);assert.equal(values.size,0);assert.deepEqual(revoked,['blob:test-1']);
});
test('late response cannot leak image after clear or device change',async()=>{
  let resolve;const pending=new Promise(r=>resolve=r);const {w,values}=fixture(()=>pending);
  const task=w.sync('tv',id,true);w.clear();resolve(response());await task;assert.equal(values.size,0);
});
test('invalid content and failed requests never become a background',async()=>{
  for(const result of [new Response('denied',{status:401}),new Response('<svg/>',{headers:{'Content-Type':'image/svg+xml'}}),new Response(new Blob([new Uint8Array(262145)]),{headers:{'Content-Type':'image/png'}})]){
    const {w,values}=fixture(async()=>result);await w.sync('tv',id,true);assert.equal(values.size,0);
  }
});
test('failed fetch uses bounded retry and invalid references are never requested',async()=>{
  let calls=0;const {w}=fixture(async()=>{calls++;throw new Error('offline');});
  await w.sync('tv',id,true);await w.sync('tv',id,true);assert.equal(calls,1);
  await w.sync('tv','../other',true);assert.equal(calls,1);
});
