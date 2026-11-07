from concurrent.futures import ThreadPoolExecutor
import secrets
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from backend.app import agents, codex_events
from backend.app.backups import backup_database, restore_database
from backend.app.config import Settings
from backend.app.database import Database
from backend.app.main import create_app
from backend.app.security import create_account


@pytest.fixture
def env(tmp_path):
    database = Database(tmp_path / 'events.sqlite3')
    database.initialize()
    password = secrets.token_urlsafe(24)
    owner = create_account(database, 'event-owner', password, 'Owner')
    settings = Settings(_env_file=None, db_path=database.path)
    with TestClient(create_app(settings)) as client:
        login = client.post('/api/v1/auth/login', headers={'X-SmallTV-Client': 'web'},
                            json={'username': 'event-owner', 'password': password})
        assert login.status_code == 200
        csrf = {'X-CSRF-Token': login.json()['csrf_token']}
        credential = client.post('/api/v1/agents/codex', headers=csrf, json={'name': 'Software event test'}).json()
        yield client, database, credential, csrf, owner, password, settings


def event(run_id=None, sequence=1, kind='START', **changes):
    return {'event_id': str(uuid4()), 'run_id': run_id or str(uuid4()),
            'sequence': sequence, 'kind': kind, 'occurred_at': 1000, **changes}


def send(env, payload, credential=None):
    return env[0].post('/api/v1/agents/codex/events', json=payload,
                       headers={'Authorization': 'Bearer ' + (credential or env[2])['token']})


def path(env, run_id=''):
    return f"/api/v1/agents/codex/{env[2]['agent_id']}/runs" + (f'/{run_id}' if run_id else '')


@pytest.mark.parametrize('terminal', ['DONE', 'ERROR', 'INTERRUPTED'])
def test_lifecycle_duplicate_terminal_and_pagination(env, terminal):
    start = event()
    response = send(env, start)
    assert response.status_code == 201 and response.headers['cache-control'] == 'no-store'
    assert response.json() == {key: start[key] for key in ('event_id', 'run_id', 'sequence')} | {'duplicate': False}
    assert env[0].get(path(env, start['run_id'])).json()['last_reported_state'] == 'WORKING'
    working = event(start['run_id'], 2, 'WORKING', occurred_at=900)  # Client clock does not order events.
    final = event(start['run_id'], 3, terminal)
    for payload in (working, final):
        assert send(env, payload).status_code == 201
    for payload in (start, working, final):
        duplicate = send(env, payload)
        assert duplicate.status_code == 200 and duplicate.json()['duplicate'] is True
    run = env[0].get(path(env, start['run_id'])).json()
    assert run['last_reported_state'] == terminal and run['last_sequence'] == 3
    assert env[0].get(path(env)).json() == [run]
    assert env[0].get(path(env) + '?limit=1&offset=1').json() == []
    reports = env[0].get(path(env, start['run_id']) + '/events?after_sequence=1&limit=1').json()
    assert [row['event_id'] for row in reports] == [working['event_id']]
    assert reports[0]['occurred_at'] == 900 and reports[0]['received_at'] > 0
    assert send(env, event(start['run_id'], 4, 'WORKING')).status_code == 409
    with env[1].connection() as connection:
        assert connection.execute('SELECT COUNT(*) FROM codex_events').fetchone()[0] == 3


@pytest.mark.parametrize('change', [dict(run_id=str(uuid4())), {'sequence': 2}, {'kind': 'ERROR'}, {'occurred_at': 1001}])
def test_reusing_event_id_with_different_content_conflicts(env, change):
    start = event()
    assert send(env, start).status_code == 201
    assert send(env, start | change).status_code == 409
    assert env[0].get(path(env, start['run_id'])).json()['last_sequence'] == 1


