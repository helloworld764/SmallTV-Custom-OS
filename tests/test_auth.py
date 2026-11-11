from contextlib import closing
import secrets
import sqlite3

from fastapi.testclient import TestClient
import pytest

from backend.app import auth, manage
from backend.app.config import Settings
from backend.app.database import Database, MIGRATIONS
from backend.app.main import create_app
from backend.app.security import create_account, hash_password, token_hash, verify_password


@pytest.fixture(scope='module')
def template(tmp_path_factory):
    database = Database(tmp_path_factory.mktemp('auth-template') / 'template.sqlite3')
    database.initialize()
    password = secrets.token_urlsafe(24)
    users = {}
    for username, role in [('admin', 'super_admin'), ('alice', 'user'), ('bob', 'user')]:
        users[username] = create_account(database, username, password, username, role=role)
    with database.transaction() as connection:
        for username in ('alice', 'bob'):
            device_id = username + '-tv'
            connection.execute('INSERT INTO devices (id, owner_id, name) VALUES (?, ?, ?)',
                               (device_id, users[username], device_id))
            connection.execute('INSERT INTO device_settings (device_id) VALUES (?)', (device_id,))
    return database, users, password


@pytest.fixture
def env(tmp_path, template):
    source, users, password = template
    path = tmp_path / 'auth.sqlite3'
    with source.connection() as origin, closing(sqlite3.connect(path)) as destination:
        origin.backup(destination)
    settings = Settings(_env_file=None, db_path=path)
    app = create_app(settings)
    with TestClient(app) as client:
        yield client, app, users, password


def sign_in(env, username='alice'):
    client, _, _, password = env
    response = client.post('/api/v1/auth/login', headers={'X-SmallTV-Client': 'web'},
                           json={'username': username, 'password': password})
    assert response.status_code == 200, response.text
    return response


def csrf(env):
    return {'X-CSRF-Token': sign_in(env).json()['csrf_token']}


def test_appearance_is_owner_scoped_and_market_api_requires_session(env, monkeypatch):
    client, _, _, _ = env
    assert client.get('/api/v1/markets').status_code == 401
    headers = csrf(env)
    change = {'device_style': 'orbit', 'clock_layout': 'analog', 'start_screen': 'markets'}
    assert client.patch('/api/v1/devices/bob-tv/settings', headers=headers, json=change).status_code == 404
    saved = client.patch('/api/v1/devices/alice-tv/settings', headers=headers, json=change)
    assert saved.status_code == 200
    assert all(saved.json()[k] == v for k,v in change.items())
    from backend.app import markets
    monkeypatch.setattr(markets.cache, 'read', lambda: {'rows': [], 'server_time': 1000})
    assert client.get('/api/v1/markets').status_code == 200


def test_device_rename_preserves_identity_settings_and_records_only_changes(env):
    client, app, users, _ = env
    headers = csrf(env)
    before = client.get('/api/v1/devices/alice-tv').json()
    settings = client.get('/api/v1/devices/alice-tv/settings').json()
    name = 'Шторм на столе <b>1</b>'
    response = client.patch('/api/v1/devices/alice-tv', headers=headers, json={'name': '  ' + name + '  '})
    assert response.status_code == 200 and response.headers['cache-control'] == 'no-store'
    assert response.json() == {**before, 'name': name}
    assert client.get('/api/v1/devices').json()[0]['name'] == name
    assert client.get('/api/v1/devices/alice-tv/settings').json() == settings
    assert client.get('/api/v1/devices/alice-tv/presence').json()['state'] == 'UNKNOWN'
    assert client.patch('/api/v1/devices/alice-tv', headers=headers, json={'name': name}).status_code == 200
    app.state.database.initialize()
    with app.state.database.connection() as connection:
        assert connection.execute("SELECT name FROM devices WHERE id = 'alice-tv'").fetchone()[0] == name
        rows = connection.execute("SELECT actor_id,resource_id FROM audit_log WHERE action = 'device.renamed'").fetchall()
        assert [tuple(row) for row in rows] == [(users['alice'], 'alice-tv')]


@pytest.mark.parametrize('payload', [{}, {'name': ''}, {'name': '   '}, {'name': 'x' * 81},
                                   {'name': None}, {'name': 42}, {'name': 'New', 'owner_id': 'other'}])
