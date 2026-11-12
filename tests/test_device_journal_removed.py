from uuid import uuid4
from test_auth import env,template,csrf,sign_in
from backend.app import features,device_presence
from backend.app.security import token_hash

def test_removed_journal_is_not_available_even_for_administrator(env):
    client=env[0];csrf(env)
    assert features.DEVICE_JOURNAL_ENABLED is False
    assert client.get('/api/v1/devices/alice-tv/journal').status_code==404
    sign_in(env,'admin');assert client.get('/api/v1/devices/alice-tv/journal').status_code==404
    assert not any(path.endswith('/journal') for path in client.get('/openapi.json').json()['paths'])

def test_normal_mutations_and_heartbeat_do_not_write_journal_or_erase_history(env,monkeypatch):
    client,app,_,_=env;headers=csrf(env);key='smalltv_device_'+uuid4().hex
    with app.state.database.transaction() as c:
        c.execute("INSERT INTO device_journal VALUES ('kept','alice-tv','device.renamed',1000,'{}')")
        c.execute("INSERT INTO device_journal_presence VALUES ('alice-tv',1000,30,90,0)")
        c.execute("INSERT INTO device_presence VALUES ('alice-tv',1000)")
        c.execute("INSERT INTO device_credentials VALUES ('alice-tv',?,1000,NULL)",(token_hash(key),))
        old=[tuple(row) for row in c.execute('SELECT * FROM device_journal')]
        anchor=[tuple(row) for row in c.execute('SELECT * FROM device_journal_presence')]
    assert client.patch('/api/v1/devices/alice-tv',headers=headers,json={'name':'Changed'}).status_code==200
    assert client.patch('/api/v1/devices/alice-tv/settings',headers=headers,json={'brightness':55}).status_code==200
    assert client.post('/api/v1/devices/alice-tv/control',headers=headers,json={'request_id':str(uuid4()),'action':'next'}).status_code==200
    monkeypatch.setattr(device_presence,'now',lambda:1005)
    assert client.post('/api/v1/devices/alice-tv/heartbeat',headers={'Authorization':'Bearer '+key},json={}).status_code==204
    with app.state.database.connection() as c:
        assert [tuple(row) for row in c.execute('SELECT * FROM device_journal')]==old
        assert [tuple(row) for row in c.execute('SELECT * FROM device_journal_presence')]==anchor
        assert c.execute('SELECT last_seen_at FROM device_presence').fetchone()[0]==1005
        assert c.execute('SELECT count(*) FROM audit_log').fetchone()[0]>=3
