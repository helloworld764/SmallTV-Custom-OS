// Only server-issued IDs are allowed. Revoke private object URLs on access denial.
export class Wallpaper {
  constructor(element,{fetcher=(...args)=>fetch(...args),urls=URL,now=()=>Date.now()}={}){this.element=element;this.fetcher=fetcher;this.urls=urls;this.now=now;this.generation=0;this.key=null;this.url=null;this.retry=0;}
  clear(){this.generation++;this.controller?.abort();this.controller=null;if(this.url)this.urls.revokeObjectURL(this.url);this.url=null;this.key=null;this.retry=0;this.element.style.removeProperty('--wallpaper');}
  async sync(device,id,enabled){
    if(!enabled||!device||!id){if(this.key||this.url)this.clear();return;}
    if(!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(id)){this.clear();return;}
    const key=device+':'+id;if(key===this.key&&(this.url||this.controller||this.now()<this.retry))return;
    if(key!==this.key)this.clear();this.key=key;const generation=this.generation;
    const controller=new AbortController();this.controller=controller;const timer=setTimeout(()=>controller.abort(),6000);
    try{
      const response=await this.fetcher(`/api/v1/devices/${encodeURIComponent(device)}/wallpapers/${id}`,{credentials:'same-origin',cache:'no-store',signal:controller.signal});
      if(!response.ok)throw new Error('Wallpaper unavailable');
      if(response.headers.get('content-type')?.split(';')[0]!=='image/png')throw new Error('Invalid image type');
      const blob=await response.blob();if(blob.size>262144||blob.size===0)throw new Error('Invalid image size');
      if(generation!==this.generation)return;
      this.url=this.urls.createObjectURL(blob);this.element.style.setProperty('--wallpaper',`url("${this.url}")`);
    }catch{if(generation===this.generation)this.retry=this.now()+10000;}
    finally{clearTimeout(timer);if(this.controller===controller)this.controller=null;}
  }
}
