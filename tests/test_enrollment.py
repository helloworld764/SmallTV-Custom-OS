from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import secrets
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from backend.app import enrollment
from backend.app.backups import backup_database, restore_database
from backend.app.config import Settings
from backend.app.database import Database, MIGRATIONS
from backend.app.main import create_app
from backend.app.security import create_account, token_hash


@pytest.fixture
def env(tmp_path):
    database = Database(tmp_path / 'enrollment.sqlite3')
    database.initialize()
    password = secrets.token_urlsafe(24)
    owner = create_account(database, 'enroll-owner', password, 'Owner')
    stranger = create_account(database, 'enroll-other', password, 'Other')
    app = create_app(Settings(_env_file=None, db_path=database.path, cookie_secure=False))
    with TestClient(app) as client:
        response = client.post('/api/v1/auth/login', headers={'X-SmallTV-Client': 'web'},
                               json={'username': 'enroll-owner', 'password': password})
        assert response.status_code == 200
        yield client, database, {'X-CSRF-Token': response.json()['csrf_token']}, owner, stranger, password


def issue(env):
    response = env[0].post('/api/v1/devices/enrollment-codes', headers=env[2], json={'name': 'Software test client'})
    assert response.status_code == 201
    return response.json()


def redeem(env, code):
    return env[0].post('/api/v1/devices/enroll', json={'code': code})


def device_header(credential):
    return {'Authorization': 'Bearer ' + credential['token']}


def test_preview_creation_is_explicit_private_and_has_no_device_login(env):
    client, database, csrf, owner, _, _ = env
    assert client.get('/api/v1/devices').json() == []
    payload = {'name': '  Local preview  ', 'request_id': str(uuid4())}
    response = client.post('/api/v1/devices/previews', headers=csrf, json=payload)
    assert response.status_code == 201
    assert response.headers['cache-control'] == 'no-store'
    preview = response.json()
    assert preview['owner_id'] == owner and preview['name'] == 'Local preview'
    assert preview['is_preview'] is True and 'token' not in preview
    identifier = preview['id']
    assert client.get(f'/api/v1/devices/{identifier}').json() == preview
    assert client.get('/api/v1/devices').json() == [preview]
    assert client.get(f'/api/v1/devices/{identifier}/presence').json()['state'] == 'UNKNOWN'
    assert client.get(f'/api/v1/devices/{identifier}/state').status_code == 401
    assert client.post(f'/api/v1/devices/{identifier}/credentials/rotate', headers=csrf).status_code == 409
    assert client.patch(f'/api/v1/devices/{identifier}/settings', headers=csrf,
                        json={'start_screen': 'tamagotchi'}).status_code == 200
    with database.connection() as connection:
        assert connection.execute('SELECT COUNT(*) FROM device_credentials').fetchone()[0] == 0
        assert connection.execute('SELECT COUNT(*) FROM device_presence').fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM audit_log WHERE action = 'device.preview.created'").fetchone()[0] == 1


def test_preview_retry_and_concurrent_creation_are_idempotent(env):
    client, database, csrf, *_ = env
    payload = {'name': 'Preview', 'request_id': str(uuid4())}
    def create(_):
        return client.post('/api/v1/devices/previews', headers=csrf, json=payload)
    with ThreadPoolExecutor(max_workers=4) as pool:
        replies = list(pool.map(create, range(4)))
    assert sorted(reply.status_code for reply in replies) == [200, 200, 200, 201]
    assert len({reply.json()['id'] for reply in replies}) == 1
    assert client.post('/api/v1/devices/previews', headers=csrf, json={**payload, 'name': 'Changed'}).status_code == 409
    database.initialize()
    assert create(None).json() == replies[0].json()
    with database.connection() as connection:
        assert connection.execute('SELECT COUNT(*) FROM devices').fetchone()[0] == 1


def test_preview_rename_does_not_break_original_request_retry(env):
    client, _, csrf, *_ = env
    payload = {'name': 'Original preview', 'request_id': str(uuid4())}
    created = client.post('/api/v1/devices/previews', headers=csrf, json=payload).json()
    identifier = created['id']
    renamed = client.patch('/api/v1/devices/' + identifier, headers=csrf, json={'name': 'New name'})
    assert renamed.status_code == 200 and renamed.json()['is_preview'] is True
    retry = client.post('/api/v1/devices/previews', headers=csrf, json=payload)
    assert retry.status_code == 200 and retry.json() == renamed.json()
    assert client.post('/api/v1/devices/previews', headers=csrf, json={**payload, 'name': 'New name'}).status_code == 409
    assert len(client.get('/api/v1/devices').json()) == 1
    assert client.get(f'/api/v1/devices/{identifier}/presence').json()['state'] == 'UNKNOWN'


