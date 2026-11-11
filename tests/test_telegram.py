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


def test_timed_sleep_card_api_and_early_wake_give_no_partial_energy(env,monkeypatch):
    from backend.app import pets
    from backend.app.pet_sleep import complete_due_sleeps
    link(env); start=int(time.time()); clock=[start]; monkeypatch.setattr(pets,'now',lambda:clock[0])
    reply=adopt_in_bot(env,start)
    _,asleep=apply(env,pet_button(reply,'Спать'),callback=True,now=start)
    assert 'Проснётся через 60 мин 0 сек' in asleep['text']
    clock[0]=start+1800
    before=env['api'].get('/api/v1/pets/current').json()
    assert before['energy']==80 and before['sleep_ends_at']==start+3600
    _,refreshed=apply(env,'/pet',now=clock[0])
    assert '30 мин 0 сек' in refreshed['text']
    apply(env,pet_button(refreshed,'Разбудить'),callback=True,now=clock[0])
    after=env['api'].get('/api/v1/pets/current').json()
    assert after['energy']==80 and after['sleeping'] is False and after['sleep_ends_at'] is None
    complete_due_sleeps(env['db'],start+3600)
    with env['db'].connection() as c:
        assert c.execute('SELECT status FROM pet_sleep_sessions').fetchone()[0]=='cancelled'
        assert c.execute("SELECT count(*) FROM telegram_replies WHERE notification_category='pet'").fetchone()[0]==0


def start_timed_sleep(env,now,enabled=True,category=True):
    link(env); reply=adopt_in_bot(env,now)
    apply(env,pet_button(reply,'Спать'),callback=True,now=now)
    with env['db'].transaction() as c:
        c.execute('INSERT INTO telegram_preferences(user_id,enabled,pet) VALUES (?,?,?)',(env['owner'],int(enabled),int(category)))
        c.execute('DELETE FROM telegram_replies')


def test_sleep_finishes_without_worker_and_completion_queues_once_without_rebasing_game(env,monkeypatch):
    from backend.app import pets
    from backend.app.pet_sleep import complete_due_sleeps
    start=int(time.time()); start_timed_sleep(env,start)
    clock=[start+3600]; monkeypatch.setattr(pets,'now',lambda:clock[0])
    before=env['api'].get('/api/v1/pets/current').json()
    assert before['sleeping'] is False and before['energy']==100 and before['reaction']['kind']=='WAKE'
    assert before['last_care_at']==start
    complete_due_sleeps(env['db'],start+3599)
    complete_due_sleeps(env['db'],clock[0])
    complete_due_sleeps(env['db'],clock[0])
    assert env['api'].get('/api/v1/pets/current').json()==before
    with env['db'].connection() as c:
        assert c.execute("SELECT count(*) FROM telegram_replies WHERE notification_category='pet'").fetchone()[0]==1
        assert c.execute('SELECT status FROM pet_sleep_sessions').fetchone()[0]=='completed'
    client=FakeTelegram(); deliver_replies(env['db'],client,clock[0]); deliver_replies(env['db'],client,clock[0])
    assert len(client.calls)==1 and 'час сна завершён' in client.calls[0][1]['text']
    clock[0]=start+7200
    assert env['api'].get('/api/v1/pets/current').json()['energy']==97


@pytest.mark.parametrize('enabled,category',[(False,True),(True,False),(False,False)])
def test_sleep_notification_obeys_consent_and_does_not_replay_when_enabled_later(env,enabled,category):
    from backend.app.pet_sleep import complete_due_sleeps
    start=int(time.time()); start_timed_sleep(env,start,enabled,category)
    complete_due_sleeps(env['db'],start+3600)
    with env['db'].transaction() as c:
        assert c.execute('SELECT count(*) FROM telegram_replies').fetchone()[0]==0
        c.execute('UPDATE telegram_preferences SET enabled=1,pet=1')
    complete_due_sleeps(env['db'],start+3601)
    with env['db'].connection() as c: assert c.execute('SELECT count(*) FROM telegram_replies').fetchone()[0]==0


