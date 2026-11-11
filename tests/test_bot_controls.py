import time
from test_telegram import env, apply, link, pet_button

def test_remote_select_cycle_duplicate_and_home(env):
    link(env);now=int(time.time())
    _,initial=apply(env,'Управление',now=now)
    assert 'Выберите устройство' in initial['text']
    _,card=apply(env,'remote-device:own-tv',callback=True,now=now)
    assert 'UNKNOWN' in card['text']
    next_button=pet_button(card,'Вперёд ▶️')
    _,weather=apply(env,next_button,callback=True,now=now)
    state=env['api'].get('/api/v1/devices/own-tv/settings').json()
    assert state['current_screen']=='weather' and state['start_screen']=='clock'
    apply(env,next_button,callback=True,now=now)
    assert env['api'].get('/api/v1/devices/own-tv/settings').json()['current_screen']=='weather'
    _,bright=apply(env,pet_button(weather,'🔆 Ярче'),callback=True,now=now)
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
