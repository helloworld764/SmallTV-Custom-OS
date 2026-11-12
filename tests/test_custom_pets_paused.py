"""Production defaults must keep retained custom-pet code unreachable."""
import json,time
from test_auth import env,template,csrf
from test_wallpapers import picture
from backend.app import features,custom_pets
from backend.app.image_processing import normalize_pet
from test_telegram import env as bot_env,apply,link,message,FakeTelegram
from backend.app.bot_worker import poll_once,deliver_replies

def test_paused_routes_absent_from_api_and_openapi(env):
    client=env[0];headers=csrf(env)
    assert features.CUSTOM_PETS_ENABLED is False
    for path in ('/image-prompt','/image-drafts','/image-confirm','/current/image/12345678-1234-4123-8123-123456789012'):
        assert client.get('/api/v1/pets'+path).status_code==404
    assert client.post('/api/v1/pets/image-drafts',headers={**headers,'Content-Type':'image/png'},content=picture()).status_code==404
    assert client.post('/api/v1/pets/image-confirm',headers=headers,json={'draft_id':'12345678-1234-4123-8123-123456789012','name':'No'}).status_code==404
    paths=client.get('/openapi.json').json()['paths']
    assert not any('/pets/' in path and 'image' in path for path in paths)

def test_saved_private_image_is_preserved_while_basic_pet_and_care_work(env):
    client,app,users,_=env;headers=csrf(env)
    pet=client.post('/api/v1/pets',headers=headers,json={'kind':'Byte','name':'Saved'}).json()
    # Seed an appearance created before the pause. There is no public setup route.
    with app.state.database.transaction() as c:
        draft=custom_pets.store_draft(c,users['alice'],normalize_pet(picture(),'image/png'),int(time.time()))
        custom_pets.confirm_owned(c,users['alice'],draft['id'],'Saved',int(time.time()))
        before=tuple(c.execute('SELECT * FROM pet_appearances').fetchone())
    actual=client.get('/api/v1/pets/current').json()
    assert actual['id']==pet['id'] and actual['kind']=='Byte' and actual['name']=='Saved' and actual['image_id'] is None
    from uuid import uuid4
    assert client.post(f'/api/v1/pets/{pet["id"]}/actions',headers=headers,json={'action_id':str(uuid4()),'action':'feed'}).status_code==201
    with app.state.database.connection() as c:assert tuple(c.execute('SELECT * FROM pet_appearances').fetchone())==before

def test_bot_menu_and_stale_custom_callbacks_are_disabled(bot_env):
    link(bot_env)
    _,menu=apply(bot_env,'pet-create',callback=True)
    assert 'pcustom-' not in menu['markup'] and 'Pixel' in menu['markup']
    for value in ('pcustom-start','pcustom-confirm:12345678-1234-4123-8123-123456789012','pet-upload'):
        _,reply=apply(bot_env,value,callback=value!='pet-upload')
        assert 'временно отключена' in reply['text'] and 'pcustom-' not in reply['markup']
    with bot_env['db'].connection() as c:
        assert c.execute('SELECT count(*) FROM pet_image_drafts').fetchone()[0]==0
        assert c.execute('SELECT count(*) FROM telegram_custom_pet_inputs').fetchone()[0]==0

def test_paused_worker_never_downloads_photos_or_sends_old_custom_prompts(bot_env):
    link(bot_env);now=int(time.time())
    with bot_env['db'].transaction() as c:
        c.execute('DELETE FROM telegram_replies')
        c.execute("INSERT INTO telegram_custom_pet_inputs(telegram_id,user_id,token,step,expires_at) VALUES (101,?,'old','photo',?)",(bot_env['owner'],now+900))
        markup=json.dumps({'inline_keyboard':[[{'text':'Create','callback_data':'pcustom-start'}]]})
        c.execute('INSERT INTO telegram_replies(update_id,telegram_id,user_id,text,markup,expires_at) VALUES (999999,101,?,?,?,?)',(bot_env['owner'],'Old custom prompt',markup,now+300))
    class NoMedia(FakeTelegram):
        def download_image(self,*args):raise AssertionError('Paused worker must not download media')
    client=NoMedia();deliver_replies(bot_env['db'],client)
    assert not client.calls
    update=message(bot_env,'');del update['message']['text']
    update['message']['document']={'file_id':'must-not-be-read','file_size':1000,'mime_type':'image/png'}
    client.updates=[update];poll_once(bot_env['db'],bot_env['settings'],client)
    assert any(method=='sendMessage' and 'временно отключена' in payload['text'] for method,payload in client.calls)
    with bot_env['db'].connection() as c:
        assert c.execute('SELECT count(*) FROM pet_image_drafts').fetchone()[0]==0
        assert c.execute('SELECT count(*) FROM telegram_custom_pet_inputs').fetchone()[0]==1