def test_sleep_pending_notification_rechecks_switches_before_delivery(env):
    from backend.app.pet_sleep import complete_due_sleeps
    start=int(time.time()); start_timed_sleep(env,start)
    complete_due_sleeps(env['db'],start+3600)
    with env['db'].transaction() as c: c.execute('UPDATE telegram_preferences SET enabled=0')
    client=FakeTelegram(); deliver_replies(env['db'],client,start+3601)
    assert client.calls==[]


def test_old_sleep_notifications_are_dropped_after_outage(env):
    from backend.app.pet_sleep import complete_due_sleeps
    start=int(time.time()); start_timed_sleep(env,start)
    complete_due_sleeps(env['db'],start+3600+1800)
    with env['db'].connection() as c:
        assert c.execute('SELECT count(*) FROM telegram_replies').fetchone()[0]==0
        assert c.execute('SELECT status FROM pet_sleep_sessions').fetchone()[0]=='completed'


def test_restore_keeps_sleep_state_but_cancels_old_notification(env,tmp_path):
    from backend.app import pets
    from backend.app.backups import backup_database, restore_database
    from backend.app.pet_sleep import complete_due_sleeps
    start=int(time.time()); start_timed_sleep(env,start)
    snapshot=tmp_path/'sleep-backup.sqlite3'; restored=tmp_path/'sleep-restored.sqlite3'
    backup_database(env['db'].path,snapshot)
    restore_database(snapshot,restored,active_database=env['db'].path)
    database=Database(restored)
    complete_due_sleeps(database,start+3600)
    with database.connection() as c:
        assert c.execute('SELECT status FROM pet_sleep_sessions').fetchone()[0]=='cancelled'
        assert c.execute('SELECT count(*) FROM telegram_replies').fetchone()[0]==0
        pet=pets.view(pets.find_pet(c,env['owner']),start+3600)
        assert pet.energy==100 and pet.sleeping is False and pet.last_care_at==start


@pytest.mark.parametrize('key',['enabled','pet'])
def test_enabling_after_sleep_deadline_does_not_replay_event_after_worker_outage(env,key):
    from backend.app.pet_sleep import complete_due_sleeps
    start=int(time.time()); start_timed_sleep(env,start)
    apply(env,'notify:'+key,callback=True,now=start+3500)
    apply(env,'notify:'+key,callback=True,now=start+3610)
    complete_due_sleeps(env['db'],start+3620)
    with env['db'].connection() as c:
        assert c.execute("SELECT count(*) FROM telegram_replies WHERE notification_category='pet'").fetchone()[0]==0


def test_queued_sleep_notice_does_not_survive_switching_off_then_on(env):
    from backend.app.pet_sleep import complete_due_sleeps
    start=int(time.time()); start_timed_sleep(env,start)
    complete_due_sleeps(env['db'],start+3600)
    apply(env,'notify:enabled',callback=True,now=start+3601)
    apply(env,'notify:enabled',callback=True,now=start+3602)
    client=FakeTelegram(); deliver_replies(env['db'],client,start+3603)
    assert not any('час сна завершён' in payload.get('text','') for _,payload in client.calls)


def need_setup(env,enabled=True):
    from backend.app.pet_needs import scan_needs
    link(env); now=int(time.time())+1; adopt_in_bot(env,now)
    with env['db'].transaction() as c:
        c.execute('INSERT INTO telegram_preferences(user_id,enabled) VALUES (?,?)',(env['owner'],int(enabled)))
        c.execute('DELETE FROM telegram_replies')
    scan_needs(env['db'],now)
    return now