def test_preview_name_migration_preserves_original_request_on_upgrade(tmp_path):
    old_sources = tmp_path / 'sql-0014'
    old_sources.mkdir()
    for source in sorted(MIGRATIONS.glob('*.sql'))[:14]:
        (old_sources / source.name).write_bytes(source.read_bytes())
    database = Database(tmp_path / 'preview-upgrade.sqlite3', old_sources)
    database.initialize()
    password = secrets.token_urlsafe(24)
    owner = create_account(database, 'upgrade-owner', password, 'Owner')
    identifier, request_id = str(uuid4()), str(uuid4())
    with database.transaction() as connection:
        connection.execute('INSERT INTO devices(id,owner_id,name) VALUES (?,?,?)', (identifier, owner, 'Old preview'))
        connection.execute('INSERT INTO device_settings(device_id) VALUES (?)', (identifier,))
        connection.execute('INSERT INTO device_previews VALUES (?,?,?,?)', (identifier, request_id, owner, 1))
        history = [tuple(row) for row in connection.execute('SELECT * FROM schema_migrations ORDER BY version')]
    upgraded = Database(database.path)
    upgraded.initialize()
    upgraded.initialize()
    with upgraded.connection() as connection:
        assert connection.execute('SELECT request_name FROM device_previews').fetchone()[0] == 'Old preview'
        assert [tuple(row) for row in connection.execute('SELECT * FROM schema_migrations WHERE version <= 14 ORDER BY version')] == history
    with TestClient(create_app(Settings(_env_file=None, db_path=database.path))) as client:
        login = client.post('/api/v1/auth/login', headers={'X-SmallTV-Client': 'web'},
                            json={'username': 'upgrade-owner', 'password': password}).json()
        headers = {'X-CSRF-Token': login['csrf_token']}
        assert client.patch('/api/v1/devices/' + identifier, headers=headers, json={'name': 'Renamed'}).status_code == 200
        retry = client.post('/api/v1/devices/previews', headers=headers, json={'name': 'Old preview', 'request_id': request_id})
        assert retry.status_code == 200 and retry.json()['id'] == identifier


@pytest.mark.parametrize('payload', [
    {'name': ' ', 'request_id': str(uuid4())}, {'name': 'x' * 81, 'request_id': str(uuid4())},
    {'name': 'Test', 'request_id': 'invalid'}, {'name': 'Test'},
    {'name': 'Test', 'request_id': str(uuid4()), 'owner_id': 'other'},
])
def test_preview_rejects_invalid_payload_and_owner_injection(env, payload):
    assert env[0].post('/api/v1/devices/previews', headers=env[2], json=payload).status_code == 422


def test_preview_requires_session_csrf_and_enforces_rate_limit(env, monkeypatch):
    client, _, csrf, *_ = env
    payload = {'name': 'Preview', 'request_id': str(uuid4())}
    assert client.post('/api/v1/devices/previews', json=payload).status_code == 403
    with monkeypatch.context() as patch:
        patch.setattr(enrollment, 'now', lambda: 10000)
        for index in range(5):
            body = payload if index == 0 else {**payload, 'request_id': str(uuid4())}
            assert client.post('/api/v1/devices/previews', headers=csrf, json=body).status_code == 201
        assert client.post('/api/v1/devices/previews', headers=csrf, json=payload).status_code == 200
        limited = client.post('/api/v1/devices/previews', headers=csrf, json={**payload, 'request_id': str(uuid4())})
        assert limited.status_code == 429 and limited.headers['retry-after'] == '60'
        patch.setattr(enrollment, 'now', lambda: 10060)
        assert client.post('/api/v1/devices/previews', headers=csrf, json={**payload, 'request_id': str(uuid4())}).status_code == 201
    client.cookies.clear()
    assert client.post('/api/v1/devices/previews', headers=csrf, json=payload).status_code == 401