def test_sequence_gaps_reordering_and_repeated_start_roll_back(env):
    start = event()
    assert send(env, event(start['run_id'], 2, 'DONE')).status_code == 409
    assert env[0].get(path(env)).json() == []
    assert send(env, start).status_code == 201
    for sequence, kind in [(3, 'DONE'), (2, 'START'), (1, 'WORKING')]:
        assert send(env, event(start['run_id'], sequence, kind)).status_code == 409
    assert send(env, event(start['run_id'], 2, 'DONE')).status_code == 201


def test_concurrent_duplicates_and_conflicting_sequence_are_atomic(env):
    start = event()
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: send(env, start), range(2)))
    assert sorted(reply.status_code for reply in responses) == [200, 201]
    finals = [event(start['run_id'], 2, kind) for kind in ('DONE', 'ERROR')]
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda payload: send(env, payload), finals))
    assert sorted(reply.status_code for reply in responses) == [201, 409]
    with env[1].connection() as connection:
        assert connection.execute('SELECT COUNT(*) FROM codex_events').fetchone()[0] == 2


def test_runs_and_agents_are_independent_with_same_uuid(env):
    first = event()
    second = env[0].post('/api/v1/agents/codex', headers=env[3], json={'name': 'Second software agent'}).json()
    assert send(env, first).status_code == send(env, first, second).status_code == 201
    assert send(env, event(first['run_id'], 2, 'DONE')).status_code == 201
    other_run = event()
    assert send(env, other_run).status_code == 201
    other_path = f"/api/v1/agents/codex/{second['agent_id']}/runs/{first['run_id']}"
    assert env[0].get(other_path).json()['last_reported_state'] == 'WORKING'
    assert len(env[0].get(path(env)).json()) == 2


def test_loss_of_heartbeat_restart_and_restore_do_not_finish_run(env, monkeypatch, tmp_path):
    monkeypatch.setattr(codex_events, 'now', lambda: 1000)
    monkeypatch.setattr(agents, 'now', lambda: 1000)
    start = event()
    assert send(env, start).status_code == 201
    auth = {'Authorization': 'Bearer ' + env[2]['token']}
    env[0].post('/api/v1/agents/codex/heartbeat', headers=auth, json={})
    monkeypatch.setattr(agents, 'now', lambda: 2000)
    assert env[0].get(f"/api/v1/agents/codex/{env[2]['agent_id']}/presence").json()['state'] == 'OFFLINE'
    with TestClient(create_app(env[6])) as restarted:
        restarted.cookies.update(env[0].cookies)
        assert restarted.get(path(env, start['run_id'])).json()['last_reported_state'] == 'WORKING'
        assert restarted.post('/api/v1/agents/codex/events', headers=auth, json=start).json()['duplicate'] is True
    snapshot, restored = tmp_path / 'backup.sqlite3', tmp_path / 'restore.sqlite3'
    backup_database(env[1].path, snapshot)
    restore_database(snapshot, restored, active_database=env[1].path)
    with Database(restored).connection() as connection:
        assert connection.execute('SELECT last_reported_state FROM codex_runs').fetchone()[0] == 'WORKING'
        assert connection.execute('SELECT COUNT(*) FROM codex_events').fetchone()[0] == 1
        assert connection.execute('SELECT revoked_at FROM agent_credentials').fetchone()[0] is not None


