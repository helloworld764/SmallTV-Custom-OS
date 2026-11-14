from uuid import uuid4
from types import SimpleNamespace
import pytest
from test_auth import env,template,csrf,sign_in
from backend.app import device_display,weather,weather_forecast,markets
from backend.app.security import token_hash

@pytest.fixture
def display_env(env,monkeypatch):
    client,app,users,_=env;csrf(env);tokens={}
    with app.state.database.transaction() as c:
        for name in ('alice','bob'):
            key='smalltv_device_'+uuid4().hex;tokens[name]={'Authorization':'Bearer '+key}
            c.execute('INSERT INTO device_credentials VALUES (?,?,1000,NULL)',(name+'-tv',token_hash(key)))
    monkeypatch.setattr(weather,'weather_for_owner',lambda request,owner:{'available':True,'city':'own-city-'+owner,'temperature_c':5})
    monkeypatch.setattr(weather_forecast,'forecast_for_owner',lambda request,owner:{'available':False,'city':'own-city-'+owner})
    monkeypatch.setattr(markets.cache,'read',lambda:{'rows':[],'server_time':1000})
    return env,tokens

def test_device_key_reads_all_screens_without_browser_session(display_env):
    env,keys=display_env;client,app,users,_=env;client.cookies.clear()
    for screen in ('clock','weather','tamagotchi','codex','markets','monitor'):
        response=client.get('/api/v1/devices/alice-tv/display',headers=keys['alice'],params={'screen':screen})
        assert response.status_code==200,response.text
        body=response.json();assert body['schema_version']==1 and body['device_id']=='alice-tv' and body['screen']==screen
        assert len(response.content)<=16384 and response.headers['cache-control']=='no-store'
        assert 'palettes' not in body['settings']['advanced'] and 'owner_id' not in response.text
        assert 'smalltv_device_' not in response.text
    assert client.get('/api/v1/devices/alice-tv/display/events',headers=keys['alice']).json()['events']==[]
    with app.state.database.connection() as c:assert c.execute('SELECT count(*) FROM device_presence').fetchone()[0]==0

def test_owner_is_taken_from_device_not_unrelated_browser_cookie(display_env):
    env,keys=display_env;client,app,users,_=env;headers=csrf(env)
    alice=client.post('/api/v1/pets',headers=headers,json={'kind':'Byte','name':'Alice pet'}).json()
    sign_in(env,'bob')
    response=client.get('/api/v1/devices/alice-tv/display',params={'screen':'tamagotchi','owner_id':users['bob']},headers=keys['alice'])
    assert response.json()['data']['pet']['id']==alice['id']
    weather_data=client.get('/api/v1/devices/alice-tv/display',headers=keys['alice'],params={'screen':'clock'}).json()['data']['weather']
    assert weather_data['city']=='own-city-'+users['alice']
    assert client.get('/api/v1/devices/alice-tv/display',headers=keys['bob']).status_code==404
    assert client.get('/api/v1/devices/alice-tv/display').status_code==401

def test_revocation_during_provider_call_blocks_old_key_response(display_env,monkeypatch):
    env,keys=display_env;client,app,users,_=env
    def revoke(request,owner):
        with app.state.database.transaction() as c:c.execute("UPDATE device_credentials SET revoked_at=1 WHERE device_id='alice-tv'")
        return {'available':True,'city':'must-not-be-delivered'}
    monkeypatch.setattr(weather,'weather_for_owner',revoke)
    response=client.get('/api/v1/devices/alice-tv/display',headers=keys['alice'])
    assert response.status_code==401 and 'must-not-be-delivered' not in response.text

def test_invalid_hidden_screens_cursors_and_payload_budget(display_env,monkeypatch):
    env,keys=display_env;client=env[0];headers=csrf(env)
    client.patch('/api/v1/devices/alice-tv/settings',headers=headers,json={'advanced':{'screens':['clock']}})
    assert client.get('/api/v1/devices/alice-tv/display',headers=keys['alice'],params={'screen':'weather'}).status_code==409
    assert client.get('/api/v1/devices/alice-tv/display',headers=keys['alice'],params={'screen':'unknown'}).status_code==422
    assert client.get('/api/v1/devices/alice-tv/display/events',headers=keys['alice'],params={'after':-1}).status_code==422
    monkeypatch.setattr(weather,'weather_for_owner',lambda *args:{'available':True,'invalid':'x'*17000})
    assert client.get('/api/v1/devices/alice-tv/display',headers=keys['alice']).status_code==503

