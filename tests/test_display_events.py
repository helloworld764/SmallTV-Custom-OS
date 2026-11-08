import secrets
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from backend.app import codex_events, display_events
from backend.app.config import Settings
from backend.app.database import Database
from backend.app.main import create_app
from backend.app.security import create_account


@pytest.fixture
def env(tmp_path, monkeypatch):
    database = Database(tmp_path / 'display.sqlite3')
    database.initialize()
    password = secrets.token_urlsafe(24)
    owner = create_account(database, 'display-owner', password, 'Owner')
    clock = [1000]
    monkeypatch.setattr(codex_events, 'now', lambda: clock[0])
    monkeypatch.setattr(display_events, 'now', lambda: clock[0])
    with TestClient(create_app(Settings(_env_file=None, db_path=database.path, cookie_secure=False))) as client:
        csrf = login(client, 'display-owner', password)
        agent = client.post('/api/v1/agents/codex', headers=csrf, json={'name': 'Private source name'}).json()
        with database.transaction() as connection:
            connection.execute("INSERT INTO devices (id, owner_id, name) VALUES ('display-device', ?, 'Preview')", (owner,))
        yield client, database, agent, clock, password, owner


def login(client, username, password):
    response = client.post('/api/v1/auth/login', headers={'X-SmallTV-Client': 'web'},
                           json={'username': username, 'password': password})
    assert response.status_code == 200
    return {'X-CSRF-Token': response.json()['csrf_token']}


def complete(env, kind='DONE', occurred_at=None, agent=None):
    client, _, own, clock, *_ = env
    source = agent or own
    headers = {'Authorization': 'Bearer ' + source['token']}
    run = str(uuid4())
    for sequence, event_kind in [(1, 'START'), (2, kind)]:
        payload = {'event_id': str(uuid4()), 'run_id': run, 'sequence': sequence,
                   'kind': event_kind, 'occurred_at': clock[0] if occurred_at is None else occurred_at}
        assert client.post('/api/v1/agents/codex/events', headers=headers, json=payload).status_code == 201
    return payload


def read(env, after=0):
    return env[0].get(f'/api/v1/devices/display-device/notifications?after={after}')


def test_terminal_projection_cursor_duplicate_delivery_and_no_private_fields(env):
    payload = complete(env)
    response = read(env)
    assert response.status_code == 200 and response.headers['cache-control'] == 'no-store'
    page = response.json()
    assert page['device_id'] == 'display-device' and page['server_time'] == 1000
    assert len(page['events']) == 1
    event = page['events'][0]
    assert event == {'id': env[2]['agent_id'] + ':' + payload['event_id'], 'cursor': 2, 'kind': 'DONE', 'ttl_seconds': 60}
    assert page['next_cursor'] == 2
    assert env[2]['token'] not in response.text and 'Private source name' not in response.text
    assert payload['run_id'] not in response.text
    assert read(env, page['next_cursor']).json()['events'] == []
    assert env[0].post('/api/v1/agents/codex/events', headers={'Authorization': 'Bearer ' + env[2]['token']},
                       json=payload).status_code == 200
    assert read(env, page['next_cursor']).json()['events'] == []
    with env[1].connection() as connection:
        assert connection.execute('SELECT COUNT(*) FROM codex_events').fetchone()[0] == 2
        assert connection.execute('SELECT COUNT(*) FROM device_presence').fetchone()[0] == 0
        assert connection.execute('SELECT COUNT(*) FROM agent_presence').fetchone()[0] == 0


@pytest.mark.parametrize('age,remaining', [(0, 60), (59, 1), (60, None), (61, None), (-1, None)])
def test_ttl_exact_boundary_and_backward_clock(env, age, remaining):
    complete(env)
    env[3][0] = 1000 + age
    events = read(env).json()['events']
    assert len(events) == (remaining is not None)
    if events:
        assert events[0]['ttl_seconds'] == remaining


def test_delayed_outbox_does_not_revive_old_notification_and_future_time_adds_no_ttl(env):
    complete(env, occurred_at=900)
    assert read(env).json()['events'] == []
    complete(env, 'ERROR', occurred_at=2000)
    events = read(env).json()['events']
    assert len(events) == 1 and events[0]['kind'] == 'ERROR' and events[0]['ttl_seconds'] == 60


def test_paging_recovery_and_cursor_reset_after_restore(env):
    for _ in range(21):
        complete(env)
    first = read(env).json()
    assert len(first['events']) == 20
    second = read(env, first['next_cursor']).json()
    assert len(second['events']) == 1 and second['next_cursor'] > first['next_cursor']
    assert read(env, second['next_cursor']).json()['events'] == []
    assert read(env, 999999).json() == first  # Stale cursor from another/restored DB.


def test_session_ownership_admin_scope_and_disabled_owner(env):
    client, database, _, _, password, owner = env
    complete(env)
    create_account(database, 'display-other', password, 'Other')
    csrf = login(client, 'display-other', password)
    foreign = client.post('/api/v1/agents/codex', headers=csrf, json={'name': 'Other source'}).json()
    complete(env, 'ERROR', agent=foreign)
    assert read(env).status_code == 404
    create_account(database, 'display-admin', password, 'Admin', role='super_admin', first_admin=True)
    login(client, 'display-admin', password)
    assert [event['kind'] for event in read(env).json()['events']] == ['DONE']
    with database.transaction() as connection:
        connection.execute('UPDATE local_accounts SET is_active = 0 WHERE user_id = ?', (owner,))
    assert read(env).json()['events'] == []
    assert client.get('/api/v1/devices/missing/notifications').status_code == 404
    client.cookies.clear()
    assert read(env).status_code == 401
    assert client.get('/api/v1/devices/display-device/notifications',
                      headers={'Authorization': 'Bearer ' + env[2]['token']}).status_code == 401


@pytest.mark.parametrize('after', ['-1', 'bad', '9007199254740992'])
def test_invalid_cursor_rejected(env, after):
    assert read(env, after).status_code == 422


@pytest.mark.parametrize('kind', ['DONE', 'ERROR', 'INTERRUPTED'])
def test_supported_terminal_kinds(env, kind):
    complete(env, kind)
    assert [event['kind'] for event in read(env).json()['events']] == [kind]
