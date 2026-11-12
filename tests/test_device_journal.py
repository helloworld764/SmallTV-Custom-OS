import json,sqlite3
from types import SimpleNamespace
from uuid import uuid4
import pytest
from test_auth import env,template,csrf,sign_in
from backend.app import device_journal,device_presence,device_controls
from device_journal_archive import archived_device_journal
pytestmark=pytest.mark.usefixtures('archived_device_journal')

BASE='/api/v1/devices/alice-tv'

@pytest.fixture
def clock(monkeypatch):
    value=[1000]
    monkeypatch.setattr(device_journal,'now',lambda:value[0])
    monkeypatch.setattr(device_presence,'now',lambda:value[0])
    monkeypatch.setattr(device_controls,'time',SimpleNamespace(time=lambda:value[0]))
    return value

def get(client,**params):
    response=client.get(BASE+'/journal',params=params);assert response.status_code==200,response.text
    return response.json()

def device_key(env):
    from backend.app.security import token_hash
    client,app,_,_=env
    key='smalltv_device_'+uuid4().hex
    with app.state.database.transaction() as c:c.execute('INSERT INTO device_credentials VALUES (?,?,1000,NULL)',('alice-tv',token_hash(key)))
    return {'Authorization':'Bearer '+key}

def beat(client,key):return client.post(BASE+'/heartbeat',headers=key,json={})

def test_journal_changes_filters_no_ops_and_no_private_values(env,clock):
    client=env[0];headers=csrf(env);assert get(client)['items']==[]
    assert client.patch(BASE,headers=headers,json={'name':'Private device name'}).status_code==200
    client.patch(BASE,headers=headers,json={'name':'Private device name'})
    clock[0]+=1
    client.patch(BASE+'/settings',headers=headers,json={'brightness':55,'theme':'Purple'})
    client.patch(BASE+'/settings',headers=headers,json={'brightness':55,'theme':'Purple'})
    clock[0]+=1
    command={'request_id':str(uuid4()),'action':'next'}
    assert client.post(BASE+'/control',headers=headers,json=command).status_code==200
    client.post(BASE+'/control',headers=headers,json=command)
    events=get(client)['items'];assert [item['kind'] for item in events]==['screen.changed','settings.changed','device.renamed']
    assert events[0]['details']=={'screen':'weather','action':'next'}
    assert events[1]['details']['fields']==['brightness','theme']
    assert 'Private device name' not in json.dumps(events) and 'owner_id' not in json.dumps(events)
    assert len(get(client,group='settings')['items'])==1 and len(get(client,group='name')['items'])==1
    assert len(get(client,since=1001,until=1001)['items'])==1

def test_owner_admin_session_and_credential_scope(env,clock):
    client=env[0];assert client.get(BASE+'/journal').status_code==401
    headers=csrf(env);client.patch(BASE,headers=headers,json={'name':'Changed'})
    assert client.get('/api/v1/devices/bob-tv/journal').status_code==404
    sign_in(env,'bob');assert client.get(BASE+'/journal').status_code==404
    sign_in(env,'admin');assert get(client)['items'][0]['kind']=='device.renamed'
    key=device_key(env);client.cookies.clear();assert client.get(BASE+'/journal',headers=key).status_code==401

def test_timeout_history_read_only_stable_ids_and_recovery(env,clock):
    client,app,_,_=env;csrf(env);key=device_key(env)
    assert get(client)['items']==[]
    assert beat(client,key).status_code==204
    assert [item['kind'] for item in get(client)['items']]==['connection.online']
    clock[0]=1030;stale=get(client)['items'][0];assert stale['kind']=='connection.stale' and stale['occurred_at']==1030
    clock[0]=1090
    before=[]
    with app.state.database.connection() as c:
        before=list(c.execute('SELECT * FROM device_journal'));seen=c.execute('SELECT last_seen_at FROM device_presence').fetchone()[0]
    page=get(client);assert [item['kind'] for item in page['items']]==['connection.offline','connection.stale','connection.online']
    assert get(client)==page
    with app.state.database.connection() as c:
        assert [tuple(r) for r in c.execute('SELECT * FROM device_journal')]==[tuple(r) for r in before]
        assert c.execute('SELECT last_seen_at FROM device_presence').fetchone()[0]==seen
    clock[0]=1200;assert beat(client,key).status_code==204
    restored=get(client)['items'];assert restored[0]['kind']=='connection.restored'
    assert {item['id'] for item in restored[1:]}=={item['id'] for item in page['items']}
    assert len(restored)==4

def test_normal_and_rejected_heartbeats_do_not_spam_or_claim_connectivity(env,clock):
    client,app,_,_=env;csrf(env);key=device_key(env)
    assert beat(client,key).status_code==204
    assert beat(client,key).status_code==429
    clock[0]=1005;assert beat(client,key).status_code==204
    assert len(get(client)['items'])==1
    assert client.post(BASE+'/heartbeat',json={}).status_code==401
    clock[0]=1035;assert get(client)['items'][0]['kind']=='connection.stale'
    assert beat(client,key).status_code==204
    assert [item['kind'] for item in get(client)['items']]==['connection.restored','connection.stale','connection.online']

