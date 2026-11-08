import io
import itertools
import json
import secrets
import time
from urllib.error import HTTPError, URLError

import pytest
from fastapi.testclient import TestClient

from backend.app import weather
from backend.app.bot_handler import identity, process_update
from backend.app.bot_transport import TelegramClient, TelegramError
from backend.app.bot_worker import deliver_replies, initialize_bot, poll_once, worker_lock
from backend.app.config import Settings
from backend.app.database import Database
from backend.app.main import create_app
from backend.app.security import create_account
from backend.app.telegram import preferences


@pytest.fixture
def env(tmp_path):
    settings = Settings(_env_file=None, db_path=tmp_path/'bot.sqlite3')
    db = Database(settings.db_path)
    db.initialize()
    password = secrets.token_urlsafe(24)
    owner = create_account(db, 'bot-owner', password, 'Owner', role='super_admin')
    other = create_account(db, 'bot-other', password, 'Other', role='user')
    with db.transaction() as c:
        for device, user in [('own-tv', owner), ('other-tv', other)]:
            c.execute('INSERT INTO devices(id,owner_id,name) VALUES (?,?,?)', (device,user,device))
            c.execute('INSERT INTO device_settings(device_id) VALUES (?)', (device,))
    with TestClient(create_app(settings)) as client:
        login = client.post('/api/v1/auth/login', headers={'X-SmallTV-Client':'web'},
                            json={'username':'bot-owner','password':password})
        yield {'db':db,'settings':settings,'api':client,'owner':owner,'other':other,
               'csrf':{'X-CSRF-Token':login.json()['csrf_token']},'ids':itertools.count(1)}


def message(env, text, who=101, callback=False):
    update = {'update_id':next(env['ids'])}
    sender = {'id':who,'is_bot':False}
    msg = {'chat':{'id':who,'type':'private'}, 'from':sender,'text':text}
    if callback:
        update['callback_query'] = {'id':'test-callback','from':sender,'message':msg,'data':text}
    else:
        update['message'] = msg
    return update


def apply(env, text, who=101, callback=False, now=None):
    update = message(env,text,who,callback)
    process_update(env['db'],env['settings'],update,now=now)
    with env['db'].connection() as c:
        row = c.execute('SELECT * FROM telegram_replies WHERE update_id=?',(update['update_id'],)).fetchone()
    return update, dict(row) if row else None


def link(env, who=101):
    response = env['api'].post('/api/v1/telegram/link-code',headers=env['csrf'])
    assert response.status_code == 201
    code = response.json()['code']
    apply(env,'/link '+code,who)
    assert env['api'].get('/api/v1/telegram/status').json()['linked']
    return code


def test_code_api_requires_session_csrf_and_never_exposes_identity(env):
    api=env['api']
    assert api.post('/api/v1/telegram/link-code').status_code == 403
    assert api.post('/api/v1/telegram/link-code', headers={**env['csrf'],'Origin':'https://evil.invalid'}).status_code == 403
    assert api.get('/api/v1/telegram/status').json() == {'linked':False,'notifications':{
        'enabled':False,'codex':True,'connection':True,'pet':True,'system':True}}
    api.cookies.clear()
    for method,path in [('get','status'),('post','link-code'),('delete','link')]:
        assert getattr(api,method)('/api/v1/telegram/'+path).status_code == 401


def test_code_hash_single_use_and_one_to_one_link(env):
    code=link(env)
    with env['db'].connection() as c:
        assert c.execute('SELECT count(*) FROM telegram_link_codes').fetchone()[0] == 0
        reply=' '.join(row[0] for row in c.execute('SELECT text FROM telegram_replies'))
        assert code not in reply
    _,denial=apply(env,'/link '+code,who=202)
    assert denial['user_id'] is None
    assert env['api'].post('/api/v1/telegram/link-code',headers=env['csrf']).status_code == 409
    assert 'telegram_id' not in env['api'].get('/api/v1/telegram/status').text


def test_code_cancel_expiry_and_issue_cooldown(env):
    api=env['api']; response=api.post('/api/v1/telegram/link-code',headers=env['csrf'])
    code=response.json()['code']
    with env['db'].connection() as c:
        stored=c.execute('SELECT code_hash FROM telegram_link_codes').fetchone()[0]
        assert stored != code and len(stored)==64
    assert api.post('/api/v1/telegram/link-code',headers=env['csrf']).status_code==429
    _,reply=apply(env,'/link '+code,now=int(time.time())+301)
    assert reply['user_id'] is None
    assert api.delete('/api/v1/telegram/link-code',headers=env['csrf']).status_code==204
    _,reply=apply(env,'/link '+code)
    assert reply['user_id'] is None


