import time
import pytest
from test_telegram import env, apply, link, pet_button

@pytest.fixture(autouse=True)
def configured_bot(env):
    with env['db'].transaction() as c: c.execute('UPDATE telegram_worker_state SET bot_id=42')

def test_remote_select_cycle_duplicate_and_home(env):
    link(env);now=int(time.time())
    _,initial=apply(env,'Управление',now=now)
    assert 'Выберите устройство' in initial['text']
    _,card=apply(env,'remote-device:own-tv',callback=True,now=now)
    assert 'UNKNOWN' in card['text']
    next_button=pet_button(card,'Вперёд ▶️')
    update,weather=apply(env,next_button,callback=True,now=now)
    state=env['api'].get('/api/v1/devices/own-tv/settings').json()
    assert state['current_screen']=='weather' and state['start_screen']=='clock'
    from backend.app.bot_handler import process_update
    process_update(env['db'],env['settings'],update,now=now)
    assert env['api'].get('/api/v1/devices/own-tv/settings').json()['current_screen']=='weather'
    _,second=apply(env,next_button,callback=True,now=now)
    assert env['api'].get('/api/v1/devices/own-tv/settings').json()['current_screen']=='tamagotchi'
    _,bright=apply(env,pet_button(second,'🔆 Ярче'),callback=True,now=now)
    assert env['api'].get('/api/v1/devices/own-tv/settings').json()['brightness']==80
    apply(env,pet_button(bright,'🏠 Главный экран'),callback=True,now=now)
    assert env['api'].get('/api/v1/devices/own-tv/settings').json()['current_screen'] is None

def test_remote_checks_owner_selection_and_expiry(env):
    link(env);now=int(time.time())
    _,card=apply(env,'remote-device:own-tv',callback=True,now=now)
    token=pet_button(card,'Вперёд ▶️')
    with env['db'].transaction() as c:
        c.execute("INSERT INTO telegram_links(telegram_id,user_id,selected_device_id,linked_at) VALUES (202,?,'other-tv',?)",(env['other'],now))
    _,denied=apply(env,token,who=202,callback=True,now=now)
    assert 'устарела' in denied['text']
    _,expired=apply(env,token,callback=True,now=now+301)
    assert 'устарела' in expired['text']
    assert env['api'].get('/api/v1/devices/own-tv/settings').json()['current_screen'] is None

def test_remote_escapes_city_and_pet_name_drafts_without_geocoding(env,monkeypatch):
    link(env)
    apply(env,'city')
    from backend.app import weather
    monkeypatch.setattr(weather,'geocode',lambda *args: (_ for _ in ()).throw(AssertionError('Should not geocode menu label')))
    _,reply=apply(env,'Управление')
    assert 'Выберите устройство' in reply['text']


def test_remote_edits_same_card_and_acknowledges_before_processing(env):
    from test_telegram import FakeTelegram, message
    from backend.app.bot_worker import deliver_replies, poll_once
    link(env)
    fake=FakeTelegram();deliver_replies(env['db'],fake)
    _,card=apply(env,'remote-device:own-tv',callback=True)
    assert card['edit_message_id']==77
    deliver_replies(env['db'],fake)
    assert fake.calls[-1][0]=='editMessageText' and fake.calls[-1][1]['message_id']==77
    class Ordered(FakeTelegram):
        def call(self,method,payload=None):
            if method=='answerCallbackQuery':
                assert env['api'].get('/api/v1/devices/own-tv/settings').json()['current_screen'] is None
            if method=='editMessageText':
                assert env['api'].get('/api/v1/devices/own-tv/settings').json()['current_screen']=='weather'
            return super().call(method,payload)
    fake=Ordered([message(env,pet_button(card,'Вперёд ▶️'),callback=True)])
    poll_once(env['db'],env['settings'],fake)
    methods=[method for method,_ in fake.calls]
    assert methods.index('answerCallbackQuery') < methods.index('editMessageText')
    assert 'sendMessage' not in methods


@pytest.mark.parametrize('status',[400,429,500])
def test_edit_failures_do_not_create_new_messages(env,status):
    from test_telegram import FakeTelegram
    from backend.app.bot_worker import deliver_replies
    from backend.app.bot_transport import TelegramError
    link(env);fake=FakeTelegram();deliver_replies(env['db'],fake)
    with env['db'].transaction() as c:c.execute('INSERT INTO telegram_preferences(user_id,enabled) VALUES (?,1)',(env['owner'],))
    apply(env,'remote-device:own-tv',callback=True)
    class FailedEdit(FakeTelegram):
        def call(self,method,payload=None):
            self.calls.append((method,payload))
            if method=='editMessageText':raise TelegramError(status)
            return True
    failure=FailedEdit()
    if status==400:deliver_replies(env['db'],failure)
    else:
        with pytest.raises(TelegramError):deliver_replies(env['db'],failure)
    assert all(method!='sendMessage' for method,_ in failure.calls)
    with env['db'].connection() as c:
        assert c.execute('SELECT enabled FROM telegram_preferences').fetchone()[0]==1
        assert c.execute('SELECT count(*) FROM telegram_replies').fetchone()[0]==(0 if status==400 else 1)
    deliver_replies(env['db'],FakeTelegram())


@pytest.mark.parametrize('change',['missing_id','wrong_bot','non_bot','invalid_id'])
def test_edit_target_must_be_trusted_bot_message_in_private_chat(env,change):
    from test_telegram import message
    from backend.app.bot_handler import process_update
    link(env);_,card=apply(env,'remote-device:own-tv',callback=True)
    update=message(env,pet_button(card,'Вперёд ▶️'),callback=True)
    msg=update['callback_query']['message']
    if change=='missing_id':del msg['message_id']
    if change=='wrong_bot':msg['from']['id']=99
    if change=='non_bot':msg['from']['is_bot']=False
    if change=='invalid_id':msg['message_id']=0
    assert process_update(env['db'],env['settings'],update) is None
    assert env['api'].get('/api/v1/devices/own-tv/settings').json()['current_screen'] is None
