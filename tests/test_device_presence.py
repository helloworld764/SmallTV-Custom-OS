from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import secrets

from fastapi.testclient import TestClient
from pydantic import ValidationError
import pytest

from backend.app import device_presence, enrollment
from backend.app.backups import backup_database, restore_database
from backend.app.config import Settings
from backend.app.database import Database, MIGRATIONS
from backend.app.main import create_app
from backend.app.security import create_account


@pytest.fixture
def env(tmp_path, monkeypatch):
    database = Database(tmp_path / 'devices.sqlite3')
    database.initialize()
    password = secrets.token_urlsafe(24)
    owner = create_account(database, 'presence-owner', password, 'Owner')
    settings = Settings(_env_file=None, db_path=database.path, cookie_secure=False)
    clock = [1000]
    monkeypatch.setattr(device_presence, 'now', lambda: clock[0])
    with TestClient(create_app(settings)) as client:
        login = client.post('/api/v1/auth/login', headers={'X-SmallTV-Client': 'web'},
                            json={'username': 'presence-owner', 'password': password})
        assert login.status_code == 200
        yield client, database, {'X-CSRF-Token': login.json()['csrf_token']}, owner, password, clock


def enroll(env):
    client, _, csrf, *_ = env
    code = client.post('/api/v1/devices/enrollment-codes', headers=csrf, json={'name': 'Software heartbeat client'})
    assert code.status_code == 201
    response = client.post('/api/v1/devices/enroll', json={'code': code.json()['code']})
    assert response.status_code == 201
    return response.json()


def path(key, action='presence'):
    return f"/api/v1/devices/{key['device_id']}/{action}"


def send(client, key, payload=None):
    return client.post(path(key, 'heartbeat'), headers={'Authorization': 'Bearer ' + key['token']},
                       json={} if payload is None else payload)


def test_boundaries_server_time_recovery_and_constant_storage(env):
    client, database, _, _, _, clock = env
    key = enroll(env)
    initial = client.get(path(key))
    assert initial.status_code == 200 and initial.headers['cache-control'] == 'no-store'
    assert initial.json() == {'device_id': key['device_id'], 'state': 'UNKNOWN', 'last_seen_at': None,
                              'server_time': 1000, 'stale_after_seconds': 30, 'offline_after_seconds': 90}
    accepted = send(client, key)
    assert accepted.status_code == 204 and accepted.content == b''
    assert accepted.headers['cache-control'] == 'no-store'
    for age, state in [(0, 'ONLINE'), (29, 'ONLINE'), (30, 'STALE'), (89, 'STALE'), (90, 'OFFLINE'), (900, 'OFFLINE')]:
        clock[0] = 1000 + age
        result = client.get(path(key)).json()
        assert result['state'] == state and result['last_seen_at'] == 1000
        assert key['token'] not in str(result) and set(result) == set(initial.json())
    assert send(client, key).status_code == 204
    assert client.get(path(key)).json()['state'] == 'ONLINE'
    with database.connection() as connection:
        assert connection.execute('SELECT COUNT(*) FROM device_presence').fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM audit_log WHERE action LIKE '%heartbeat%'").fetchone()[0] == 0
        assert connection.execute('SELECT COUNT(*) FROM agent_presence').fetchone()[0] == 0


def test_reads_and_backend_liveness_do_not_claim_device_online(env):
    client = env[0]
    first, second = enroll(env), enroll(env)
    assert client.get('/health').status_code == 200
    assert client.get('/simulator/').status_code == 200
    assert client.get(path(first, 'settings')).status_code == 200
    assert client.get(path(first, 'state'), headers={'Authorization': 'Bearer ' + first['token']}).status_code == 200
    assert [client.get(path(key)).json()['state'] for key in (first, second)] == ['UNKNOWN', 'UNKNOWN']
    assert send(client, first).status_code == 204
    assert [client.get(path(key)).json()['state'] for key in (first, second)] == ['ONLINE', 'UNKNOWN']


def test_rate_limit_and_simultaneous_requests_do_not_refresh_twice(env):
    client, database, _, _, _, clock = env
    key = enroll(env)
    with ThreadPoolExecutor(max_workers=2) as pool:
        codes = list(pool.map(lambda _: send(client, key).status_code, range(2)))
    assert sorted(codes) == [204, 429]
    clock[0] = 1004
    rejected = send(client, key)
    assert rejected.status_code == 429 and rejected.headers['retry-after'] == '1'
    assert client.get(path(key)).json()['last_seen_at'] == 1000
    clock[0] = 1005
    assert send(client, key).status_code == 204
    with database.connection() as connection:
        assert connection.execute('SELECT last_seen_at FROM device_presence').fetchone()[0] == 1005