def need_values(env,now,satiety=80,energy=80,mood=80,**patch):
    from backend.app import pet_engine
    values={'satiety':satiety*pet_engine.UNIT,'energy':energy*pet_engine.UNIT,'mood':mood*pet_engine.UNIT,
            'updated_at':now,'last_care_at':now,'reaction_at':now,'sleeping':0}|patch
    with env['db'].transaction() as c:
        c.execute('UPDATE pet_states SET '+','.join(key+'=?' for key in values),tuple(values.values()))


def need_replies(env):
    with env['db'].connection() as c:
        return [dict(row) for row in c.execute('SELECT * FROM telegram_replies WHERE need_fields IS NOT NULL')]


def test_pet_need_crossing_combines_fields_and_does_not_mutate_game(env):
    from backend.app.pet_needs import scan_needs
    now=need_setup(env)
    need_values(env,now+60,satiety=20,energy=19,mood=18)
    with env['db'].connection() as c: before=tuple(c.execute('SELECT * FROM pet_states').fetchone())
    scan_needs(env['db'],now+60); scan_needs(env['db'],now+60)
    replies=need_replies(env)
    assert len(replies)==1
    assert all(label in replies[0]['text'] for label in ('Пора покормить','Пора отдохнуть','Хочется поиграть'))
    with env['db'].connection() as c: assert tuple(c.execute('SELECT * FROM pet_states').fetchone())==before
    client=FakeTelegram(); deliver_replies(env['db'],client,now+60)
    assert len(client.calls)==1


def test_needs_rearm_at_30_and_have_six_hour_limit_without_delayed_reminders(env):
    from backend.app.pet_needs import scan_needs
    now=need_setup(env)
    for offset,value in [(60,20),(120,29),(180,20),(240,30),(300,20)]:
        need_values(env,now+offset,satiety=value); scan_needs(env['db'],now+offset)
    assert len(need_replies(env))==1
    # A long outage is a baseline, not a reminder when a cooldown happens to end.
    later=now+6*3600+60
    need_values(env,later,satiety=20); scan_needs(env['db'],later)
    assert len(need_replies(env))==1
    need_values(env,later+60,satiety=30); scan_needs(env['db'],later+60)
    need_values(env,later+120,satiety=20); scan_needs(env['db'],later+120)
    assert len(need_replies(env))==1  # Previous expired reply is pruned; this is the new event.
    assert need_replies(env)[0]['notification_at']==later+120


def test_disabled_need_episode_does_not_replay_after_enabling(env):
    from backend.app.pet_needs import scan_needs
    now=need_setup(env,False)
    need_values(env,now+60,satiety=20); scan_needs(env['db'],now+60)
    apply(env,'notify:enabled',callback=True,now=now+61)
    scan_needs(env['db'],now+120)
    assert need_replies(env)==[]
    need_values(env,now+180,satiety=30); scan_needs(env['db'],now+180)
    need_values(env,now+240,satiety=20); scan_needs(env['db'],now+240)
    assert len(need_replies(env))==1


@pytest.mark.parametrize('situation',['sleeping','missing','unknown','new_round','outage'])
def test_needs_are_silent_in_suppressed_states_and_after_outage(env,situation):
    from backend.app.pet_needs import scan_needs
    now=need_setup(env)
    patch={}
    if situation=='sleeping': patch={'sleeping':1,'sleep_at':now+60}
    elif situation=='missing': patch={'last_care_at':now+60-72*3600}
    elif situation=='unknown': patch={'reaction_at':now+61}
    elif situation=='new_round': patch={'round':2}
    delta=301 if situation=='outage' else 60
    need_values(env,now+delta,satiety=10,energy=10,mood=10,**patch)
    scan_needs(env['db'],now+delta)
    assert need_replies(env)==[]