def test_device_rename_validates_name_and_rejects_owner_injection(env, payload):
    headers = csrf(env)
    assert env[0].patch('/api/v1/devices/alice-tv', headers=headers, json=payload).status_code == 422
    assert env[0].get('/api/v1/devices/alice-tv').json()['name'] == 'alice-tv'


def test_device_rename_requires_session_csrf_and_ownership(env):
    client = env[0]
    path = '/api/v1/devices/alice-tv'
    assert client.patch(path, json={'name': 'New'}).status_code == 401
    sign_in(env)
    assert client.patch(path, json={'name': 'New'}).status_code == 403
    headers = csrf(env)
    assert client.patch('/api/v1/devices/bob-tv', headers=headers, json={'name': 'New'}).status_code == 404
    assert client.patch('/api/v1/devices/missing', headers=headers, json={'name': 'New'}).status_code == 404
    admin = {'X-CSRF-Token': sign_in(env, 'admin').json()['csrf_token']}
    assert client.patch('/api/v1/devices/bob-tv', headers=admin, json={'name': 'Admin renamed'}).json()['name'] == 'Admin renamed'


def test_login_session_cookie_and_no_plaintext_storage(env):
    client, app, users, password = env
    response = sign_in(env, 'ALICE')
    cookie = response.headers['set-cookie'].lower()
    assert 'httponly' in cookie and 'samesite=strict' in cookie and 'path=/' in cookie
    assert 'max-age=43200' in cookie
    token = client.cookies.get(auth.COOKIE)
    assert token not in response.text and password not in response.text
    assert response.json()['user']['id'] == users['alice']
    session = client.get('/api/v1/auth/session')
    assert session.json() == response.json()
    assert session.headers['cache-control'] == 'no-store'
    with app.state.database.connection() as connection:
        row = connection.execute('SELECT * FROM auth_sessions').fetchone()
        assert row['token_hash'] == token_hash(token) and token != row['token_hash']
        encoded = connection.execute('SELECT password_hash FROM local_accounts LIMIT 1').fetchone()[0]
        assert password not in encoded


@pytest.mark.parametrize('route,method', [
    ('/api/v1/auth/session', 'get'), ('/api/v1/auth/logout', 'post'),
    ('/api/v1/devices', 'get'), ('/api/v1/devices/alice-tv', 'get'),
    ('/api/v1/devices/alice-tv/settings', 'get'),
    ('/api/v1/devices/alice-tv/settings', 'patch'),
])
def test_all_private_routes_require_session(env, route, method):
    response = getattr(env[0], method)(route)
    assert response.status_code == 401
    assert response.headers['cache-control'] == 'no-store'


def test_wrong_and_missing_accounts_have_same_login_error(env):
    client, _, _, password = env
    results = []
    for username in ('alice', 'missing'):
        response = client.post('/api/v1/auth/login', headers={'X-SmallTV-Client': 'web'},
                               json={'username': username, 'password': password + '-wrong'})
        results.append((response.status_code, response.json()))
    assert results[0] == results[1] == (401, {'detail': 'Invalid credentials'})
    assert client.cookies.get(auth.COOKIE) is None


def test_login_csrf_and_origin_are_enforced(env):
    client, _, _, password = env
    body = {'username': 'alice', 'password': password}
    assert client.post('/api/v1/auth/login', json=body).status_code == 403
    for origin in ('https://attacker.invalid', 'null', 'http://testserver:81'):
        response = client.post('/api/v1/auth/login', json=body,
                               headers={'X-SmallTV-Client': 'web', 'Origin': origin})
        assert response.status_code == 403
    assert client.post('/api/v1/auth/login', json=body,
                       headers={'X-SmallTV-Client': 'web', 'Origin': 'http://testserver'}).status_code == 200


def test_validation_errors_never_echo_password(env):
    client, _, _, password = env
    response = client.post('/api/v1/auth/login', headers={'X-SmallTV-Client': 'web'},
                           json={'username': '!', 'password': password, 'role': 'super_admin'})
    assert response.status_code == 422
    assert password not in response.text
    assert all('input' not in item and 'ctx' not in item for item in response.json()['detail'])


def test_logout_revokes_session_in_database(env):
    client = env[0]
    headers = csrf(env)
    old = client.cookies.get(auth.COOKIE)
    assert client.post('/api/v1/auth/logout', headers=headers).status_code == 204
    client.cookies.clear()
    client.cookies.set(auth.COOKIE, old)
    assert client.get('/api/v1/auth/session').status_code == 401