@pytest.mark.parametrize('payload', [{'state': 'ONLINE'}, {'device_id': 'other'}, {'timestamp': 9999999999}, []])
def test_client_cannot_supply_identity_status_or_time(env, payload):
    key = enroll(env)
    assert send(env[0], key, payload).status_code == 422
    assert env[0].get(path(key)).json()['state'] == 'UNKNOWN'


def test_authentication_scope_and_owner_access(env):
    client, database, _, _, password, _ = env
    first, second = enroll(env), enroll(env)
    assert client.post(path(first, 'heartbeat'), json={}).status_code == 401
    assert client.post(path(second, 'heartbeat'), headers={'Authorization': 'Bearer ' + first['token']}, json={}).status_code == 404
    agent = client.post('/api/v1/agents/codex', headers=env[2], json={'name': 'Wrong credential type'}).json()
    assert client.post(path(first, 'heartbeat'), headers={'Authorization': 'Bearer ' + agent['token']}, json={}).status_code == 401
    client.cookies.clear()
    assert client.get(path(first), headers={'Authorization': 'Bearer ' + first['token']}).status_code == 401
    create_account(database, 'presence-other', password, 'Other')
    client.post('/api/v1/auth/login', headers={'X-SmallTV-Client': 'web'},
                json={'username': 'presence-other', 'password': password})
    assert client.get(path(first)).status_code == 404
    assert client.get('/api/v1/devices/missing/presence').status_code == 404
    assert send(client, first).status_code == 204  # Bearer does not inherit the unrelated browser user.


def test_rotation_revoke_disable_and_admin_read(env):
    client, database, csrf, owner, password, _ = env
    key = enroll(env)
    assert send(client, key).status_code == 204
    rotated = client.post(path(key, 'credentials/rotate'), headers=csrf).json()
    assert client.get(path(key)).json()['state'] == 'UNKNOWN'
    assert send(client, key).status_code == 401
    assert send(client, rotated).status_code == 204
    create_account(database, 'presence-admin', password, 'Admin', role='super_admin', first_admin=True)
    login = client.post('/api/v1/auth/login', headers={'X-SmallTV-Client': 'web'},
                        json={'username': 'presence-admin', 'password': password})
    admin_csrf = {'X-CSRF-Token': login.json()['csrf_token']}
    with database.transaction() as connection:
        connection.execute('UPDATE local_accounts SET is_active = 0 WHERE user_id = ?', (owner,))
    assert send(client, rotated).status_code == 401
    assert client.get(path(key)).json()['state'] == 'OFFLINE'
    assert client.post(path(key, 'credentials/revoke'), headers=admin_csrf).status_code == 204
    state = client.get(path(key)).json()
    assert state['state'] == 'OFFLINE' and state['last_seen_at'] is None


@pytest.mark.parametrize('change', ['revoke', 'rotate', 'disable'])
def test_authentication_race_is_rechecked_under_write_lock(env, change):
    client, database, _, owner, _, _ = env
    key = enroll(env)
    def superseded_identity():
        with database.transaction() as connection:
            if change == 'revoke':
                connection.execute('UPDATE device_credentials SET revoked_at = 1 WHERE device_id = ?', (key['device_id'],))
            elif change == 'rotate':
                connection.execute('UPDATE device_credentials SET token_hash = ? WHERE device_id = ?', ('0'*64, key['device_id']))
            else:
                connection.execute('UPDATE local_accounts SET is_active = 0 WHERE user_id = ?', (owner,))
        return key['device_id']
    client.app.dependency_overrides[enrollment.authenticated_device] = superseded_identity
    assert send(client, key).status_code == 401
    with database.connection() as connection:
        assert connection.execute('SELECT COUNT(*) FROM device_presence').fetchone()[0] == 0


def test_restart_custom_timeouts_backward_clock_and_new_heartbeat(env):
    client, database, _, _, _, clock = env
    key = enroll(env)
    assert send(client, key).status_code == 204
    clock[0] = 1020
    with TestClient(create_app(Settings(_env_file=None, db_path=database.path,
                                       device_stale_seconds=10, device_offline_seconds=20))) as restarted:
        restarted.cookies.update(client.cookies)
        result = restarted.get(path(key)).json()
        assert result['state'] == 'OFFLINE' and result['last_seen_at'] == 1000
        assert (result['stale_after_seconds'], result['offline_after_seconds']) == (10, 20)
        clock[0] = 900
        assert restarted.get(path(key)).json()['state'] == 'UNKNOWN'
        assert send(restarted, key).status_code == 204
        assert restarted.get(path(key)).json()['last_seen_at'] == 900