@pytest.mark.parametrize('recovery',['all','partial','sleep'])
def test_pending_needs_drop_resolved_fields_and_sleeping_recipients(env,recovery):
    from backend.app.pet_needs import scan_needs
    now=need_setup(env)
    need_values(env,now+60,satiety=10,energy=10); scan_needs(env['db'],now+60)
    need_values(env,now+61,satiety=80,energy=10 if recovery=='partial' else 80,
                **({'sleeping':1,'sleep_at':now+61} if recovery=='sleep' else {}))
    client=FakeTelegram(); deliver_replies(env['db'],client,now+61)
    if recovery=='partial':
        assert len(client.calls)==1
        assert 'Пора отдохнуть' in client.calls[0][1]['text'] and 'Пора покормить' not in client.calls[0][1]['text']
    else: assert client.calls==[]


def test_sleep_and_need_events_have_independent_durable_queue_identifiers(env):
    from backend.app import pets
    from backend.app.pet_needs import scan_needs
    from backend.app.pet_sleep import complete_due_sleeps
    now=need_setup(env)
    with env['db'].transaction() as c:
        c.execute('INSERT INTO telegram_links(telegram_id,user_id,linked_at) VALUES (202,?,?)',(env['other'],now-4000))
        c.execute('INSERT INTO telegram_preferences(user_id,enabled) VALUES (?,1)',(env['other'],))
        pet=pets.adopt_owned(c,env['other'],pets.Adopt(kind='Byte',name='Sleeper'),now-3540)
        pets.act_owned(c,env['other'],pet.id,pets.PetAction(action_id=secrets.token_hex(16),action='sleep'),now-3540)
    need_values_owner={'satiety':10*pets.engine.UNIT,'updated_at':now+60}
    with env['db'].transaction() as c:
        c.execute('UPDATE pet_states SET satiety=?,updated_at=? WHERE pet_id=(SELECT id FROM pets WHERE owner_id=?)',
                  (*need_values_owner.values(),env['owner']))
    scan_needs(env['db'],now+60); complete_due_sleeps(env['db'],now+60)
    with env['db'].connection() as c:
        rows=c.execute('SELECT update_id FROM telegram_replies WHERE notification_category IS NOT NULL').fetchall()
    assert len(rows)==2 and len({row[0] for row in rows})==2 and all(row[0]<0 for row in rows)


def device_options(env,card,label,now):
    labels=lambda reply:{button['text'] for row in json.loads(reply['markup'])['inline_keyboard'] for button in row}
    if label not in labels(card):
        group={'Тема':'Оформление','Яркость':'Экран','Стартовый экран':'Экран',
               'Формат часов':'Часы','Секунды':'Часы','Вид часов':'Часы','Стиль устройства':'Оформление'}[label]
        if group not in labels(card): card=apply(env,'/device',now=now)[1]
        card=apply(env,pet_button(card,group),callback=True,now=now)[1]
    return apply(env,pet_button(card,label),callback=True,now=now)[1]


def test_device_settings_are_grouped_and_back_returns_to_clock_section(env):
    link(env); now=int(time.time())
    _,root=apply(env,'device:own-tv',callback=True,now=now)
    labels=lambda reply:[button['text'] for row in json.loads(reply['markup'])['inline_keyboard'] for button in row]
    assert labels(root)==['Часы','Экран','Оформление','Обновить','Список устройств']
    _,clock=apply(env,pet_button(root,'Часы'),callback=True,now=now)
    assert labels(clock)==['Формат часов','Секунды','Вид часов','Обновить','Назад']
    choices=device_options(env,clock,'Вид часов',now)
    _,back=apply(env,pet_button(choices,'Назад'),callback=True,now=now)
    assert labels(back)==labels(clock)
    choices=device_options(env,back,'Вид часов',now)
    _,saved=apply(env,pet_button(choices,'Увеличенный'),callback=True,now=now)
    assert 'Увеличенный' in saved['text'] and labels(saved)==labels(clock)
    _,root=apply(env,pet_button(saved,'Назад'),callback=True,now=now)
    _,screen=apply(env,pet_button(root,'Экран'),callback=True,now=now)
    assert labels(screen)==['Яркость','Стартовый экран','Обновить','Назад']
    levels=device_options(env,screen,'Яркость',now)
    _,custom=apply(env,pet_button(levels,'Своя яркость'),callback=True,now=now)
    _,cancelled=apply(env,pet_button(custom,'Отмена'),callback=True,now=now)
    assert labels(cancelled)==labels(screen)