def test_enrollment_status_lifecycle_never_reveals_secret(env, monkeypatch):
    client, _, csrf, *_ = env
    code = issue(env)
    path = '/api/v1/devices/enrollment-codes/' + code['id']
    response = client.get(path)
    assert response.status_code == 200 and response.headers['cache-control'] == 'no-store'
    assert response.json()['state'] == 'pending'
    assert 0 < response.json()['expires_in'] <= 300
    assert 'code' not in response.json() and 'token' not in response.json()
    with monkeypatch.context() as patch:
        patch.setattr(enrollment, 'now', lambda: code['expires_at'])
        assert client.get(path).json()['state'] == 'expired'
    credential = redeem(env, code['code']).json()
    status = client.get(path).json()
    assert status['state'] == 'enrolled' and status['device_id'] == credential['device_id']
    assert status['expires_in'] == 0
    # Cancelling after redemption does not undo registration or change its status.
    assert client.delete(path, headers=csrf).status_code == 204
    assert client.get(path).json()['state'] == 'enrolled'
    another = issue(env)
    path = '/api/v1/devices/enrollment-codes/' + another['id']
    assert client.delete(path, headers=csrf).status_code == 204
    assert client.get(path).json()['state'] == 'closed'
    assert client.get(path).json()['device_id'] is None


def test_wizard_isolation_and_request_ids_are_owner_scoped(env):
    client, _, csrf, _, stranger, password = env
    payload = {'name': 'Preview', 'request_id': str(uuid4())}
    preview = client.post('/api/v1/devices/previews', headers=csrf, json=payload).json()
    code = issue(env)
    client.cookies.clear()
    path = '/api/v1/devices/enrollment-codes/' + code['id']
    assert client.get(path).status_code == 401
    session = client.post('/api/v1/auth/login', headers={'X-SmallTV-Client': 'web'},
                          json={'username': 'enroll-other', 'password': password}).json()
    other_csrf = {'X-CSRF-Token': session['csrf_token']}
    assert client.get(path).status_code == 404
    assert client.delete(path, headers=other_csrf).status_code == 404
    assert client.get('/api/v1/devices/' + preview['id']).status_code == 404
    assert client.get('/api/v1/devices').json() == []
    other = client.post('/api/v1/devices/previews', headers=other_csrf, json=payload).json()
    assert other['id'] != preview['id'] and other['owner_id'] == stranger


def test_wizard_migration_preserves_existing_data_and_legacy_consumed_codes(tmp_path):
    old_sources = tmp_path / 'old-migrations'
    old_sources.mkdir()
    for source in sorted(MIGRATIONS.glob('*.sql'))[:13]:
        (old_sources / source.name).write_bytes(source.read_bytes())
    database = Database(tmp_path / 'upgrade.sqlite3', old_sources)
    database.initialize()
    password = secrets.token_urlsafe(24)
    owner = create_account(database, 'upgrade-owner', password, 'Owner')
    with database.transaction() as connection:
        connection.execute("INSERT INTO devices(id, owner_id, name) VALUES ('legacy-device', ?, 'Existing device')", (owner,))
        connection.execute("INSERT INTO device_settings(device_id, brightness) VALUES ('legacy-device', 42)")
        connection.execute('''INSERT INTO device_enrollment_codes
            (id,code_hash,owner_id,device_name,created_at,expires_at,consumed_at)
            VALUES ('legacy-code', ?, ?, 'Existing device', 1, 301, 2)''',
            (token_hash(secrets.token_urlsafe(24)), owner))
        history = [tuple(row) for row in connection.execute('SELECT * FROM schema_migrations ORDER BY version')]
    upgraded = Database(database.path)
    upgraded.initialize()
    upgraded.initialize()
    with upgraded.connection() as connection:
        assert [tuple(row) for row in connection.execute('SELECT * FROM schema_migrations WHERE version <= 13 ORDER BY version')] == history
        assert connection.execute("SELECT brightness FROM device_settings WHERE device_id = 'legacy-device'").fetchone()[0] == 42
        assert connection.execute('SELECT COUNT(*) FROM device_previews').fetchone()[0] == 0
    with TestClient(create_app(Settings(_env_file=None, db_path=database.path))) as client:
        assert client.post('/api/v1/auth/login', headers={'X-SmallTV-Client': 'web'},
                           json={'username': 'upgrade-owner', 'password': password}).status_code == 200
        assert client.get('/api/v1/devices/legacy-device').json()['is_preview'] is False
        result = client.get('/api/v1/devices/enrollment-codes/legacy-code').json()
        assert result['state'] == 'closed' and result['device_id'] is None


