// Test-only CDP transport: bounded waits, owned loopback Chrome, no browser profile reuse.
import fs from 'node:fs/promises';
import path from 'node:path';
const [profile,url,screenshot,selector]=process.argv.slice(2);
const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms));
const deadline=Date.now()+40000;
let socket,sequence=0;
const pending=new Map();
function call(method,params={},sessionId){
  return new Promise((resolve,reject)=>{
    const id=++sequence,timer=setTimeout(()=>{pending.delete(id);reject(new Error(`CDP timeout: ${method} ${String(params.expression??'').slice(0,90)}`));},10000);
    pending.set(id,{resolve,reject,timer});socket.send(JSON.stringify({id,method,params,...(sessionId?{sessionId}:{})}));
  });
}
try {
  let endpoint;
  while(Date.now()<deadline){
    try {
      const [port,route]=(await fs.readFile(path.join(profile,'DevToolsActivePort'),'utf8')).trim().split(/\r?\n/);
      if(!/^\d+$/.test(port)||!route.startsWith('/devtools/browser/'))throw new Error('Invalid owned browser endpoint');
      endpoint=`ws://127.0.0.1:${port}${route}`;break;
    } catch {await pause(50);}
  }
  if(!endpoint)throw new Error('Owned Chrome did not start');
  socket=new WebSocket(endpoint);
  await new Promise((resolve,reject)=>{
    const timer=setTimeout(()=>reject(new Error('Chrome connection timeout')),5000);
    socket.addEventListener('open',()=>{clearTimeout(timer);resolve();},{once:true});
    socket.addEventListener('error',()=>{clearTimeout(timer);reject(new Error('Chrome connection failed'));},{once:true});
  });
  socket.addEventListener('message',event=>{
    const message=JSON.parse(event.data),entry=pending.get(message.id);
    if(!entry)return;
    clearTimeout(entry.timer);pending.delete(message.id);
    if(message.error)entry.reject(new Error(message.error.message));else entry.resolve(message.result);
  });
  const {targetId}=await call('Target.createTarget',{url:'about:blank'});
  const {sessionId}=await call('Target.attachToTarget',{targetId,flatten:true});
  await call('Page.enable',{},sessionId);
  await call('Page.navigate',{url},sessionId);
  let ready=false;
  while(Date.now()<deadline){
    const value=await call('Runtime.evaluate',{expression:`Boolean(document.querySelector(${JSON.stringify(selector)})?.textContent.trim())`,returnByValue:true},sessionId);
    if(value.result?.value){ready=true;break;}
    await pause(50);
  }
  if(!ready)throw new Error('Page did not produce its assertion report');
  await call('Runtime.evaluate',{expression:'document.fonts.ready',awaitPromise:true},sessionId);
  // Transfer only the assertion report, not hundreds of duplicate test scenes.
  const dom=await call('Runtime.evaluate',{expression:`document.querySelector(${JSON.stringify(selector)}).outerHTML`,returnByValue:true},sessionId);
  const shot=await call('Page.captureScreenshot',{format:'png',captureBeyondViewport:false},sessionId);
  await fs.writeFile(screenshot,Buffer.from(shot.data,'base64'));
  process.stdout.write(dom.result.value);
} catch(error){console.error(error.message);process.exitCode=1;}
finally {
  if(socket?.readyState===WebSocket.OPEN){try{await call('Browser.close');}catch{}socket.close();}
  for(const entry of pending.values())clearTimeout(entry.timer);
}
