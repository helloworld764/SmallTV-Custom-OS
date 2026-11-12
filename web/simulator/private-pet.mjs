export const imageId=value=>typeof value==='string'&&/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value);
export class PrivatePet {
  constructor(onReady,{fetcher=(...args)=>fetch(...args),urls=URL,makeImage=()=>new Image(),now=()=>Date.now()}={}){Object.assign(this,{onReady,fetcher,urls,makeImage,now,generation:0,id:null,image:null,url:null,retry:0});}
  clear(){this.generation++;this.controller?.abort();this.controller=null;if(this.url)this.urls.revokeObjectURL(this.url);this.id=null;this.url=null;this.image=null;this.retry=0;}
  async load(id){
    if(!imageId(id)){this.clear();return;}
    if(this.id===id&&(this.image||this.url||this.controller||this.now()<this.retry))return;
    if(this.id!==id)this.clear();this.id=id;const generation=this.generation,controller=new AbortController();this.controller=controller;
    const timer=setTimeout(()=>controller.abort(),5000);
    try{
      const response=await this.fetcher('/api/v1/pets/current/image/'+id,{credentials:'same-origin',cache:'no-store',signal:controller.signal});
      if(!response.ok||response.headers.get('content-type')?.split(';')[0]!=='image/png')throw new Error('Private image unavailable');
      const blob=await response.blob();if(!blob.size||blob.size>262144)throw new Error('Invalid image');
      if(generation!==this.generation)return;
      this.url=this.urls.createObjectURL(blob);const image=this.makeImage();
      image.onload=()=>{if(generation===this.generation){this.image=image;this.onReady();}};
      image.onerror=()=>{if(generation===this.generation){this.urls.revokeObjectURL(this.url);this.url=null;this.image=null;this.retry=this.now()+10000;this.onReady();}};
      image.src=this.url;
    }catch{if(generation===this.generation)this.retry=this.now()+10000;}
    finally{clearTimeout(timer);if(this.controller===controller)this.controller=null;}
  }
}
export function drawPrivatePet(context,image,state,width,height){
  if(!image?.naturalWidth||!image?.naturalHeight)return false;
  const scale=Math.min((width-4)/image.naturalWidth,(height-4)/image.naturalHeight),w=image.naturalWidth*scale,h=image.naturalHeight*scale;
  context.clearRect(0,0,width,height);context.imageSmoothingEnabled=false;
  context.drawImage(image,(width-w)/2,(height-h)/2,w,h);
  const badge={happy:'♥',eating:'♡',playing:'♪',sleeping:'Zzz',waking:'☀',hungry_sad:'!',tired:'…'}[state];
  if(badge){context.font='bold 16px sans-serif';context.textAlign='right';context.fillStyle='#ffffff';context.strokeStyle='#080809';context.lineWidth=3;context.strokeText(badge,width-6,19);context.fillText(badge,width-6,19);}
  return true;
}