def test_group_navigation_still_checks_owner_and_expiry(env):
    link(env); now=int(time.time())
    _,root=apply(env,'device:own-tv',callback=True,now=now)
    button=pet_button(root,'Часы')
    with env['db'].transaction() as c:
        c.execute("INSERT INTO telegram_links(telegram_id,user_id,selected_device_id,linked_at) VALUES (202,?,'other-tv',?)",(env['other'],now))
    _,denied=apply(env,button,who=202,callback=True,now=now)
    assert 'own-tv' not in denied['text']
    _,expired=apply(env,button,callback=True,now=now+301)
    assert 'устарела' in expired['text']


@pytest.mark.parametrize('label,option,field,value,newer', [
    ('Формат часов','12 часов (AM/PM)','clock_format','12h','24h'),
    ('Секунды','Без секунд','clock_seconds',False,True),
    ('Вид часов','Увеличенный','clock_layout','tall','stacked'),
    ('Стиль устройства','Орбита','device_style','orbit','minimal'),
])
def test_bot_clock_options_share_api_and_repeated_buttons_do_not_undo_panel_changes(env,label,option,field,value,newer):
    link(env); now=int(time.time())
    _,card=apply(env,'device:own-tv',callback=True,now=now)
    choices=device_options(env,card,label,now)
    button=pet_button(choices,option)
    _,saved=apply(env,button,callback=True,now=now)
    assert 'сохранена на сервере' in saved['text']
    result=env['api'].get('/api/v1/devices/own-tv/settings').json()
    assert result[field]==value and type(result[field]) is type(value)
    assert result['theme']=='BlackArch Red' and result['brightness']==70
    assert env['api'].patch('/api/v1/devices/own-tv/settings',headers=env['csrf'],json={field:newer}).status_code==200
    _,duplicate=apply(env,button,callback=True,now=now)
    assert 'уже была сохранена' in duplicate['text']
    assert env['api'].get('/api/v1/devices/own-tv/settings').json()[field]==newer
    assert env['api'].get('/api/v1/devices/own-tv/presence').json()['state']=='UNKNOWN'


def test_unused_clock_button_detects_concurrent_panel_update(env):
    link(env); now=int(time.time())
    _,card=apply(env,'device:own-tv',callback=True,now=now)
    choices=device_options(env,card,'Вид часов',now)
    button=pet_button(choices,'Увеличенный')
    env['api'].patch('/api/v1/devices/own-tv/settings',headers=env['csrf'],json={'clock_layout':'stacked'})
    _,stale=apply(env,button,callback=True,now=now)
    assert 'уже изменилась' in stale['text']
    assert env['api'].get('/api/v1/devices/own-tv/settings').json()['clock_layout']=='stacked'


def test_clock_button_checks_ownership_and_expiry(env):
    link(env); now=int(time.time())
    _,card=apply(env,'device:own-tv',callback=True,now=now)
    choices=device_options(env,card,'Секунды',now)
    button=pet_button(choices,'Без секунд')
    _,expired=apply(env,button,callback=True,now=now+301)
    assert 'устарела' in expired['text']
    _,card=apply(env,'/device',now=now+302)
    choices=device_options(env,card,'Секунды',now+302)
    with env['db'].transaction() as c:
        c.execute("UPDATE devices SET owner_id=? WHERE id='own-tv'",(env['other'],))
    apply(env,pet_button(choices,'Без секунд'),callback=True,now=now+302)
    assert env['api'].get('/api/v1/devices/own-tv/settings').json()['clock_seconds'] is True