def test_complete_lifecycle_and_hash_only_storage(env):
    client, database, csrf, owner, _, _ = env
    code = issue(env)
    assert code['expires_at'] - enrollment.now() <= 300
    response = redeem(env, code['code'])
    assert response.status_code == 201 and response.headers['cache-control'] == 'no-store'
    credential = response.json()
    device_id = credential['device_id']
    state = client.get(f'/api/v1/devices/{device_id}/state', headers=device_header(credential))
    assert state.status_code == 200
    assert state.json() == {'device_id': device_id, 'settings': {'brightness': 70, 'theme': 'BlackArch Red', 'start_screen': 'clock',
                                                             'clock_format': '24h', 'clock_seconds': True, 'clock_style': 'classic', 'device_style': 'classic', 'clock_layout': 'regular'}}
    assert client.patch(f'/api/v1/devices/{device_id}/settings', headers=csrf,
                        json={'clock_format': '12h', 'clock_seconds': False, 'clock_style': 'minimal'}).status_code == 200
    clock_state = client.get(f'/api/v1/devices/{device_id}/state', headers=device_header(credential)).json()['settings']
    assert clock_state['clock_seconds'] is False and clock_state['clock_format'] == '12h' and clock_state['clock_style'] == 'minimal'
    assert client.get(f'/api/v1/devices/{device_id}').json()['owner_id'] == owner
    assert client.patch(f'/api/v1/devices/{device_id}/settings', headers=csrf, json={'brightness': 17}).status_code == 200
    assert client.get(f'/api/v1/devices/{device_id}/state', headers=device_header(credential)).json()['settings']['brightness'] == 17
    assert redeem(env, code['code']).status_code == 400
    with database.connection() as connection:
        assert connection.execute('SELECT code_hash FROM device_enrollment_codes').fetchone()[0] == token_hash(code['code'])
        assert connection.execute('SELECT token_hash FROM device_credentials').fetchone()[0] == token_hash(credential['token'])
        rows = [dict(row) for row in connection.execute('SELECT * FROM audit_log')]
        assert credential['token'] not in str(rows) and code['code'] not in str(rows)
    rotated = client.post(f'/api/v1/devices/{device_id}/credentials/rotate', headers=csrf)
    assert rotated.status_code == 200
    assert client.get(f'/api/v1/devices/{device_id}/state', headers=device_header(credential)).status_code == 401
    assert client.get(f'/api/v1/devices/{device_id}/state', headers=device_header(rotated.json())).status_code == 200
    assert client.post(f'/api/v1/devices/{device_id}/credentials/revoke', headers=csrf).status_code == 204
    assert client.get(f'/api/v1/devices/{device_id}/state', headers=device_header(rotated.json())).status_code == 401


def test_session_and_csrf_required_for_issue_and_foreign_assignment_rejected(env):
    client = env[0]
    assert client.post('/api/v1/devices/enrollment-codes', json={'name': 'Test'}).status_code == 403
    assert client.post('/api/v1/devices/enrollment-codes', headers=env[2], json={'name': 'Test', 'owner_id': env[4]}).status_code == 422
    client.cookies.clear()
    assert client.post('/api/v1/devices/enrollment-codes', headers=env[2], json={'name': 'Test'}).status_code == 401


def test_expired_cancelled_invalid_and_disabled_owner_codes_fail(env, monkeypatch):
    client, database, csrf, owner, _, _ = env
    code = issue(env)
    with monkeypatch.context() as patch:
        patch.setattr(enrollment, 'now', lambda: code['expires_at'])
        assert redeem(env, code['code']).status_code == 400
    assert client.delete('/api/v1/devices/enrollment-codes/' + code['id'], headers=csrf).status_code == 204
    assert redeem(env, code['code']).status_code == 400
    fresh = issue(env)
    with database.transaction() as connection:
        connection.execute('UPDATE local_accounts SET is_active = 0 WHERE user_id = ?', (owner,))
    assert redeem(env, fresh['code']).status_code == 400
    assert redeem(env, 'not-a-valid-code').status_code == 400
    with database.connection() as connection:
        assert connection.execute('SELECT COUNT(*) FROM devices').fetchone()[0] == 0