def test_pagination_same_timestamp_and_interleaved_new_events(env,clock):
    client=env[0];headers=csrf(env)
    for n in range(8):client.patch(BASE,headers=headers,json={'name':f'Name {n}'})
    first=get(client,limit=3);assert first['next_cursor']
    clock[0]+=1;client.patch(BASE,headers=headers,json={'name':'New head'})
    entries=list(first['items']);cursor=first['next_cursor']
    while cursor:
        page=get(client,limit=3,cursor=cursor);entries+=page['items'];cursor=page['next_cursor']
    assert len(entries)==8 and len({item['id'] for item in entries})==8
    assert len(get(client)['items'])==9

def test_timeout_page_cursor_stays_stable_when_recovery_persists_events(env,clock):
    client=env[0];csrf(env);key=device_key(env);beat(client,key)
    clock[0]=1090;first=get(client,limit=1);assert first['items'][0]['kind']=='connection.offline'
    clock[0]=1200;beat(client,key)
    following=get(client,cursor=first['next_cursor'])
    assert [item['kind'] for item in following['items']]==['connection.stale','connection.online']
    assert get(client)['items'][1]['id']==first['items'][0]['id']

def test_existing_presence_before_journal_does_not_invent_past_events(env,clock):
    client,app,_,_=env;csrf(env);key=device_key(env)
    with app.state.database.transaction() as c:c.execute('INSERT INTO device_presence VALUES (?,?)',('alice-tv',100))
    assert get(client)['items']==[]
    clock[0]=1000;beat(client,key)
    assert [item['kind'] for item in get(client)['items']]==['connection.online']

def test_failed_or_denied_commands_do_not_add_history(env,clock):
    client=env[0];headers=csrf(env)
    assert client.patch(BASE,headers=headers,json={'name':''}).status_code==422
    assert client.patch('/api/v1/devices/bob-tv/settings',headers=headers,json={'brightness':35}).status_code==404
    command={'request_id':str(uuid4()),'action':'next'}
    assert client.post('/api/v1/devices/bob-tv/control',headers=headers,json=command).status_code==404
    assert get(client)['items']==[]

@pytest.mark.parametrize('params',[{'limit':0},{'limit':101},{'group':'secret'},{'since':-1},{'since':20,'until':10},{'cursor':'invalid!'}, {'cursor':'W10'}])
def test_invalid_journal_filters_rejected(env,params):
    csrf(env);assert env[0].get(BASE+'/journal',params=params).status_code==422

def test_rotation_revocation_clock_rollback_and_no_fabricated_online(env,clock):
    client=env[0];headers=csrf(env);key=device_key(env);beat(client,key)
    clock[0]=990;assert beat(client,key).status_code==204
    assert any(item['kind']=='connection.unknown' for item in get(client)['items'])
    clock[0]=995;assert beat(client,key).status_code==204
    assert any(item['kind']=='connection.restored' for item in get(client)['items'])
    client.post(BASE+'/credentials/rotate',headers=headers)
    assert get(client)['items'][0]['kind']=='connection.reset'
    assert beat(client,key).status_code==401
    client.post(BASE+'/credentials/revoke',headers=headers)
    client.post(BASE+'/credentials/revoke',headers=headers)
    assert len([item for item in get(client)['items'] if item['kind']=='connection.revoked'])==1

def test_journal_metadata_is_projected_not_raw_json_or_audit(env,clock):
    client,app,_,_=env;csrf(env)
    with app.state.database.transaction() as c:
        c.execute('INSERT INTO device_journal VALUES (?,?,?,?,?)',('entry','alice-tv','settings.changed',1000,json.dumps({'fields':['theme','sensitive-value'],'token':'must-not-leak','brightness':'secret'})))
    assert get(client)['items'][0]['details']=={'fields':['theme']}
    assert 'must-not-leak' not in json.dumps(get(client))

def test_restore_keeps_history_but_clears_live_journal_anchor(env,clock,tmp_path):
    from backend.app.backups import backup_database,restore_database
    from backend.app.database import Database
    client,app,_,_=env;csrf(env);key=device_key(env);beat(client,key)
    snapshot=tmp_path/'snapshot.sqlite3';target=tmp_path/'restored.sqlite3'
    backup_database(app.state.database.path,snapshot);restore_database(snapshot,target,active_database=app.state.database.path)
    with Database(target).connection() as c:
        assert c.execute('SELECT count(*) FROM device_journal').fetchone()[0]==1
        assert c.execute('SELECT count(*) FROM device_journal_presence').fetchone()[0]==0