def test_credentials_owner_and_admin_access(env):
    client, database, credential, csrf, _, password, _ = env
    start = event()
    assert client.post('/api/v1/agents/codex/events', json=start).status_code == 401
    assert send(env, start).status_code == 201
    code = client.post('/api/v1/devices/enrollment-codes', headers=csrf, json={'name': 'Software device'}).json()
    device = client.post('/api/v1/devices/enroll', json={'code': code['code']}).json()
    assert send(env, event(), device).status_code == 401
    client.cookies.clear()
    assert client.get(path(env), headers={'Authorization': 'Bearer ' + credential['token']}).status_code == 401
    create_account(database, 'event-other', password, 'Other')
    create_account(database, 'event-admin', password, 'Admin', role='super_admin', first_admin=True)
    for username, expected in [('event-other', 404), ('event-admin', 200)]:
        assert client.post('/api/v1/auth/login', headers={'X-SmallTV-Client': 'web'},
                           json={'username': username, 'password': password}).status_code == 200
        for route in (path(env), path(env, start['run_id']), path(env, start['run_id']) + '/events'):
            assert client.get(route).status_code == expected
    assert client.get(path(env, str(uuid4()))).status_code == 404
    assert client.get(path(env) + '?limit=101').status_code == 422
    assert client.get(path(env, start['run_id']) + '/events?after_sequence=-1').status_code == 422


def test_revocation_rotation_disabled_owner_and_race(env):
    start = event()
    assert send(env, start).status_code == 201
    credential = env[0].post(f"/api/v1/agents/codex/{env[2]['agent_id']}/credentials/rotate", headers=env[3]).json()
    assert send(env, start).status_code == 401
    assert send(env, start, credential).status_code == 200
    with env[1].transaction() as connection:
        connection.execute('UPDATE local_accounts SET is_active = 0 WHERE user_id = ?', (env[4],))
    assert send(env, event(), credential).status_code == 401
    with env[1].transaction() as connection:
        connection.execute('UPDATE local_accounts SET is_active = 1 WHERE user_id = ?', (env[4],))
    def race():
        with env[1].transaction() as connection:
            connection.execute('UPDATE agent_credentials SET revoked_at = 1 WHERE agent_id = ?', (credential['agent_id'],))
        return credential['agent_id']
    env[0].app.dependency_overrides[agents.authenticated_agent] = race
    assert send(env, event(), credential).status_code == 401
    with env[1].connection() as connection:
        assert connection.execute('SELECT COUNT(*) FROM codex_events').fetchone()[0] == 1


def test_rate_limit_preserves_duplicates_and_recovers(env, monkeypatch):
    clock = [1000]
    monkeypatch.setattr(codex_events, 'now', lambda: clock[0])
    monkeypatch.setattr(codex_events, 'EVENTS_PER_MINUTE', 1)
    start = event()
    assert send(env, start).status_code == 201
    assert send(env, start).status_code == 200
    final = event(start['run_id'], 2, 'DONE')
    limited = send(env, final)
    assert limited.status_code == 429 and limited.headers['retry-after'] == '60'
    assert env[0].get(path(env, start['run_id'])).json()['last_sequence'] == 1
    clock[0] = 1060
    assert send(env, final).status_code == 201


@pytest.mark.parametrize('limit_name', ['MAX_RUNS', 'MAX_EVENTS'])
def test_storage_quotas_do_not_drop_existing_data_or_break_duplicates(env, monkeypatch, limit_name):
    monkeypatch.setattr(codex_events, limit_name, 1)
    start = event()
    assert send(env, start).status_code == 201
    assert send(env, event()).status_code == 429
    assert send(env, start).status_code == 200
    expected = 201 if limit_name == 'MAX_RUNS' else 429
    assert send(env, event(start['run_id'], 2, 'DONE')).status_code == expected


@pytest.mark.parametrize('change', [
    {'event_id': 'invalid'}, {'run_id': 'invalid'}, {'sequence': 0}, {'sequence': True},
    {'sequence': '1'}, {'occurred_at': -1}, {'occurred_at': 1.5}, {'kind': 'IDLE'},
    {'agent_id': 'other'}, {'prompt': 'not accepted'},
])
def test_invalid_event_payload_never_mutates_database(env, change):
    assert send(env, event(**change)).status_code == 422
    with env[1].connection() as connection:
        assert connection.execute('SELECT COUNT(*) FROM codex_runs').fetchone()[0] == 0
        assert connection.execute('SELECT COUNT(*) FROM codex_events').fetchone()[0] == 0
