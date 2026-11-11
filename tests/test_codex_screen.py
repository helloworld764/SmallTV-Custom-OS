from uuid import uuid4
import json
import pytest
from test_display_events import env,login,complete
from backend.app import codex_screen,agents
from backend.app.security import create_account
from backend.app.database import Database,MIGRATIONS

@pytest.fixture(autouse=True)
def display_clock(env,monkeypatch):
    monkeypatch.setattr(codex_screen,'now',lambda:env[3][0]);monkeypatch.setattr(agents,'now',lambda:env[3][0])

def read(env):return env[0].get('/api/v1/devices/display-device/codex')
def beat(env):return env[0].post('/api/v1/agents/codex/heartbeat',json={},headers={'Authorization':'Bearer '+env[2]['token']})

def start(env):
    run=str(uuid4());payload={'event_id':str(uuid4()),'run_id':run,'sequence':1,'kind':'START','occurred_at':env[3][0]}
    assert env[0].post('/api/v1/agents/codex/events',json=payload,headers={'Authorization':'Bearer '+env[2]['token']}).status_code==201
    return run

def test_unknown_is_not_idle_until_fresh_heartbeat(env):
    assert read(env).json()['state']=='UNKNOWN'
    assert beat(env).status_code==204
    data=read(env).json();assert data['state']=='IDLE' and data['run'] is None
    assert env[2]['token'] not in read(env).text
    with env[1].connection() as c:assert c.execute('SELECT count(*) FROM device_presence').fetchone()[0]==0

def test_working_stale_offline_recovery_and_confirmed_done(env):
    beat(env);run=start(env);env[3][0]+=10
    data=read(env).json();assert data['state']=='WORKING' and data['run']['duration_seconds']==10
    env[3][0]=1030;data=read(env).json();assert data['state']=='UNKNOWN' and data['presence']=='STALE'
    assert data['run']['last_reported_state']=='WORKING' and data['run']['duration_seconds'] is None
    env[3][0]=1090;assert read(env).json()['state']=='OFFLINE'
    beat(env);assert read(env).json()['state']=='WORKING'
    env[3][0]+=5
    assert env[0].post('/api/v1/agents/codex/events',json={'event_id':str(uuid4()),'run_id':run,'sequence':2,'kind':'DONE','occurred_at':1095},headers={'Authorization':'Bearer '+env[2]['token']}).status_code==201
    data=read(env).json();assert data['state']=='DONE' and data['run']['duration_seconds']==95
    env[3][0]=1200;data=read(env).json();assert data['state']=='OFFLINE' and data['run']['last_reported_state']=='DONE'

@pytest.mark.parametrize('kind',['DONE','ERROR','INTERRUPTED'])
def test_terminal_state_requires_event_and_no_prompt_fields(env,kind):
    beat(env);complete(env,kind)
    data=read(env).json();assert data['state']==kind and data['run']['last_reported_state']==kind
    assert not {'prompt','answer','task','command_line','token'}&data.keys()

def test_new_run_does_not_resurrect_old_unfinished_run(env):
    beat(env);old=start(env);env[3][0]+=1;complete(env)
    data=read(env).json();assert data['state']=='DONE' and data['run']['run_id']!=old
    env[3][0]+=1;new=start(env);assert read(env).json()['run']['run_id']==new
    env[3][0]+=1
    env[0].post('/api/v1/agents/codex/events',json={'event_id':str(uuid4()),'run_id':old,'sequence':2,'kind':'DONE','occurred_at':1000},headers={'Authorization':'Bearer '+env[2]['token']})
    data=read(env).json();assert data['state']=='WORKING' and data['run']['run_id']==new

def test_revoke_and_backward_clock_are_not_done(env):
    beat(env);start(env);env[3][0]=999
    assert read(env).json()['state']=='UNKNOWN'
    env[3][0]=1001;headers=login(env[0],'display-owner',env[4])
    env[0].post('/api/v1/agents/codex/'+env[2]['agent_id']+'/credentials/revoke',headers=headers)
    assert read(env).json()['state']=='OFFLINE'

def test_snapshot_is_owner_scoped_even_when_caller_has_other_agent(env):
    client,db,*_=env
    create_account(db,'other-screen',env[4],'Other')
    login(client,'other-screen',env[4]);assert read(env).status_code==404
    client.cookies.clear();assert read(env).status_code==401
    assert client.get('/api/v1/devices/display-device/codex',headers={'Authorization':'Bearer '+env[2]['token']}).status_code==401

def test_codex_upgrade_appends_only_to_previously_all_visible_lists(tmp_path):
    old=tmp_path/'old';old.mkdir()
    for file in sorted(MIGRATIONS.glob('*.sql'))[:22]:(old/file.name).write_bytes(file.read_bytes())
    db=Database(tmp_path/'upgrade.sqlite3',old);db.initialize()
    with db.transaction() as c:
        c.execute("INSERT INTO users(id,display_name) VALUES ('owner','Owner')")
        for name,screens in [('all',['weather','clock','markets','tamagotchi']),('hidden',['weather','clock'])]:
            c.execute('INSERT INTO devices(id,owner_id,name) VALUES (?,\'owner\',?)',(name,name))
            c.execute('INSERT INTO device_settings(device_id,advanced) VALUES (?,?)',(name,json.dumps({'screens':screens,'accent':'#abcdef'})))
    Database(db.path).initialize()
    with db.connection() as c:
        rows={r['device_id']:json.loads(r['advanced']) for r in c.execute('SELECT * FROM device_settings')}
        assert rows['all']['screens']==['weather','clock','markets','tamagotchi','codex']
        assert rows['hidden']['screens']==['weather','clock'] and rows['all']['accent']=='#abcdef'