def test_failed_claims_are_limited(env):
    for _ in range(5): apply(env,'/link '+('a'*24))
    _,reply=apply(env,'/link '+('a'*24))
    assert 'минуту' in reply['text']


@pytest.mark.parametrize('change', ['group','sender','bot','forward','edited','malformed'])
def test_reject_untrusted_message_surfaces(env,change):
    update=message(env,'/start')
    msg=update['message']
    if change=='group': msg['chat']['type']='group'
    elif change=='sender': msg['chat']['id']=202
    elif change=='bot': msg['from']['is_bot']=True
    elif change=='forward': msg['forward_origin']={}
    elif change=='edited': update['edited_message']=update.pop('message')
    elif change=='malformed': msg['chat']='invalid'
    assert identity(update) is None
    assert process_update(env['db'],env['settings'],update) is None
    with env['db'].connection() as c:
        assert c.execute('SELECT count(*) FROM telegram_replies').fetchone()[0]==0


def test_device_choice_is_owner_scoped_even_for_super_admin(env):
    link(env)
    _,reply=apply(env,'Мои устройства')
    assert 'other-tv' not in reply['markup'] and 'own-tv' in reply['markup']
    apply(env,'device:other-tv',callback=True)
    with env['db'].connection() as c:
        assert c.execute('SELECT selected_device_id FROM telegram_links').fetchone()[0] is None
    apply(env,'device:own-tv',callback=True)
    with env['db'].connection() as c:
        assert c.execute('SELECT selected_device_id FROM telegram_links').fetchone()[0]=='own-tv'


def test_notifications_default_off_preserve_categories_and_deduplicate(env):
    link(env)
    update,_=apply(env,'notify:codex',callback=True)
    process_update(env['db'],env['settings'],update)
    apply(env,'notify:enabled',callback=True)
    apply(env,'notify:enabled',callback=True)
    with env['db'].connection() as c:
        pref=preferences(c,env['owner'])
    assert pref['enabled'] is False and pref['codex'] is False and pref['pet'] is True


def test_city_is_shared_with_web_and_failed_lookup_preserves_it(env,monkeypatch):
    link(env)
    monkeypatch.setattr(weather,'geocode',lambda *args:{'name':'Test City','latitude':51.5,'longitude':0,
                                                     'timezone':'Europe/London','country':'Test'})
    apply(env,'city',callback=True)
    apply(env,'Test City')
    assert env['api'].get('/api/v1/preferences/weather').json()['city']=='Test City'
    def failure(*args): raise weather.HTTPException(503,'unavailable')
    monkeypatch.setattr(weather,'geocode',failure)
    _,reply=apply(env,'/city Other')
    assert 'временно' in reply['text']
    assert env['api'].get('/api/v1/preferences/weather').json()['city']=='Test City'


def test_city_mode_does_not_treat_callback_as_city(env,monkeypatch):
    link(env)
    apply(env,'city',callback=True)
    monkeypatch.setattr(weather,'geocode',lambda *args: pytest.fail('callback must not query geocoding'))
    apply(env,'notifications',callback=True)


def test_unlink_revokes_callbacks_pending_replies_and_consent(env):
    link(env)
    apply(env,'notify:enabled',callback=True)
    assert env['api'].delete('/api/v1/telegram/link',headers=env['csrf']).status_code==204
    _,reply=apply(env,'device:own-tv',callback=True)
    assert reply['user_id'] is None
    with env['db'].connection() as c:
        assert not preferences(c,env['owner'])['enabled']
        assert c.execute('SELECT count(*) FROM telegram_replies WHERE user_id IS NOT NULL').fetchone()[0]==0


class FakeTelegram:
    def __init__(self, updates=None): self.calls=[]; self.updates=updates or []; self.failure=None
    def call(self,method,payload=None):
        self.calls.append((method,payload))
        if self.failure and method=='sendMessage': raise self.failure
        if method=='getMe': return {'id':42,'is_bot':True}
        if method=='getWebhookInfo': return {'url':''}
        if method=='getUpdates': return self.updates
        return True
    def send_photo(self,payload,path):
        self.calls.append(('sendPhoto',payload))
        if self.failure: raise self.failure
        assert path.is_file()
        return True


