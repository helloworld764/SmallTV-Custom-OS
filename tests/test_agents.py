import secrets

from fastapi.testclient import TestClient
import pytest

from backend.app.backups import backup_database, restore_database
from backend.app.config import Settings
from backend.app.database import Database
from backend.app.main import create_app
from backend.app.security import create_account, token_hash


@pytest.fixture
def env(tmp_path):
    database = Database(tmp_path / 'agents.sqlite3')
    database.initialize()
    password = secrets.token_urlsafe(24)
    owner = create_account(database, 'agent-owner', password, 'Owner')
    other = create_account(database, 'agent-other', password, 'Other')
    settings = Settings(_env_file=None, db_path=database.path, cookie_secure=False)
    with TestClient(create_app(settings)) as client:
        response = client.post('/api/v1/auth/login', headers={'X-SmallTV-Client': 'web'}, json={'username': 'agent-owner', 'password': password})
        assert response.status_code == 200
        yield client, database, {'X-CSRF-Token': response.json()['csrf_token']}, owner, other, password, settings


def register(env):
    response = env[0].post('/api/v1/agents/codex', headers=env[2], json={'name': 'Windows software test agent'})
    assert response.status_code == 201
    return response.json()


def bearer(value):
    return {'Authorization': 'Bearer ' + value['token']}


def test_registration_identity_hash_storage_rotation_and_revoke(env):
    client, database, csrf, owner, _, _, _ = env
    credentials = register(env)
    agent_id = credentials['agent_id']
    identity = client.get('/api/v1/agents/codex/identity', headers=bearer(credentials))
    assert identity.status_code == 200 and identity.headers['cache-control'] == 'no-store'
    assert identity.json() == {'agent_id': agent_id, 'kind': 'codex_windows'}
    assert client.get(f'/api/v1/agents/codex/{agent_id}').json()['owner_id'] == owner
    listing = client.get('/api/v1/agents/codex').json()
    assert [row['id'] for row in listing] == [agent_id]
    assert credentials['token'] not in str(listing) + identity.text
    with database.connection() as connection:
        assert connection.execute('SELECT token_hash FROM agent_credentials').fetchone()[0] == token_hash(credentials['token'])
        assert credentials['token'] not in str([dict(row) for row in connection.execute('SELECT * FROM audit_log')])
    response = client.post(f'/api/v1/agents/codex/{agent_id}/credentials/rotate', headers=csrf)
    assert response.status_code == 200
    rotated = response.json()
    assert rotated['token'] != credentials['token']
    assert client.get('/api/v1/agents/codex/identity', headers=bearer(credentials)).status_code == 401
    assert client.get('/api/v1/agents/codex/identity', headers=bearer(rotated)).status_code == 200
    for _ in range(2):
        assert client.post(f'/api/v1/agents/codex/{agent_id}/credentials/revoke', headers=csrf).status_code == 204
    assert client.get('/api/v1/agents/codex/identity', headers=bearer(rotated)).status_code == 401


def test_management_requires_session_csrf_and_rejects_owner_override(env):
    client = env[0]
    assert client.post('/api/v1/agents/codex', json={'name': 'Test'}).status_code == 403
    assert client.post('/api/v1/agents/codex', headers=env[2], json={'name': 'Test', 'owner_id': env[4]}).status_code == 422
    credentials = register(env)
    for operation in ('rotate', 'revoke'):
        assert client.post(f"/api/v1/agents/codex/{credentials['agent_id']}/credentials/{operation}").status_code == 403
    client.cookies.clear()
    assert client.post('/api/v1/agents/codex', headers=env[2], json={'name': 'Test'}).status_code == 401
    assert client.get('/api/v1/agents/codex').status_code == 401


def test_foreign_owner_cannot_read_or_manage_agent(env):
    client, _, _, _, _, password, _ = env
    credentials = register(env)
    response = client.post('/api/v1/auth/login', headers={'X-SmallTV-Client': 'web'}, json={'username': 'agent-other', 'password': password})
    csrf = {'X-CSRF-Token': response.json()['csrf_token']}
    assert client.get('/api/v1/agents/codex').json() == []
    assert client.get(f"/api/v1/agents/codex/{credentials['agent_id']}").status_code == 404
    for operation in ('rotate', 'revoke'):
        assert client.post(f"/api/v1/agents/codex/{credentials['agent_id']}/credentials/{operation}", headers=csrf).status_code == 404


