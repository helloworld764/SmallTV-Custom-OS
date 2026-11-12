import io,json,time
from urllib.error import URLError
import pytest
from test_telegram import env,apply,message,link,FakeTelegram
from test_wallpapers import picture
from backend.app.bot_handler import process_update,identity
from backend.app.bot_worker import poll_once,deliver_replies
from backend.app.bot_custom_pets import prepare
from backend.app.bot_transport import TelegramClient,TelegramError

class MediaTelegram(FakeTelegram):
    def __init__(self,updates=None):super().__init__(updates);self.downloads=[]
    def download_image(self,file_id):self.downloads.append(file_id);return picture()
    def send_photo_bytes(self,payload,raw):self.calls.append(('sendPrivatePhoto',payload));assert raw.startswith(b'\x89PNG') and len(raw)<=1048576

def photo(env,who=101,document=True):
    update=message(env,'',who)
    del update['message']['text']
    if document:update['message']['document']={'file_id':'synthetic-image','file_size':1000,'mime_type':'image/png','file_name':'not-stored.png'}
    else:update['message']['photo']=[{'file_id':'small','file_size':100,'width':30,'height':30},{'file_id':'large','file_size':1000,'width':240,'height':240}]
    return update

def receive(env,update,client=None):
    client=client or MediaTelegram();now=int(time.time())
    prepared=prepare(env['db'],client,update,now)
    process_update(env['db'],env['settings'],update,now=now,prepared_image=prepared)
    with env['db'].connection() as c:row=c.execute('SELECT * FROM telegram_replies WHERE update_id=?',(update['update_id'],)).fetchone()
    return dict(row) if row else None

def test_bot_own_pet_photo_name_preview_confirm_repeat_and_shared_api(env):
    link(env);_,start=apply(env,'pcustom-start',callback=True);assert 'Шаблон' in start['text']
    assert '[ВСТАВЬ СЮДА ОПИСАНИЕ' in start['text']
    card=receive(env,photo(env));assert card['photo_asset'].startswith('draft:') and 'имя' in card['text']
    assert env['api'].get('/api/v1/pets/current').status_code==404
    _,invalid=apply(env,'x'*25);assert '1–24' in invalid['text']
    _,preview=apply(env,'Новая Мурка');assert 'Пока ничего не применено' in preview['text']
    token=json.loads(preview['markup'])['inline_keyboard'][0][0]['callback_data']
    _,saved=apply(env,token,callback=True);assert 'сохранены' in saved['text'] and saved['photo_asset'].startswith('custom:')
    pet=env['api'].get('/api/v1/pets/current').json();assert pet['name']=='Новая Мурка' and pet['image_id']
    _,again=apply(env,token,callback=True);assert 'уже подтверждён' in again['text']
    client=MediaTelegram();deliver_replies(env['db'],client)
    assert any(method=='sendPrivatePhoto' for method,_ in client.calls)
    assert 'not-stored.png' not in json.dumps(client.calls)

def test_full_prompt_states_are_copied_as_one_native_pre_block_after_queue_reload(env):
    from backend.app.custom_pets import PROMPT
    link(env);_,start=apply(env,'pcustom-start',callback=True)
    markup=json.loads(start['markup']);entity=markup['_storm_entities'][0]
    assert entity['type']=='pre' and len(start['text'])<4096
    encoded=start['text'].encode('utf-16-le')
    assert encoded[entity['offset']*2:(entity['offset']+entity['length'])*2].decode('utf-16-le')==PROMPT
    for state in ('neutral','happy','eating','playing','sleeping','waking','hungry_sad','tired','blink'):
        assert state+'.png' in PROMPT
    assert PROMPT.count('[ВСТАВЬ СЮДА ОПИСАНИЕ СВОЕГО ПИТОМЦА]')==1
    assert 'только neutral.png' in start['text']
    client=MediaTelegram();deliver_replies(env['db'],client)
    payload=next(p for method,p in client.calls if method=='sendMessage' and p.get('entities'))
    assert payload['entities']==[entity]
    assert '_storm_entities' not in payload['reply_markup'] and 'parse_mode' not in payload
    assert payload['text']==start['text']

def test_bot_media_without_opt_in_and_other_chat_never_downloaded(env):
    link(env);client=MediaTelegram();assert prepare(env['db'],client,photo(env),int(time.time())) is None
    apply(env,'pcustom-start',callback=True)
    assert prepare(env['db'],client,photo(env,who=999),int(time.time())) is None
    forwarded=photo(env);forwarded['message']['forward_origin']={}
    assert identity(forwarded) is None and prepare(env['db'],client,forwarded,int(time.time())) is None
    assert not client.downloads