def test_worker_offsets_delivery_and_retries_do_not_reapply_mutations(env):
    link(env)
    update=message(env,'notify:enabled',callback=True)
    client=FakeTelegram([update])
    initialize_bot(env['db'],client)
    poll_once(env['db'],env['settings'],client)
    poll_once(env['db'],env['settings'],client)
    with env['db'].connection() as c:
        assert preferences(c,env['owner'])['enabled'] is True
        assert c.execute('SELECT next_offset FROM telegram_worker_state').fetchone()[0]==update['update_id']+1
    assert sum(method=='answerCallbackQuery' for method,_ in client.calls)==1


def test_delivery_retry_is_durable_and_inactive_account_drops_private_reply(env):
    link(env)
    client=FakeTelegram(); client.failure=TelegramError()
    with pytest.raises(TelegramError): deliver_replies(env['db'],client)
    with env['db'].transaction() as c:
        assert c.execute('SELECT count(*) FROM telegram_replies').fetchone()[0]>0
        c.execute('UPDATE local_accounts SET is_active=0 WHERE user_id=?',(env['owner'],))
    client.failure=None
    before=len(client.calls)
    deliver_replies(env['db'],client)
    assert len(client.calls)==before
    _,reply=apply(env,'/settings')
    assert reply['user_id'] is None


def test_bot_identity_webhook_and_process_lock_protect_polling(env,tmp_path):
    client=FakeTelegram(); initialize_bot(env['db'],client)
    with env['db'].transaction() as c: c.execute('UPDATE telegram_worker_state SET bot_id=99')
    with pytest.raises(ValueError): initialize_bot(env['db'],client)
    class Webhook(FakeTelegram):
        def call(self,method,payload=None):
            return {'url':'https://example.invalid/hook'} if method=='getWebhookInfo' else super().call(method,payload)
    with pytest.raises(ValueError): initialize_bot(env['db'],Webhook())
    with worker_lock(tmp_path/'worker.lock'):
        with pytest.raises(OSError):
            with worker_lock(tmp_path/'worker.lock'): pass


def test_restore_revokes_telegram_links_codes_and_pending_delivery(env,tmp_path):
    from backend.app.backups import backup_database, restore_database
    link(env)
    apply(env,'notify:enabled',callback=True)
    snapshot=tmp_path/'snapshot.sqlite3'; restored=tmp_path/'restored.sqlite3'
    backup_database(env['db'].path,snapshot)
    restore_database(snapshot,restored,active_database=env['db'].path)
    with Database(restored).connection() as c:
        for table in ('telegram_links','telegram_link_codes','telegram_replies'):
            assert c.execute(f'SELECT count(*) FROM {table}').fetchone()[0]==0
        assert preferences(c,env['owner'])['enabled'] is False


def test_unauthenticated_users_cannot_trigger_geocoding(env,monkeypatch):
    monkeypatch.setattr(weather,'geocode',lambda *args: pytest.fail('private weather lookup'))
    apply(env,'/city London')


def test_cli_check_never_contacts_telegram_or_prints_secrets(monkeypatch,capsys):
    from backend.app import bot_worker
    token='123456:'+('b'*35)
    monkeypatch.setattr(bot_worker,'Settings',lambda:Settings(_env_file=None,telegram_bot_token=token))
    monkeypatch.setattr(TelegramClient,'call',lambda *args:pytest.fail('check must stay offline'))
    assert bot_worker.main(['check'])==0
    assert token not in capsys.readouterr().out
    assert token not in repr(Settings(_env_file=None,telegram_bot_token=token))


@pytest.mark.parametrize('http_code',[401,409,429,500])
def test_transport_does_not_leak_token_in_exceptions(http_code):
    token='123456:'+('a'*35)  # Deliberately synthetic.
    client=TelegramClient(token)
    class Opener:
        def open(self,*args,**kwargs):
            raise HTTPError('https://api.telegram.org/bot'+token, http_code, token, {},
                            io.BytesIO(b'{"parameters":{"retry_after":12}}'))
    client._opener=Opener()
    with pytest.raises(TelegramError) as error: client.call('getMe')
    assert token not in str(error.value) and token not in repr(error.value)
    assert error.value.status==http_code and error.value.retry_after==12


def pet_button(reply,label):
    for row in json.loads(reply['markup'])['inline_keyboard']:
        for button in row:
            if button['text']==label: return button['callback_data']
    raise AssertionError('Pet button not found: '+label)