def test_restore_clears_presence_and_revokes_key_without_touching_source(env, tmp_path):
    client, database, *_ = env
    key = enroll(env)
    assert send(client, key).status_code == 204
    snapshot, restored = tmp_path / 'backup.sqlite3', tmp_path / 'restored.sqlite3'
    backup_database(database.path, snapshot)
    restore_database(snapshot, restored, active_database=database.path)
    for location, count in [(database.path, 1), (snapshot, 1), (restored, 0)]:
        with Database(location).connection() as connection:
            assert connection.execute('SELECT COUNT(*) FROM device_presence').fetchone()[0] == count
    with TestClient(create_app(Settings(_env_file=None, db_path=restored))) as recovered:
        assert send(recovered, key).status_code == 401
    assert client.get(path(key)).json()['state'] == 'ONLINE'


def test_upgrade_from_six_migrations_preserves_data_and_old_backup_restores(tmp_path):
    previous = tmp_path / 'previous-migrations'
    previous.mkdir()
    for source in sorted(MIGRATIONS.glob('*.sql'))[:6]:
        (previous / source.name).write_text(source.read_text(encoding='utf-8'), encoding='utf-8')
    database = Database(tmp_path / 'previous.sqlite3', previous)
    database.initialize()
    with database.transaction() as connection:
        connection.execute("INSERT INTO users (id, display_name) VALUES ('u', 'Legacy owner')")
        connection.execute("INSERT INTO devices (id, owner_id, name) VALUES ('d', 'u', 'Legacy device')")
        connection.execute("INSERT INTO device_settings (device_id, brightness) VALUES ('d', 17)")
        original_history = [tuple(row) for row in connection.execute('SELECT * FROM schema_migrations ORDER BY version')]
    snapshot = tmp_path / 'previous-backup.sqlite3'
    backup_database(database.path, snapshot)
    upgraded = Database(database.path)
    upgraded.initialize()
    upgraded.initialize()
    with upgraded.connection() as connection:
        assert connection.execute('SELECT brightness FROM device_settings').fetchone()[0] == 17
        assert [tuple(row) for row in connection.execute('SELECT * FROM schema_migrations WHERE version <= 6 ORDER BY version')] == original_history
        assert connection.execute('SELECT COUNT(*) FROM device_presence').fetchone()[0] == 0
        assert connection.execute('PRAGMA foreign_key_check').fetchall() == []
    restored = tmp_path / 'previous-restored.sqlite3'
    restore_database(snapshot, restored, active_database=database.path)
    with Database(restored).connection() as connection:
        assert connection.execute('SELECT COUNT(*) FROM device_presence').fetchone()[0] == 0
        assert connection.execute('SELECT brightness FROM device_settings').fetchone()[0] == 17


def test_legacy_device_without_key_is_unknown(env):
    client, database, _, owner, *_ = env
    with database.transaction() as connection:
        connection.execute("INSERT INTO devices (id, owner_id, name) VALUES ('legacy', ?, 'Legacy')", (owner,))
    assert client.get('/api/v1/devices/legacy/presence').json()['state'] == 'UNKNOWN'


@pytest.mark.parametrize('stale,offline', [(9, 90), (3601, 4000), (30, 30), (90, 30), (30, 86401)])
def test_invalid_device_timeouts_are_rejected(stale, offline):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, device_stale_seconds=stale, device_offline_seconds=offline)


def test_timeouts_from_environment(monkeypatch):
    monkeypatch.setenv('SMALLTV_DEVICE_STALE_SECONDS', '20')
    monkeypatch.setenv('SMALLTV_DEVICE_OFFLINE_SECONDS', '60')
    settings = Settings(_env_file=None)
    assert (settings.device_stale_seconds, settings.device_offline_seconds) == (20, 60)


def test_device_heartbeat_over_real_loopback_http(env):
    import socket
    from threading import Thread
    import time
    import httpx
    import uvicorn

    _, database, _, _, password, clock = env
    key = enroll(env)
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    listener.listen()
    server = uvicorn.Server(uvicorn.Config(create_app(Settings(_env_file=None, db_path=database.path,
                                                              cookie_secure=False)), log_level='error'))
    thread = Thread(target=server.run, kwargs={'sockets': [listener]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server.started
        with httpx.Client(base_url=f'http://127.0.0.1:{listener.getsockname()[1]}',
                          timeout=3, trust_env=False) as client:
            assert client.post('/api/v1/auth/login', headers={'X-SmallTV-Client': 'web'},
                               json={'username': 'presence-owner', 'password': password}).status_code == 200
            assert client.get(path(key)).json()['state'] == 'UNKNOWN'
            assert client.post(path(key, 'heartbeat'), json={}).status_code == 401
            assert send(client, key).status_code == 204
            assert client.get(path(key)).json()['state'] == 'ONLINE'
            clock[0] = 1090
            assert client.get(path(key)).json()['state'] == 'OFFLINE'
            assert send(client, key).status_code == 204
            assert client.get(path(key)).json()['state'] == 'ONLINE'
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        listener.close()
        assert not thread.is_alive()