def test_bot_cancel_navigation_expiry_and_stale_callback_do_not_adopt(env):
    link(env);apply(env,'pcustom-start',callback=True);receive(env,photo(env));_,preview=apply(env,'Питомец')
    # Main menu text cancels the setup instead of silently naming a pet.
    with env['db'].connection() as c:assert c.execute('SELECT count(*) FROM telegram_custom_pet_inputs').fetchone()[0]==0
    apply(env,'pcustom-start',callback=True);receive(env,photo(env));_,preview=apply(env,'Test')
    callback=json.loads(preview['markup'])['inline_keyboard'][0][0]['callback_data']
    apply(env,'pet-cancel',callback=True);_,reply=apply(env,callback,callback=True);assert 'устарело' in reply['text']
    assert env['api'].get('/api/v1/pets/current').status_code==404
    apply(env,'pcustom-start',callback=True);apply(env,'Настройки')
    with env['db'].connection() as c:assert c.execute('SELECT count(*) FROM telegram_custom_pet_inputs').fetchone()[0]==0

def test_unlink_during_download_rejects_prepared_image_and_private_delivery(env):
    link(env);apply(env,'pcustom-start',callback=True);update=photo(env);client=MediaTelegram()
    prepared=prepare(env['db'],client,update,int(time.time()))
    env['api'].delete('/api/v1/telegram/link',headers=env['csrf'])
    process_update(env['db'],env['settings'],update,prepared_image=prepared)
    with env['db'].connection() as c:assert c.execute('SELECT count(*) FROM pet_image_drafts').fetchone()[0]==0
    deliver_replies(env['db'],client);assert not any(m=='sendPrivatePhoto' for m,_ in client.calls)

def test_worker_downloads_and_processes_realistic_update_once(env):
    link(env);apply(env,'pcustom-start',callback=True);update=photo(env);client=MediaTelegram([update])
    poll_once(env['db'],env['settings'],client);poll_once(env['db'],env['settings'],client)
    assert client.downloads==['synthetic-image']
    assert any(method=='sendPrivatePhoto' for method,_ in client.calls)

def test_invalid_document_and_decoder_failure_preserve_current_pet(env):
    link(env);apply(env,'pcustom-start',callback=True);update=photo(env);update['message']['document']['mime_type']='image/svg+xml'
    client=MediaTelegram();reply=receive(env,update,client);assert 'PNG/JPEG' in reply['text'] and not client.downloads
    class Broken(MediaTelegram):
        def download_image(self,file_id):return b'invalid image'
    assert 'Не удалось' in receive(env,photo(env),Broken())['text']
    assert env['api'].get('/api/v1/pets/current').status_code==404

@pytest.mark.parametrize('path,size',[('../secret',100),('https://example.invalid/file',100),('/absolute/file.png',100),('photos/../file.png',100),('photos/file.png',2097153),('photos/file.png',None)])
def test_telegram_download_rejects_untrusted_path_or_size_without_network(path,size,monkeypatch):
    client=TelegramClient('synthetic-not-a-token')
    monkeypatch.setattr(client,'call',lambda *args:{'file_path':path,'file_size':size})
    class Block:
        def open(self,*args,**kwargs):pytest.fail('Untrusted path reached network')
    client._opener=Block()
    with pytest.raises(TelegramError) as error:client.download_image('synthetic')
    assert 'synthetic-not-a-token' not in str(error.value)

def test_telegram_file_download_is_bounded_and_network_errors_hide_token(monkeypatch):
    client=TelegramClient('synthetic-not-a-token');monkeypatch.setattr(client,'call',lambda *args:{'file_path':'photos/file_1.png','file_size':100})
    class Response:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def read(self,size):assert size==2097153;return b'image'
    class Opener:
        def open(self,request,timeout):assert request.full_url.startswith('https://api.telegram.org/file/bot');assert timeout==15;return Response()
    client._opener=Opener();assert client.download_image('synthetic')==b'image'
    class Bad:
        def open(self,*args,**kwargs):raise URLError('sensitive synthetic-not-a-token')
    client._opener=Bad()
    with pytest.raises(TelegramError) as error:client.download_image('synthetic')
    assert 'synthetic-not-a-token' not in str(error.value) and error.value.__suppress_context__