def test_successful_login_rotates_existing_session(env):
    client = env[0]
    sign_in(env)
    old = client.cookies.get(auth.COOKIE)
    sign_in(env)
    assert old != client.cookies.get(auth.COOKIE)
    client.cookies.clear()
    client.cookies.set(auth.COOKIE, old)
    assert client.get('/api/v1/auth/session').status_code == 401


def test_expired_forged_and_inactive_sessions_are_rejected(env, monkeypatch):
    client, app, users, _ = env
    sign_in(env)
    current = auth.now()
    with monkeypatch.context() as patch:
        patch.setattr(auth, 'now', lambda: current + app.state.settings.session_ttl_seconds + 1)
        assert client.get('/api/v1/auth/session').status_code == 401
    with app.state.database.transaction() as connection:
        connection.execute('UPDATE local_accounts SET is_active = 0 WHERE user_id = ?', (users['alice'],))
    assert client.get('/api/v1/auth/session').status_code == 401
    client.cookies.clear()
    client.cookies.set(auth.COOKIE, secrets.token_urlsafe(32))
    assert client.get('/api/v1/auth/session').status_code == 401


def test_inactive_account_cannot_login(env):
    client, app, users, password = env
    with app.state.database.transaction() as connection:
        connection.execute('UPDATE local_accounts SET is_active = 0 WHERE user_id = ?', (users['alice'],))
    response = client.post('/api/v1/auth/login', headers={'X-SmallTV-Client': 'web'},
                           json={'username': 'alice', 'password': password})
    assert response.status_code == 401


def test_login_throttle_survives_new_app_and_expires(env, monkeypatch):
    client, app, _, password = env
    start = auth.now()
    monkeypatch.setattr(auth, 'now', lambda: start)
    for _ in range(5):
        response = client.post('/api/v1/auth/login', headers={'X-SmallTV-Client': 'web'},
                               json={'username': 'missing', 'password': password})
        assert response.status_code == 401
    with TestClient(create_app(app.state.settings)) as restarted:
        response = restarted.post('/api/v1/auth/login', headers={'X-SmallTV-Client': 'web'},
                                  json={'username': 'missing', 'password': password})
        assert response.status_code == 429
        assert response.headers['retry-after'] == '60'
    monkeypatch.setattr(auth, 'now', lambda: start + 61)
    response = client.post('/api/v1/auth/login', headers={'X-SmallTV-Client': 'web'},
                           json={'username': 'missing', 'password': password})
    assert response.status_code == 401


def test_normal_user_only_sees_own_device(env):
    client = env[0]
    headers = csrf(env)
    assert [row['id'] for row in client.get('/api/v1/devices').json()] == ['alice-tv']
    assert client.get('/api/v1/devices/alice-tv').status_code == 200
    for device_id in ('bob-tv', 'not-real'):
        assert client.get(f'/api/v1/devices/{device_id}').status_code == 404
        assert client.get(f'/api/v1/devices/{device_id}/settings').status_code == 404
        assert client.patch(f'/api/v1/devices/{device_id}/settings', headers=headers,
                            json={'brightness': 15}).status_code == 404
    with env[1].state.database.connection() as connection:
        assert connection.execute("SELECT brightness FROM device_settings WHERE device_id = 'bob-tv'").fetchone()[0] == 70


def test_ip_throttle_applies_across_different_usernames(env):
    client, _, _, password = env
    for attempt in range(10):
        response = client.post('/api/v1/auth/login', headers={'X-SmallTV-Client': 'web'},
                               json={'username': f'missing-{attempt}', 'password': password})
        assert response.status_code == 401
    response = client.post('/api/v1/auth/login', headers={'X-SmallTV-Client': 'web'},
                           json={'username': 'another-missing', 'password': password})
    assert response.status_code == 429


def test_csrf_token_from_previous_session_is_rejected(env):
    client = env[0]
    old_csrf = sign_in(env, 'alice').json()['csrf_token']
    sign_in(env, 'bob')
    response = client.patch('/api/v1/devices/bob-tv/settings',
                            headers={'X-CSRF-Token': old_csrf}, json={'brightness': 10})
    assert response.status_code == 403


def test_admin_can_read_and_update_all_devices(env):
    client = env[0]
    response = sign_in(env, 'admin')
    headers = {'X-CSRF-Token': response.json()['csrf_token']}
    assert len(client.get('/api/v1/devices').json()) == 2
    assert client.get('/api/v1/devices/bob-tv').status_code == 200
    response = client.patch('/api/v1/devices/bob-tv/settings', headers=headers, json={'brightness': 33})
    assert response.status_code == 200 and response.json()['brightness'] == 33


