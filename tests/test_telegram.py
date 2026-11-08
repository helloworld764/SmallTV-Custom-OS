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