def test_bot_theme_brightness_and_start_screen_share_panel_api_and_do_not_fake_presence(env):
    link(env); now=int(time.time())
    _,card=apply(env,'device:own-tv',callback=True,now=now)
    themes=device_options(env,card,'Тема',now)
    _,card=apply(env,pet_button(themes,'Синяя'),callback=True,now=now)
    levels=device_options(env,card,'Яркость',now)
    _,card=apply(env,pet_button(levels,'0%'),callback=True,now=now)
    screens=device_options(env,card,'Стартовый экран',now)
    _,card=apply(env,pet_button(screens,'Tamagotchi'),callback=True,now=now)
    saved=env['api'].get('/api/v1/devices/own-tv/settings').json()
    assert (saved['theme'],saved['brightness'],saved['start_screen'])==('Dark Blue',0,'tamagotchi')
    assert 'сохранена на сервере' in card['text']
    with env['db'].connection() as c:
        assert c.execute('SELECT count(*) FROM device_presence').fetchone()[0]==0


@pytest.mark.parametrize('value',['101','-1','2.5','text'])
def test_custom_device_brightness_validates_and_then_accepts_exact_percent(env,value):
    link(env); now=int(time.time())
    _,card=apply(env,'device:own-tv',callback=True,now=now)
    levels=device_options(env,card,'Яркость',now)
    apply(env,pet_button(levels,'Своя яркость'),callback=True,now=now)
    _,error=apply(env,value,now=now)
    assert 'целое число' in error['text']
    apply(env,'42',now=now)
    assert env['api'].get('/api/v1/devices/own-tv/settings').json()['brightness']==42


def test_old_consumed_setting_button_does_not_overwrite_a_newer_panel_value(env):
    link(env); now=int(time.time())
    _,card=apply(env,'device:own-tv',callback=True,now=now)
    levels=device_options(env,card,'Яркость',now)
    button=pet_button(levels,'25%')
    update,_=apply(env,button,callback=True,now=now)
    assert env['api'].patch('/api/v1/devices/own-tv/settings',headers=env['csrf'],json={'brightness':60}).status_code==200
    process_update(env['db'],env['settings'],update,now=now)
    _,duplicate=apply(env,button,callback=True,now=now)
    assert 'уже была сохранена' in duplicate['text']
    assert env['api'].get('/api/v1/devices/own-tv/settings').json()['brightness']==60


def test_old_device_button_cannot_retarget_another_selection_or_expire_into_a_write(env):
    link(env); now=int(time.time())
    _,card=apply(env,'device:own-tv',callback=True,now=now)
    themes=device_options(env,card,'Тема',now); button=pet_button(themes,'Синяя')
    with env['db'].transaction() as c:
        c.execute("INSERT INTO devices(id,owner_id,name) VALUES ('own-two',?,'Second')",(env['owner'],))
        c.execute("INSERT INTO device_settings(device_id) VALUES ('own-two')")
    apply(env,'device:own-two',callback=True,now=now)
    apply(env,button,callback=True,now=now)
    apply(env,'device:own-tv',callback=True,now=now)
    _,expired=apply(env,button,callback=True,now=now+301)
    assert 'устарела' in expired['text']
    for did in ('own-tv','own-two'):
        assert env['api'].get(f'/api/v1/devices/{did}/settings').json()['theme']=='BlackArch Red'


def test_device_owner_is_rechecked_on_every_write_even_for_super_admin(env):
    link(env); now=int(time.time())
    _,card=apply(env,'device:own-tv',callback=True,now=now)
    themes=device_options(env,card,'Тема',now); button=pet_button(themes,'Синяя')
    with env['db'].transaction() as c: c.execute("UPDATE devices SET owner_id=? WHERE id='own-tv'",(env['other'],))
    apply(env,button,callback=True,now=now)
    assert env['api'].get('/api/v1/devices/own-tv/settings').json()['theme']=='BlackArch Red'