def adopt_in_bot(env,now,kind='Pixel',name='Мурка'):
    apply(env,'pet-create',callback=True,now=now)
    apply(env,'petkind:'+kind,callback=True,now=now)
    return apply(env,name,now=now)[1]


def test_bot_adoption_card_api_and_simulator_share_one_profile(env,monkeypatch):
    from backend.app import pets
    link(env); now=int(time.time()); monkeypatch.setattr(pets,'now',lambda:now)
    reply=adopt_in_bot(env,now)
    assert reply['photo_asset']=='Pixel-happy' and 'Мурка' in reply['text']
    data=env['api'].get('/api/v1/pets/current').json()
    assert data['name']=='Мурка' and data['satiety']==80 and data['round']==1
    assert data['reaction']['kind']=='ADOPT'
    client=FakeTelegram(); deliver_replies(env['db'],client,now)
    assert any(method=='sendPhoto' and 'Мурка' in payload['caption'] for method,payload in client.calls)


def test_bot_name_validation_cancel_expiry_and_no_null(env):
    link(env); now=int(time.time())
    _,reply=apply(env,'petkind:Null',callback=True,now=now)
    assert reply['photo_asset'] is None
    apply(env,'petkind:Byte',callback=True,now=now)
    _,reply=apply(env,'\u200b',now=now)
    assert 'Имя должно' in reply['text']
    _,reply=apply(env,'Valid name',now=now+301)
    assert 'истекло' in reply['text']
    assert env['api'].get('/api/v1/pets/current').status_code==404
    apply(env,'petkind:Glitch',callback=True,now=now)
    apply(env,'Настройки',now=now)
    apply(env,'Accidental text',now=now)
    assert env['api'].get('/api/v1/pets/current').status_code==404


def test_care_changes_once_and_receipt_reaction_reaches_api(env,monkeypatch):
    from backend.app import pets
    link(env); now=int(time.time()); monkeypatch.setattr(pets,'now',lambda:now)
    reply=adopt_in_bot(env,now)
    button=pet_button(reply,'Кормить')
    update,reply=apply(env,button,callback=True,now=now)
    assert reply['photo_asset']=='Pixel-eating'
    data=env['api'].get('/api/v1/pets/current').json()
    assert data['satiety']==100 and data['reaction']['kind']=='FEED'
    process_update(env['db'],env['settings'],update,now=now)
    _,duplicate=apply(env,button,callback=True,now=now)
    assert 'уже было выполнено' in duplicate['text']
    with env['db'].connection() as c:
        assert c.execute('SELECT count(*) FROM pet_actions').fetchone()[0]==1


def test_sleep_stale_feed_wake_and_cooldown_are_shared_rules(env):
    link(env); now=int(time.time())
    reply=adopt_in_bot(env,now)
    old_feed=pet_button(reply,'Кормить')
    _,sleeping=apply(env,pet_button(reply,'Спать'),callback=True,now=now)
    assert sleeping['photo_asset']=='Pixel-sleeping'
    _,blocked=apply(env,old_feed,callback=True,now=now)
    assert 'Сначала разбудите' in blocked['text']
    wake=pet_button(sleeping,'Разбудить')
    _,blocked=apply(env,wake,callback=True,now=now+1)
    assert 'Подождите 9 сек.' in blocked['text']
    _,awake=apply(env,wake,callback=True,now=now+10)
    assert awake['photo_asset']=='Pixel-waking'
    _,fed=apply(env,pet_button(awake,'Кормить'),callback=True,now=now+10)
    _,blocked=apply(env,pet_button(fed,'Кормить'),callback=True,now=now+11)
    assert 'Подождите 59 сек.' in blocked['text']


def test_play_and_expired_keyboard_do_not_bypass_time_rules(env):
    link(env); now=int(time.time())
    reply=adopt_in_bot(env,now)
    button=pet_button(reply,'Играть')
    _,played=apply(env,button,callback=True,now=now)
    assert played['photo_asset']=='Pixel-playing' and '70/100' in played['text']
    _,expired=apply(env,pet_button(played,'Играть'),callback=True,now=now+301)
    assert 'устарела' in expired['text']
    with env['db'].connection() as c: assert c.execute('SELECT count(*) FROM pet_actions').fetchone()[0]==1


