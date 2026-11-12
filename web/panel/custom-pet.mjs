// Appearance setup only; game actions remain in the existing bot/domain engine.
export class CustomPetEditor {
  constructor(session){
    this.session=session;this.generation=0;this.draft=null;this.url=null;this.busy=false;
    const root=document.createElement('details');root.className='card weather-settings';root.id='custom-pet-section';
    root.innerHTML=`<summary>Свой питомец: изображение и имя</summary>
      <p class="hint">PNG/JPEG до 2 МБ и 4 млн пикселей, лучше прозрачный PNG. Один нейтральный кадр: реакции показываются значками и подписями. Замена образа и имени сохраняет прогресс и ожидание возвращения.</p>
      <form id="custom-pet-form"><fieldset>
      <label for="custom-pet-file">Изображение питомца</label><input id="custom-pet-file" type="file" accept="image/png,image/jpeg">
      <button type="button" id="custom-pet-upload" class="secondary">Подготовить превью</button>
      <div id="custom-pet-draft" hidden><img id="custom-pet-preview" alt="Предпросмотр своего питомца" width="240" height="240">
      <label for="custom-pet-name">Имя питомца</label><input id="custom-pet-name" maxlength="24" autocomplete="off">
      <p class="hint">Пока ничего не применено. Черновик действует 15 минут. Новая загрузка заменяет только черновик.</p>
      <button id="custom-pet-confirm" type="submit" class="primary">Подтвердить образ и имя</button>
      <button id="custom-pet-cancel" type="button" class="text-button">Отменить черновик</button></div>
      </fieldset></form><p id="custom-pet-result" class="save-result" role="status"></p>
      <details><summary>Промпт для создания изображения</summary><button id="custom-pet-prompt" class="secondary" type="button">Показать шаблон</button><p id="custom-pet-template" class="hint"></p></details>`;
    document.querySelector('.section-heading').before(root);this.root=root;this.fieldset=root.querySelector('fieldset');this.status=root.querySelector('#custom-pet-result');
    this.get=id=>root.querySelector('#custom-pet-'+id);
    this.get('upload').onclick=()=>void this.run(async token=>{
      const file=this.get('file').files[0];if(!file||!['image/png','image/jpeg'].includes(file.type)||!file.size||file.size>2097152)throw new Error('Выберите PNG/JPEG до 2 МБ.');
      const draft=await this.request('/image-drafts',{method:'POST',headers:{'Content-Type':file.type,'X-CSRF-Token':token},body:file});
      this.draft=draft.id;
      const image=await this.request('/image-drafts/'+draft.id+'/image',{},true);
      if(this.url)URL.revokeObjectURL(this.url);this.url=URL.createObjectURL(image);this.get('preview').src=this.url;this.get('draft').hidden=false;this.get('confirm').disabled=false;this.get('name').focus();
      this.status.textContent='Превью готово. Введите имя и подтвердите. Текущий питомец ещё не изменён.';
    });
    this.get('form').onsubmit=event=>{event.preventDefault();if(!this.draft)return;void this.run(async token=>{
      const name=this.get('name').value.trim();if(!name||[...name].length>24)throw new Error('Введите имя от 1 до 24 символов.');
      const saved=await this.request('/image-confirm',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':token},body:JSON.stringify({draft_id:this.draft,name})});
      this.draft=null;this.get('confirm').disabled=true;this.get('draft').hidden=true;this.get('file').value='';this.status.textContent=`${saved.pet.name}: образ и имя сохранены. Откройте Tamagotchi в симуляторе.`;
    });};
    this.get('cancel').onclick=()=>void this.run(async token=>{if(this.draft)await this.request('/image-drafts/'+this.draft,{method:'DELETE',headers:{'X-CSRF-Token':token}});this.clear();this.status.textContent='Черновик отменён. Питомец не изменён.';});
    this.get('prompt').onclick=()=>void this.run(async()=>{const value=await this.request('/image-prompt');this.get('template').textContent=value.prompt;this.status.textContent='Скопируйте шаблон во внешний генератор, затем загрузите готовое изображение.';});
  }
  clear(){this.generation++;this.draft=null;if(this.url)URL.revokeObjectURL(this.url);this.url=null;this.get('preview').removeAttribute('src');this.get('draft').hidden=true;this.get('file').value='';this.get('name').value='';this.get('template').textContent='';this.status.textContent='';}
  async request(path,init={},image=false){
    const generation=this.generation,controller=new AbortController(),timer=setTimeout(()=>controller.abort(),15000);
    try{const response=await fetch('/api/v1/pets'+path,{...init,credentials:'same-origin',cache:'no-store',signal:controller.signal});
      if(generation!==this.generation)throw new Error('Сессия изменилась.');
      if(!response.ok){const labels={401:'Войдите в панель снова.',403:'Обновите страницу и повторите.',404:'Черновик недоступен.',409:'Этот черновик уже подтверждён с другим именем.',410:'Черновик истёк. Загрузите изображение снова.',413:'Файл слишком большой.',415:'Нужен PNG или JPEG.',422:'Проверьте имя и изображение: без анимации, до 4 млн пикселей и 4096 по каждой стороне.',429:'Слишком много загрузок. Подождите минуту.'};throw new Error(labels[response.status]||'Не удалось обработать запрос.');}
      if(response.status===204)return null;
      if(!image){const value=await response.json();if(generation!==this.generation)throw new Error('Сессия изменилась.');return value;}
      if(response.headers.get('content-type')?.split(';')[0]!=='image/png')throw new Error('Некорректное превью.');
      const blob=await response.blob();if(generation!==this.generation)throw new Error('Сессия изменилась.');if(!blob.size||blob.size>262144)throw new Error('Некорректное превью.');return blob;
    }finally{clearTimeout(timer);}
  }
  async run(fn){if(this.busy||!this.session())return;this.busy=true;this.fieldset.disabled=true;this.status.textContent='Обрабатываем…';const generation=this.generation;
    try{await fn(this.session().csrf_token);}catch(error){if(generation===this.generation)this.status.textContent=error.name==='AbortError'?'Сервер не ответил. Попробуйте снова.':error.message;}
    finally{this.busy=false;this.fieldset.disabled=false;}
  }
}