def test_wallpaper_is_device_scoped_and_game_writes_remain_session_only(display_env):
    from test_wallpapers import picture
    env,keys=display_env;client=env[0];headers=csrf(env)
    image=client.post('/api/v1/devices/alice-tv/wallpapers',headers={**headers,'Content-Type':'image/png'},content=picture()).json()['wallpaper']['id']
    client.cookies.clear()
    from backend.app.raster import inspect
    compact=client.get('/api/v1/devices/alice-tv/display/wallpaper/'+image,headers=keys['alice'])
    assert compact.status_code==200 and inspect(compact.content)['width']==240 and len(compact.content)<=58636
    png=client.get('/api/v1/devices/alice-tv/display/wallpaper/'+image+'?format=png',headers=keys['alice'])
    assert png.status_code==200 and png.headers['content-type']=='image/png'
    assert client.get('/api/v1/devices/bob-tv/display/wallpaper/'+image,headers=keys['bob']).status_code==404
    assert client.post('/api/v1/pets',headers=keys['alice'],json={'kind':'Pixel','name':'No'}).status_code==401

def test_public_compact_assets_need_device_auth_and_preserve_private_null(display_env):
    from backend.app.raster import inspect
    env,keys=display_env;client=env[0]
    path='/api/v1/devices/alice-tv/display/assets/Pixel/neutral'
    assert client.get(path).status_code==401
    assert client.get(path,headers=keys['bob']).status_code==404
    response=client.get(path,headers=keys['alice'])
    assert response.status_code==200 and inspect(response.content)['width']==128
    assert client.get(path.replace('Pixel','Null'),headers=keys['alice']).status_code==404
    headers=csrf(env);client.post('/api/v1/pets',headers=headers,json={'kind':'Byte','name':'Asset pet'})
    body=client.get('/api/v1/devices/alice-tv/display?screen=tamagotchi',headers=keys['alice']).json()
    assert client.get(body['data']['asset'],headers=keys['alice']).status_code==200

def test_software_device_over_real_http_survives_restart_and_refuses_revoked_key(display_env):
    import socket,threading,time,uvicorn
    from contextlib import contextmanager
    from test_release_preparation import load_probe
    env,keys=display_env;client,app,users,_=env;identity=str(uuid4());key='smalltv_device_'+uuid4().hex
    with app.state.database.transaction() as c:
        c.execute('INSERT INTO devices(id,owner_id,name) VALUES(?,?,?)',(identity,users['alice'],'Software test device'))
        c.execute('INSERT INTO device_settings(device_id) VALUES(?)',(identity,))
        c.execute('INSERT INTO device_credentials VALUES(?,?,1000,NULL)',(identity,token_hash(key)))
    sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1];sock.close()
    @contextmanager
    def running():
        server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=port,log_level='critical',access_log=False))
        thread=threading.Thread(target=server.run,daemon=True);thread.start()
        deadline=time.monotonic()+5
        while not server.started and thread.is_alive() and time.monotonic()<deadline:time.sleep(.01)
        assert server.started
        try:yield
        finally:server.should_exit=True;thread.join(5);assert not thread.is_alive()
    probe=load_probe().Probe(f'http://127.0.0.1:{port}',identity,key,timeout=.5)
    assert probe.poll()['status']=='UNKNOWN'
    with running():
        for _ in range(30):assert probe.poll()['status']=='ONLINE'
    assert probe.poll()['status']=='OFFLINE'
    with running():
        assert probe.poll()['status']=='ONLINE'
        with app.state.database.transaction() as c:c.execute('UPDATE device_credentials SET revoked_at=1 WHERE device_id=?',(identity,))
        assert probe.poll()['status']=='OFFLINE'