def test_csrf_and_cross_origin_settings_writes_are_blocked(env):
    client = env[0]
    valid = csrf(env)
    for headers in ({}, {'X-CSRF-Token': 'wrong'}, {**valid, 'Origin': 'http://attacker.invalid'},
                    {**valid, 'Sec-Fetch-Site': 'cross-site'}):
        assert client.patch('/api/v1/devices/alice-tv/settings', headers=headers,
                            json={'brightness': 1}).status_code == 403
        assert client.post('/api/v1/auth/logout', headers=headers).status_code == 403


def test_settings_update_persists_after_app_restart_with_session(env):
    client, app, _, _ = env
    headers = csrf(env)
    response = client.patch('/api/v1/devices/alice-tv/settings', headers=headers,
                            json={'brightness': 0, 'theme': 'Purple', 'start_screen': 'weather'})
    assert response.status_code == 200
    token = client.cookies.get(auth.COOKIE)
    with TestClient(create_app(app.state.settings)) as restarted:
        restarted.cookies.set(auth.COOKIE, token)
        response = restarted.get('/api/v1/devices/alice-tv/settings')
        assert response.status_code == 200
        assert response.json() == {'device_id': 'alice-tv', 'brightness': 0,
                                   'theme': 'Purple', 'start_screen': 'weather',
                                   'clock_format': '24h', 'clock_seconds': True, 'clock_style': 'classic', 'device_style': 'classic', 'clock_layout': 'regular', 'current_screen': None}


def test_clock_settings_defaults_partial_updates_and_restart(env):
    client, app, _, _ = env
    headers = csrf(env)
    path = '/api/v1/devices/alice-tv/settings'
    before = client.get(path).json()
    assert (before['clock_format'], before['clock_seconds'], before['clock_style']) == ('24h', True, 'classic')
    patch = {'clock_format': '12h', 'clock_seconds': False, 'clock_style': 'terminal'}
    saved = client.patch(path, headers=headers, json=patch)
    assert saved.status_code == 200 and saved.json() == {**before, **patch}
    assert client.patch(path, headers=headers, json={'brightness': 55}).json() == {**before, **patch, 'brightness': 55}
    app.state.database.initialize()
    with app.state.database.connection() as connection:
        row = connection.execute("SELECT clock_seconds,clock_format,clock_style FROM device_settings WHERE device_id='alice-tv'").fetchone()
        assert tuple(row) == (0, '12h', 'terminal')
    assert client.patch('/api/v1/devices/bob-tv/settings', headers=headers, json=patch).status_code == 404
    assert client.patch(path, json=patch).status_code == 403


@pytest.mark.parametrize('patch', [
    {'clock_format': '12'}, {'clock_format': 24}, {'clock_format': None}, {'clock_style': 'custom-code'},
    {'clock_seconds': 0}, {'clock_seconds': 'false'}, {'clock_seconds': None}, {'clock_style': None},
])
def test_clock_settings_reject_invalid_values_atomically(env, patch):
    client = env[0]
    headers = csrf(env)
    path = '/api/v1/devices/alice-tv/settings'
    before = client.get(path).json()
    assert client.patch(path, headers=headers, json={'brightness': 19, **patch}).status_code == 422
    assert client.get(path).json() == before


@pytest.mark.parametrize('body', [
    {}, {'brightness': None}, {'brightness': True}, {'brightness': '40'}, {'brightness': 101},
    {'theme': 'unknown'}, {'start_screen': 'unknown'}, {'owner_id': 'bob'}, {'role': 'super_admin'},
])
def test_settings_validation_and_mass_assignment_are_rejected(env, body):
    client = env[0]
    response = client.patch('/api/v1/devices/alice-tv/settings', headers=csrf(env), json=body)
    assert response.status_code == 422
    assert client.get('/api/v1/devices/alice-tv/settings').json()['brightness'] == 70


def test_current_database_role_is_used_for_each_request(env):
    client, app, users, _ = env
    sign_in(env, 'admin')
    with app.state.database.transaction() as connection:
        connection.execute("UPDATE users SET role = 'user' WHERE id = ?", (users['admin'],))
    assert client.get('/api/v1/devices').json() == []
    assert client.get('/api/v1/devices/alice-tv').status_code == 404


