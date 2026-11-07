from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import secrets

from fastapi.testclient import TestClient
import pytest

from backend.app import enrollment
from backend.app.backups import backup_database, restore_database
from backend.app.config import Settings
from backend.app.database import Database
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
    assert state.json() == {'device_id': device_id, 'settings': {'brightness': 70, 'theme': 'BlackArch Red', 'start_screen': 'clock'}}
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