def test_device_credentials_never_grant_user_or_foreign_device_access(env):
    client = env[0]
    first = redeem(env, issue(env)['code']).json()
    second = redeem(env, issue(env)['code']).json()
    headers = device_header(first)
    client.cookies.clear()
    assert client.get('/api/v1/devices').status_code == 401
    assert client.get('/api/v1/devices', headers=headers).status_code == 401
    assert client.get('/api/v1/auth/session', headers=headers).status_code == 401
    assert client.get(f"/api/v1/devices/{second['device_id']}/state", headers=headers).status_code == 404
    assert client.get(f"/api/v1/devices/{first['device_id']}/state").status_code == 401
    assert client.patch(f"/api/v1/devices/{first['device_id']}/settings", headers=headers, json={'brightness': 5}).status_code == 401


def test_foreign_user_cannot_cancel_rotate_or_revoke(env):
    client, _, _, _, _, password = env
    code = issue(env)
    credential = redeem(env, code['code']).json()
    response = client.post('/api/v1/auth/login', headers={'X-SmallTV-Client': 'web'}, json={'username': 'enroll-other', 'password': password})
    csrf = {'X-CSRF-Token': response.json()['csrf_token']}
    assert client.delete('/api/v1/devices/enrollment-codes/' + code['id'], headers=csrf).status_code == 404
    for action in ('rotate', 'revoke'):
        assert client.post(f"/api/v1/devices/{credential['device_id']}/credentials/{action}", headers=csrf).status_code == 404


def test_concurrent_redemption_creates_exactly_one_device(env):
    code = issue(env)['code']
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: redeem(env, code).status_code, range(2)))
    assert sorted(responses) == [201, 400]
    with env[1].connection() as connection:
        assert connection.execute('SELECT COUNT(*) FROM devices').fetchone()[0] == 1


def test_rate_limits_pending_codes_and_public_attempts(env):
    for _ in range(5):
        issue(env)
    assert env[0].post('/api/v1/devices/enrollment-codes', headers=env[2], json={'name': 'Overflow'}).status_code == 429
    for _ in range(30):
        assert redeem(env, 'invalid').status_code == 400
    assert redeem(env, 'invalid').status_code == 429


def test_restore_revokes_device_tokens_and_unconsumed_codes_only_in_copy(env, tmp_path):
    client, database, _, _, _, _ = env
    credential = redeem(env, issue(env)['code']).json()
    pending = issue(env)
    snapshot, restored = tmp_path / 'copy.sqlite3', tmp_path / 'restored.sqlite3'
    backup_database(database.path, snapshot)
    restore_database(snapshot, restored, active_database=database.path)
    with TestClient(create_app(Settings(_env_file=None, db_path=restored))) as recovered:
        assert recovered.get(f"/api/v1/devices/{credential['device_id']}/state", headers=device_header(credential)).status_code == 401
        assert recovered.post('/api/v1/devices/enroll', json={'code': pending['code']}).status_code == 400
    assert client.get(f"/api/v1/devices/{credential['device_id']}/state", headers=device_header(credential)).status_code == 200


def test_validation_does_not_echo_enrollment_secret(env):
    secret = secrets.token_urlsafe(24)
    response = env[0].post('/api/v1/devices/enroll', json={'code': secret, 'unexpected': True})
    assert response.status_code == 422 and secret not in response.text


def test_owner_cookie_is_not_device_auth_and_key_changes_require_csrf(env):
    client = env[0]
    credential = redeem(env, issue(env)['code']).json()
    device_id = credential['device_id']
    assert client.get(f'/api/v1/devices/{device_id}/state').status_code == 401
    for action in ('rotate', 'revoke'):
        assert client.post(f'/api/v1/devices/{device_id}/credentials/{action}').status_code == 403
    assert client.get(f'/api/v1/devices/{device_id}/state', headers=device_header(credential)).status_code == 200


def test_super_admin_can_revoke_foreign_device(env):
    client, database, _, _, _, password = env
    credential = redeem(env, issue(env)['code']).json()
    create_account(database, 'enroll-admin', password, 'Admin', role='super_admin', first_admin=True)
    response = client.post('/api/v1/auth/login', headers={'X-SmallTV-Client': 'web'},
                           json={'username': 'enroll-admin', 'password': password})
    headers = {'X-CSRF-Token': response.json()['csrf_token']}
    assert client.post(f"/api/v1/devices/{credential['device_id']}/credentials/revoke", headers=headers).status_code == 204
    assert client.get(f"/api/v1/devices/{credential['device_id']}/state", headers=device_header(credential)).status_code == 401