def test_agent_token_is_not_a_user_or_device_credential(env):
    client = env[0]
    credentials = register(env)
    assert client.get('/api/v1/agents/codex/identity').status_code == 401  # Cookie is not enough.
    client.cookies.clear()
    for route in ('/api/v1/agents/codex', '/api/v1/auth/session', '/api/v1/devices', '/api/v1/devices/other/state'):
        assert client.get(route, headers=bearer(credentials)).status_code == 401
    assert client.post('/api/v1/agents/codex', headers=bearer(credentials), json={'name': 'Escalation'}).status_code == 401


def test_device_token_and_user_cookie_cannot_impersonate_agent(env):
    client = env[0]
    code = client.post('/api/v1/devices/enrollment-codes', headers=env[2], json={'name': 'Test device'}).json()
    device = client.post('/api/v1/devices/enroll', json={'code': code['code']}).json()
    assert client.get('/api/v1/agents/codex/identity', headers=bearer(device)).status_code == 401
    assert client.get('/api/v1/agents/codex/identity', headers={'Authorization': 'Basic invalid'}).status_code == 401


def test_disabling_owner_blocks_existing_agent_credential(env):
    credentials = register(env)
    with env[1].transaction() as connection:
        connection.execute('UPDATE local_accounts SET is_active = 0 WHERE user_id = ?', (env[3],))
    assert env[0].get('/api/v1/agents/codex/identity', headers=bearer(credentials)).status_code == 401


def test_admin_can_manage_all_agents_and_demotion_is_respected(env):
    client, database, _, _, _, password, _ = env
    credentials = register(env)
    admin = create_account(database, 'agent-admin', password, 'Admin', role='super_admin', first_admin=True)
    response = client.post('/api/v1/auth/login', headers={'X-SmallTV-Client': 'web'}, json={'username': 'agent-admin', 'password': password})
    csrf = {'X-CSRF-Token': response.json()['csrf_token']}
    assert len(client.get('/api/v1/agents/codex').json()) == 1
    assert client.post(f"/api/v1/agents/codex/{credentials['agent_id']}/credentials/rotate", headers=csrf).status_code == 200
    with database.transaction() as connection:
        connection.execute("UPDATE users SET role = 'user' WHERE id = ?", (admin,))
    assert client.get('/api/v1/agents/codex').json() == []
    assert client.post(f"/api/v1/agents/codex/{credentials['agent_id']}/credentials/revoke", headers=csrf).status_code == 404


def test_restore_revokes_agent_credentials_without_affecting_original(env, tmp_path):
    client, database, _, _, _, _, _ = env
    credentials = register(env)
    snapshot, restored = tmp_path / 'backup.sqlite3', tmp_path / 'restored.sqlite3'
    backup_database(database.path, snapshot)
    restore_database(snapshot, restored, active_database=database.path)
    with TestClient(create_app(Settings(_env_file=None, db_path=restored))) as recovered:
        assert recovered.get('/api/v1/agents/codex/identity', headers=bearer(credentials)).status_code == 401
    assert client.get('/api/v1/agents/codex/identity', headers=bearer(credentials)).status_code == 200


def test_registration_cap_and_pagination(env):
    for _ in range(20):
        register(env)
    assert env[0].post('/api/v1/agents/codex', headers=env[2], json={'name': 'Overflow'}).status_code == 429
    assert len(env[0].get('/api/v1/agents/codex?limit=5&offset=15').json()) == 5
    assert env[0].get('/api/v1/agents/codex?limit=101').status_code == 422


@pytest.mark.parametrize('name', ['', '   ', 'x' * 81])
def test_invalid_names_cannot_register_agents(env, name):
    assert env[0].post('/api/v1/agents/codex', headers=env[2], json={'name': name}).status_code == 422
    with env[1].connection() as connection:
        assert connection.execute('SELECT COUNT(*) FROM codex_agents').fetchone()[0] == 0