def test_audit_contains_actions_not_secrets(env):
    client, app, _, password = env
    headers = csrf(env)
    token = client.cookies.get(auth.COOKIE)
    client.patch('/api/v1/devices/alice-tv/settings', headers=headers, json={'brightness': 90})
    client.post('/api/v1/auth/logout', headers=headers)
    with app.state.database.connection() as connection:
        rows = [dict(row) for row in connection.execute('SELECT * FROM audit_log')]
    actions = {row['action'] for row in rows}
    assert {'auth.login', 'auth.logout', 'device.settings.updated'} <= actions
    assert password not in str(rows) and token not in str(rows)


def test_secure_cookie_setting(env):
    _, app, _, password = env
    settings = app.state.settings.model_copy(update={'cookie_secure': True})
    with TestClient(create_app(settings), base_url='https://testserver') as client:
        response = client.post('/api/v1/auth/login', headers={'X-SmallTV-Client': 'web'},
                               json={'username': 'alice', 'password': password})
        assert '; secure' in response.headers['set-cookie'].lower()
        assert client.get('/api/v1/auth/session').status_code == 200


def test_password_hash_is_salted_and_verifies_unicode():
    password = 'Тест-' + secrets.token_urlsafe(16)
    first, second = hash_password(password), hash_password(password)
    assert first != second and password not in first
    assert verify_password(password, first)
    assert not verify_password(password + 'x', first)
    assert not verify_password(password, 'broken')


def test_duplicate_username_rolls_back_user_creation(env):
    _, app, _, password = env
    with pytest.raises(sqlite3.IntegrityError):
        create_account(app.state.database, 'ALICE', password, 'Duplicate')
    with app.state.database.connection() as connection:
        assert connection.execute('SELECT COUNT(*) FROM users').fetchone()[0] == 3


def test_bootstrap_cli_is_hidden_and_refuses_second_admin(tmp_path, monkeypatch, capsys):
    path = tmp_path / 'cli.sqlite3'
    monkeypatch.setenv('SMALLTV_DB_PATH', str(path))
    monkeypatch.chdir(tmp_path)
    password = secrets.token_urlsafe(24)
    monkeypatch.setattr(manage.getpass, 'getpass', lambda prompt: password)
    assert manage.main(['create-admin', '--username', 'Owner']) == 0
    with pytest.raises(SystemExit) as error:
        manage.main(['create-admin', '--username', 'another-admin'])
    assert error.value.code == 1
    with Database(path).connection() as connection:
        assert connection.execute('SELECT COUNT(*) FROM users').fetchone()[0] == 1
        assert connection.execute('SELECT username FROM local_accounts').fetchone()[0] == 'owner'
    output = capsys.readouterr()
    assert password not in output.out + output.err


def test_bootstrap_password_mismatch_writes_nothing(tmp_path, monkeypatch):
    path = tmp_path / 'cli.sqlite3'
    monkeypatch.setenv('SMALLTV_DB_PATH', str(path))
    inputs = iter([secrets.token_urlsafe(24), secrets.token_urlsafe(24)])
    monkeypatch.setattr(manage.getpass, 'getpass', lambda prompt: next(inputs))
    with pytest.raises(SystemExit):
        manage.main(['create-admin', '--username', 'owner'])
    assert not path.exists()


def test_migration_from_previous_registry_preserves_users_and_devices(tmp_path):
    migrations = tmp_path / 'old-migrations'
    migrations.mkdir()
    first = MIGRATIONS / '0001_registry.sql'
    (migrations / first.name).write_bytes(first.read_bytes())
    database = Database(tmp_path / 'upgrade.sqlite3', migrations)
    database.initialize()
    with database.transaction() as connection:
        connection.execute("INSERT INTO users (id, display_name) VALUES ('legacy', 'Existing')")
        connection.execute("INSERT INTO devices (id, owner_id, name) VALUES ('old-tv', 'legacy', 'TV')")
    upgraded = Database(database.path)
    upgraded.initialize()
    with upgraded.connection() as connection:
        assert connection.execute('SELECT owner_id FROM devices').fetchone()[0] == 'legacy'
        assert connection.execute('SELECT COUNT(*) FROM schema_migrations').fetchone()[0] == len(list(MIGRATIONS.glob('*.sql')))
        assert connection.execute('SELECT COUNT(*) FROM local_accounts').fetchone()[0] == 0
