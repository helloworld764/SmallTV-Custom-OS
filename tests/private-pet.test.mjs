import test from 'node:test';import assert from 'node:assert/strict';
import {PrivatePet,drawPrivatePet,imageId} from '../web/simulator/private-pet.mjs';
const id='12345678-1234-4123-8123-123456789012';
function fixture(fetcher){const revoked=[];let ready=0,image;const pet=new PrivatePet(()=>ready++,{fetcher,urls:{createObjectURL:()=> 'blob:private',revokeObjectURL:url=>revoked.push(url)},makeImage:()=>image={},now:()=>1000});return {pet,revoked,get ready(){return ready;},get image(){return image;}};}
const response=()=>new Response(new Blob(['PNG']),{headers:{'Content-Type':'image/png'}});
test('private image UUIDs only',()=>{assert.ok(imageId(id));for(const value of ['../secret','https://example.invalid','',null])assert.ok(!imageId(value));});
test('one private download, release on logout and no late onload',async()=>{
  let count=0;const f=fixture(async(url,init)=>{count++;assert.ok(url.endsWith(id));assert.equal(init.cache,'no-store');return response();});
  await f.pet.load(id);await f.pet.load(id);assert.equal(count,1);
  f.image.naturalWidth=100;f.image.naturalHeight=200;f.image.onload();assert.equal(f.ready,1);assert.equal(f.pet.image,f.image);
  f.pet.clear();f.image.onload();assert.equal(f.ready,1);assert.equal(f.pet.image,null);assert.deepEqual(f.revoked,['blob:private']);
});
test('late HTTP response never restores an image after access is lost',async()=>{
  let resolve;const f=fixture(()=>new Promise(r=>resolve=r));const pending=f.pet.load(id);f.pet.clear();resolve(response());await pending;assert.equal(f.pet.url,null);
});
test('unsupported content rejected and failures have retry delay',async()=>{
  let count=0;const f=fixture(async()=>{count++;return new Response('<svg/>',{headers:{'Content-Type':'image/svg+xml'}});});
  await f.pet.load(id);await f.pet.load(id);assert.equal(count,1);assert.equal(f.pet.url,null);
});
test('custom frame keeps aspect, uses full frame, and adds action badge',()=>{
  const calls=[];const c={clearRect(){},drawImage(...a){calls.push(a);},strokeText(...a){calls.push(a);},fillText(){}};
  const image={naturalWidth:100,naturalHeight:200};assert.ok(drawPrivatePet(c,image,'sleeping',240,180));
  assert.deepEqual(calls[0].slice(1),[76,2,88,176]);assert.equal(calls[1][0],'Zzz');
});
