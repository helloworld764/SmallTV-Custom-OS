from uuid import uuid4
import pytest
from test_auth import env, template, csrf

def command(client,headers,action,screen=None,key=None,device='alice-tv'):
    body={'request_id':str(key or uuid4()),'action':action}
    if screen is not None:body['screen']=screen
    return client.post(f'/api/v1/devices/{device}/control',headers=headers,json=body)

def test_cycle_home_and_brightness_keep_start_screen(env):
    client,_,_,_=env;headers=csrf(env)
    assert client.patch('/api/v1/devices/alice-tv/settings',headers=headers,json={'start_screen':'clock'}).status_code==200
    for expected in ['weather','tamagotchi','markets','codex','clock']:
        result=command(client,headers,'next').json()
        assert result['settings']['current_screen']==expected
        assert result['settings']['start_screen']=='clock'
        assert result['saved_on_server'] and not result['replayed']
    assert command(client,headers,'previous').json()['settings']['current_screen']=='codex'
    assert command(client,headers,'screen','weather').json()['settings']['current_screen']=='weather'
    assert command(client,headers,'home').json()['settings']['current_screen'] is None
    client.patch('/api/v1/devices/alice-tv/settings',headers=headers,json={'brightness':95})
    assert command(client,headers,'brighter').json()['settings']['brightness']==100
    client.patch('/api/v1/devices/alice-tv/settings',headers=headers,json={'brightness':5})
    assert command(client,headers,'dimmer').json()['settings']['brightness']==0

def test_repeated_request_is_not_applied_again_and_conflicts_are_rejected(env):
    client,_,_,_=env;headers=csrf(env);key=uuid4()
    assert command(client,headers,'next',key=key).status_code==200
    assert command(client,headers,'next').json()['settings']['current_screen']=='tamagotchi'
    replay=command(client,headers,'next',key=key)
    assert replay.json()['replayed']
    assert client.get('/api/v1/devices/alice-tv/settings').json()['current_screen']=='tamagotchi'
    assert command(client,headers,'previous',key=key).status_code==409

def test_controls_require_csrf_and_device_scope(env):
    client,_,_,_=env;headers=csrf(env)
    assert command(client,{},'next').status_code==403
    assert command(client,headers,'next',device='bob-tv').status_code==404
    client.cookies.clear()
    assert command(client,headers,'next').status_code==401


@pytest.mark.parametrize('duplicate',[False,True])
def test_concurrent_controls_are_atomic_and_survive_new_connection(env,duplicate):
    from concurrent.futures import ThreadPoolExecutor
    from backend.app.device_controls import ControlCommand,apply_control
    client,app,users,_=env;csrf(env)
    shared=uuid4()
    def run(index):
        cmd=ControlCommand(request_id=shared if duplicate else uuid4(),action='next')
        with app.state.database.transaction() as c:
            return apply_control(c,'alice-tv',cmd,users['alice'])
    with ThreadPoolExecutor(max_workers=3) as pool:results=list(pool.map(run,range(3)))
    with app.state.database.connection() as c:
        row=c.execute("SELECT current_screen,start_screen FROM device_settings WHERE device_id='alice-tv'").fetchone()
        assert tuple(row)==('weather' if duplicate else 'markets','clock')
        assert c.execute('SELECT count(*) FROM device_control_requests').fetchone()[0]==(1 if duplicate else 3)

@pytest.mark.parametrize('patch',[{'action':'bad'},{'action':'screen'},{'action':'screen','screen':'monitor'},
                                 {'action':'next','screen':'clock'},{'action':'home','extra':1},
                                 {'action':'next','request_id':'invalid'}])
def test_invalid_commands_are_rejected(env,patch):
    client,_,_,_=env;headers=csrf(env)
    body={'request_id':str(uuid4()),**patch}
    assert client.post('/api/v1/devices/alice-tv/control',headers=headers,json=body).status_code==422