def test_pet_callbacks_cannot_access_another_owner(env):
    from backend.app import pets
    link(env); now=int(time.time())
    reply=adopt_in_bot(env,now)
    with env['db'].transaction() as c:
        c.execute('INSERT INTO telegram_links(telegram_id,user_id,linked_at) VALUES (202,?,?)',(env['other'],now))
        pets.adopt_owned(c,env['other'],pets.Adopt(kind='Byte',name='Other pet'),now)
    _,denied=apply(env,pet_button(reply,'Кормить'),who=202,callback=True,now=now)
    assert 'Other pet' in denied['text'] and 'Мурка' not in denied['text']
    with env['db'].connection() as c:
        assert c.execute('SELECT count(*) FROM pet_actions').fetchone()[0]==0


def test_missing_countdown_and_return_require_confirmation_no_reset_by_adoption(env):
    link(env); now=int(time.time()); adopt_in_bot(env,now)
    with env['db'].transaction() as c:
        c.execute('UPDATE pet_states SET last_care_at=?',(now-72*3600,))
    _,missing=apply(env,'/pet',now=now)
    assert '24 ч 0 мин' in missing['text'] and 'Вернуть' not in missing['markup']
    _,existing=apply(env,'petkind:Glitch',callback=True,now=now)
    assert 'уже создан' in existing['text']
    later=now+24*3600
    _,ready=apply(env,'/pet',now=later)
    confirm=pet_button(ready,'Вернуть')
    token=confirm.split(':')[1]
    _,denied=apply(env,'pact:'+token,callback=True,now=later)
    assert 'сначала' in denied['text']
    _,question=apply(env,confirm,callback=True,now=later)
    old_yes=pet_button(question,'Да, вернуть')
    _,cancelled=apply(env,'pet-cancel',callback=True,now=later)
    _,stale=apply(env,old_yes,callback=True,now=later)
    assert 'устарела' in stale['text']
    _,question=apply(env,pet_button(cancelled,'Вернуть'),callback=True,now=later)
    _,returned=apply(env,pet_button(question,'Да, вернуть'),callback=True,now=later)
    assert returned['photo_asset']=='Pixel-happy' and '80/100' in returned['text']
    apply(env,pet_button(question,'Да, вернуть'),callback=True,now=later)
    with env['db'].connection() as c:
        assert c.execute('SELECT round FROM pet_states').fetchone()[0]==2
        assert c.execute('SELECT kind,name FROM pets').fetchone()[:]==('Pixel','Мурка')
        assert c.execute('SELECT count(*) FROM pet_actions').fetchone()[0]==1


def test_menu_refresh_is_read_only_for_pet_state(env):
    link(env); now=int(time.time()); adopt_in_bot(env,now)
    with env['db'].connection() as c: before=tuple(c.execute('SELECT * FROM pet_states').fetchone())
    for _ in range(3): apply(env,'/pet',now=now+120)
    with env['db'].connection() as c:
        assert tuple(c.execute('SELECT * FROM pet_states').fetchone())==before
        assert c.execute('SELECT count(*) FROM pet_actions').fetchone()[0]==0


def test_pet_media_is_catalogue_only_and_photos_upload_as_multipart(env):
    from backend.app.bot_pets import photo_path
    assert photo_path('Null-neutral') is None and photo_path('../../.env') is None
    client=TelegramClient('synthetic-token')
    class Response:
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def read(self,limit): return b'{"ok":true,"result":{"message_id":1}}'
    class Opener:
        def open(self,request,timeout):
            assert request.full_url.endswith('/sendPhoto')
            assert request.headers['Content-type'].startswith('multipart/form-data; boundary=')
            assert b'name="photo"; filename="pet.png"' in request.data
            assert b'\x89PNG\r\n\x1a\n' in request.data
            return Response()
    client._opener=Opener()
    assert client.send_photo({'chat_id':101,'caption':'Pet card','reply_markup':{}},photo_path('Pixel-neutral'))=={'message_id':1}


def test_unlink_clears_pet_drafts_and_action_intents(env):
    link(env); now=int(time.time()); reply=adopt_in_bot(env,now)
    assert env['api'].delete('/api/v1/telegram/link',headers=env['csrf']).status_code==204
    with env['db'].connection() as c:
        assert c.execute('SELECT count(*) FROM telegram_pet_drafts').fetchone()[0]==0
        assert c.execute('SELECT count(*) FROM telegram_pet_intents').fetchone()[0]==0
    _,denied=apply(env,pet_button(reply,'Кормить'),callback=True,now=now)
    assert denied['photo_asset'] is None
