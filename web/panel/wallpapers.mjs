// Private catalogue. Uploading never changes the saved device settings.
export class WallpaperEditor {
  constructor(parent,editor,session){
    this.editor=editor;this.session=session;this.generation=0;this.busy=false;
    const box=document.createElement('fieldset');box.className='wallpaper-editor';parent.append(box);
    const legend=document.createElement('legend');legend.textContent='Мои обои';box.append(legend);
    const hint=document.createElement('p');hint.className='hint';hint.textContent='До 5 изображений на устройство. PNG/JPEG до 2 МБ, до 4 млн пикселей. По центру вырезается квадрат 240×240. Фон приглушается для читаемости текста.';box.append(hint);
    this.file=document.createElement('input');this.file.type='file';this.file.accept='image/png,image/jpeg';this.file.setAttribute('aria-label','Файл обоев');box.append(this.file);
    this.label=document.createElement('input');this.label.placeholder='Название обоев';this.label.maxLength=40;this.label.setAttribute('aria-label','Название обоев');box.append(this.label);
    this.select=document.createElement('select');this.select.setAttribute('aria-label','Мои обои');box.append(this.select);
    this.select.onchange=()=>{editor.wallpaperId=this.select.value||null;if(this.select.value)editor.get('backdrop').value='wallpaper';else if(editor.get('backdrop').value==='wallpaper')editor.get('backdrop').value='solid';editor.changed();};
    const button=(text,fn)=>{const b=document.createElement('button');b.type='button';b.className='secondary';b.textContent=text;b.onclick=()=>void this.run(fn);box.append(b);return b;};
    this.upload=button('Загрузить обои',async(id,csrf)=>{
      const file=this.file.files[0];if(!file||!['image/png','image/jpeg'].includes(file.type)||file.size>2097152||!file.size)throw new Error('Выберите PNG/JPEG до 2 МБ.');
      const result=await this.request(id,'?label='+encodeURIComponent(this.label.value.trim()||'Мои обои'),{method:'POST',body:file,headers:{'Content-Type':file.type,'X-CSRF-Token':csrf}});
      if(id!==this.deviceId)return;
      await this.load(id,result.wallpaper.id);this.file.value='';this.status.textContent=result.duplicate?'Эти обои уже есть в каталоге.':'Обои загружены. Посмотрите превью и сохраните настройки для применения.';
    });
    this.remove=button('Удалить выбранные обои',async(id,csrf)=>{
      const selected=this.select.value;if(!selected)return;
      if(!window.confirm('Удалить выбранные обои из каталога?'))return;
      await this.request(id,'/'+encodeURIComponent(selected),{method:'DELETE',headers:{'X-CSRF-Token':csrf}});
      if(id!==this.deviceId)return;
      if(editor.wallpaperId===selected){editor.wallpaperId=null;if(editor.get('backdrop').value==='wallpaper')editor.get('backdrop').value='solid';editor.base.wallpaper_id=null;}
      await this.load(id);editor.changed();this.status.textContent='Обои удалены.';
    });
    this.status=document.createElement('p');this.status.className='hint';this.status.setAttribute('role','status');box.append(this.status);
  }
  async request(id,suffix='',init={}){
    const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),12000);
    try{
      const response=await fetch(`/api/v1/devices/${encodeURIComponent(id)}/wallpapers${suffix}`,{...init,credentials:'same-origin',cache:'no-store',signal:controller.signal});
      if(!response.ok){const messages={401:'Сессия закончилась. Войдите снова.',403:'Нет разрешения. Обновите страницу.',404:'Обои или устройство недоступны.',409:'Каталог заполнен или обои используются. Сначала выберите и сохраните другой фон.',413:'Файл слишком большой.',415:'Нужен PNG или JPEG.',422:'Не удалось прочитать изображение. Проверьте тип, размер и название.',429:'Слишком много загрузок. Подождите минуту.'};throw new Error(messages[response.status]||'Сервис обоев временно недоступен.');}
      return response.status===204?null:await response.json();
    }finally{clearTimeout(timer);}
  }
  async run(fn){
    if(this.busy||!this.deviceId)return;
    const id=this.deviceId,session=this.session();if(!session)return;
    this.busy=true;this.upload.disabled=true;this.remove.disabled=true;this.status.textContent='Подождите…';
    try{await fn(id,session.csrf_token);}catch(error){if(this.deviceId===id)this.status.textContent=error.name==='AbortError'?'Сервер не ответил. Попробуйте ещё раз.':error.message;}
    finally{this.busy=false;this.upload.disabled=false;this.remove.disabled=false;}
  }
  async load(id,choose=null){
    const version=++this.generation;this.deviceId=id;
    try{
      const items=await this.request(id);if(version!==this.generation)return;
      this.select.replaceChildren(new Option('Выбрать обои',''));
      for(const item of items)this.select.append(new Option(item.label,item.id));
      this.select.value=choose||this.editor.wallpaperId||'';
      if(choose){this.editor.wallpaperId=choose;this.editor.get('backdrop').value='wallpaper';this.editor.changed();}
    }catch(error){if(version===this.generation)this.status.textContent=error.message;}
  }
  clear(){this.generation++;this.deviceId=null;this.select.replaceChildren();this.file.value='';this.label.value='';this.status.textContent='';}
}