def test_device_brightness_prompt_expires_and_other_menus_cancel_it(env):
    link(env); now=int(time.time())
    _,card=apply(env,'device:own-tv',callback=True,now=now)
    levels=device_options(env,card,'Яркость',now)
    apply(env,pet_button(levels,'Своя яркость'),callback=True,now=now)
    _,expired=apply(env,'42',now=now+301)
    assert 'устарел' in expired['text']
    _,card=apply(env,'/device',now=now+302)
    levels=device_options(env,card,'Яркость',now+302)
    apply(env,pet_button(levels,'Своя яркость'),callback=True,now=now+302)
    apply(env,'Настройки',now=now+302); apply(env,'42',now=now+302)
    assert env['api'].get('/api/v1/devices/own-tv/settings').json()['brightness']==70


def test_unused_old_setting_button_cannot_overwrite_a_concurrent_panel_change(env):
    link(env); now=int(time.time())
    _,card=apply(env,'device:own-tv',callback=True,now=now)
    levels=device_options(env,card,'Яркость',now)
    button=pet_button(levels,'25%')
    env['api'].patch('/api/v1/devices/own-tv/settings',headers=env['csrf'],json={'brightness':60})
    _,stale=apply(env,button,callback=True,now=now)
    assert 'уже изменилась' in stale['text']
    assert env['api'].get('/api/v1/devices/own-tv/settings').json()['brightness']==60


def test_device_custom_prompt_detects_panel_change(env):
    link(env); now=int(time.time())
    _,card=apply(env,'device:own-tv',callback=True,now=now)
    levels=device_options(env,card,'Яркость',now)
    apply(env,pet_button(levels,'Своя яркость'),callback=True,now=now)
    env['api'].patch('/api/v1/devices/own-tv/settings',headers=env['csrf'],json={'brightness':60})
    _,stale=apply(env,'42',now=now)
    assert 'уже изменилась' in stale['text']
    assert env['api'].get('/api/v1/devices/own-tv/settings').json()['brightness']==60


def test_stolen_device_button_is_bound_to_its_telegram_owner(env):
    link(env); now=int(time.time())
    _,card=apply(env,'device:own-tv',callback=True,now=now)
    themes=device_options(env,card,'Тема',now); button=pet_button(themes,'Синяя')
    with env['db'].transaction() as c:
        c.execute("INSERT INTO telegram_links(telegram_id,user_id,selected_device_id,linked_at) VALUES (202,?,'other-tv',?)",(env['other'],now))
    _,denied=apply(env,button,who=202,callback=True,now=now)
    assert 'own-tv' not in denied['text']
    assert env['api'].get('/api/v1/devices/own-tv/settings').json()['theme']=='BlackArch Red'


def test_device_intents_and_prompts_are_revoked_by_restore_and_unlink(env,tmp_path):
    from backend.app.backups import backup_database, restore_database
    link(env); now=int(time.time())
    _,card=apply(env,'device:own-tv',callback=True,now=now)
    levels=device_options(env,card,'Яркость',now)
    apply(env,pet_button(levels,'Своя яркость'),callback=True,now=now)
    snapshot=tmp_path/'devices.sqlite3'; restored=tmp_path/'devices-restored.sqlite3'
    backup_database(env['db'].path,snapshot)
    restore_database(snapshot,restored,active_database=env['db'].path)
    with Database(restored).connection() as c:
        for table in ('telegram_device_intents','telegram_device_drafts','pet_need_tracking'):
            assert c.execute(f'SELECT count(*) FROM {table}').fetchone()[0]==0
    env['api'].delete('/api/v1/telegram/link',headers=env['csrf'])
    with env['db'].connection() as c:
        for table in ('telegram_device_intents','telegram_device_drafts'):
            assert c.execute(f'SELECT count(*) FROM {table}').fetchone()[0]==0
