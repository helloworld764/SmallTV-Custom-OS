from concurrent.futures import ThreadPoolExecutor
import secrets
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from backend.app import auth, pets
from backend.app.backups import backup_database, restore_database
from backend.app.config import Settings
from backend.app.database import Database, MIGRATIONS
from backend.app.main import create_app
from backend.app.security import create_account, token_hash


@pytest.fixture
def env(tmp_path, monkeypatch):
    database = Database(tmp_path / 'pets.sqlite3')
    database.initialize()
    password = secrets.token_urlsafe(24)
    owner = create_account(database, 'pet-owner', password, 'Owner')
    clock = [1000]
    monkeypatch.setattr(pets, 'now', lambda: clock[0])
    with TestClient(create_app(Settings(_env_file=None, db_path=database.path, cookie_secure=False))) as client:
        login = client.post('/api/v1/auth/login', headers={'X-SmallTV-Client': 'web'},
                            json={'username': 'pet-owner', 'password': password})
        assert login.status_code == 200
        yield client, database, {'X-CSRF-Token': login.json()['csrf_token']}, clock, password, owner


def adopt(env, name='Мур', kind='Pixel'):
    response = env[0].post('/api/v1/pets', json={'kind': kind, 'name': name}, headers=env[2])
    assert response.status_code == 201
    return response.json()


def send(env, pet, action='feed', identifier=None, confirmed=False):
    return env[0].post(f"/api/v1/pets/{pet['id']}/actions", headers=env[2],
                       json={'action_id': identifier or str(uuid4()), 'action': action, 'confirm_restart': confirmed})


def current(env):
    response = env[0].get('/api/v1/pets/current')
    assert response.status_code == 200
    return response.json()


def test_sleep_upgrade_preserves_existing_pet_and_uses_original_start(tmp_path):
    import shutil
    legacy=tmp_path/'migrations'
    legacy.mkdir()
    for source in sorted(MIGRATIONS.glob('*.sql'))[:11]: shutil.copyfile(source,legacy/source.name)
    database=Database(tmp_path/'legacy.sqlite3',legacy)
    database.initialize()
    owner=create_account(database,'legacy-owner',secrets.token_urlsafe(24),'Owner')
    with database.transaction() as connection:
        pets.adopt_owned(connection,owner,pets.Adopt(kind='Pixel',name='Legacy'),1000)
        connection.execute('UPDATE pet_states SET sleeping=1,sleep_at=1000,energy=?',(10*pets.engine.UNIT,))
    upgraded=Database(database.path)
    upgraded.initialize()
    with upgraded.connection() as connection:
        row=pets.find_pet(connection,owner)
        assert row['name']=='Legacy' and row['energy']==10*pets.engine.UNIT
        assert pets.view(row,4599).sleeping is True
        assert pets.view(row,4600).energy==100 and pets.view(row,4600).sleeping is False
        session=connection.execute('SELECT started_at,due_at FROM pet_sleep_sessions').fetchone()
        assert tuple(session)==(1000,4600)


def test_adoption_name_catalog_stats_reactions_and_pure_reads(env):
    assert env[0].get('/api/v1/pets/catalog').json() == ['Pixel', 'Byte', 'Glitch']
    pet = adopt(env, '  Мур  ')
    assert pet['name'] == 'Мур' and pet['lifecycle'] == 'ACTIVE'
    assert (pet['satiety'], pet['energy'], pet['mood']) == (80, 80, 80)
    assert pet['reaction']['kind'] == 'ADOPT'
    assert pet['reaction']['duration_ms'] == 3000
    env[3][0] += 60
    assert current(env)['reaction'] is None
    for _ in range(4):
        current(env)
    with env[1].connection() as connection:
        state = connection.execute('SELECT updated_at, last_care_at FROM pet_states').fetchone()
        assert tuple(state) == (1000, 1000)
        assert connection.execute('SELECT COUNT(*) FROM pet_actions').fetchone()[0] == 0
    assert env[0].get('/api/v1/pets/current').headers['cache-control'] == 'no-store'


def test_idempotency_conflict_cooldown_and_no_duplicate_emotion(env):
    pet = adopt(env)
    identifier = str(uuid4())
    first = send(env, pet, identifier=identifier)
    assert first.status_code == 201 and first.json()['duplicate'] is False
    assert first.json()['reaction']['kind'] == 'FEED'
    again = send(env, pet, identifier=identifier)
    assert again.status_code == 200 and again.json()['duplicate'] is True and again.json()['reaction'] is None
    assert send(env, pet, action='play', identifier=identifier).status_code == 409
    env[3][0] = 1059
    assert send(env, pet).headers['retry-after'] == '1'
    env[3][0] = 1060
    assert send(env, pet).status_code == 201
    with env[1].connection() as connection:
        assert connection.execute('SELECT COUNT(*) FROM pet_actions').fetchone()[0] == 2


@pytest.mark.parametrize('same_identifier', [True, False])
def test_simultaneous_actions_are_serialized(env, same_identifier):
    pet = adopt(env)
    identifier = str(uuid4())
    def action(_):
        return send(env, pet, identifier=identifier if same_identifier else None).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        codes = sorted(pool.map(action, range(2)))
    assert codes == ([200, 201] if same_identifier else [201, 429])
    assert current(env)['satiety'] == 100
    with env[1].connection() as connection:
        assert connection.execute('SELECT COUNT(*) FROM pet_actions').fetchone()[0] == 1


def test_neglect_return_confirmation_and_old_action_replay_cannot_restart(env):
    pet = adopt(env)
    for elapsed, state in [(72*3600-1, 'ACTIVE'), (72*3600, 'ABSENT'), (96*3600-1, 'ABSENT'), (96*3600, 'RETURN_AVAILABLE')]:
        env[3][0] = 1000 + elapsed
        assert current(env)['lifecycle'] == state
    assert send(env, pet).status_code == 409
    assert send(env, pet, 'return').status_code == 422
    identifier = str(uuid4())
    assert send(env, pet, 'return', identifier, True).status_code == 201
    restored = current(env)
    assert restored['round'] == 2 and restored['name'] == pet['name'] and restored['id'] == pet['id']
    assert (restored['satiety'], restored['energy'], restored['mood']) == (80, 80, 80)
    env[3][0] += 96*3600
    assert send(env, pet, 'return', identifier, True).json()['duplicate'] is True
    assert current(env)['round'] == 2 and current(env)['lifecycle'] == 'RETURN_AVAILABLE'
    assert env[0].post('/api/v1/pets', headers=env[2], json={'kind': 'Byte', 'name': 'Новый'}).status_code == 409


def test_restart_backward_clock_and_backup_restore_preserve_game(env, tmp_path):
    pet = adopt(env)
    assert send(env, pet, 'play').status_code == 201
    snapshot, restored = tmp_path / 'backup.sqlite3', tmp_path / 'restored.sqlite3'
    backup_database(env[1].path, snapshot)
    restore_database(snapshot, restored, active_database=env[1].path)
    for location in (snapshot, restored, env[1].path):
        with Database(location).connection() as connection:
            assert connection.execute('SELECT mood FROM pet_states').fetchone()[0] == 95*3600
            assert connection.execute('SELECT COUNT(*) FROM pet_actions').fetchone()[0] == 1
    env[3][0] = 900
    with TestClient(create_app(Settings(_env_file=None, db_path=env[1].path))) as restarted:
        restarted.cookies.update(env[0].cookies)
        assert restarted.get('/api/v1/pets/current').json()['clock_state'] == 'UNKNOWN'
    assert send(env, pet).status_code == 409
    with TestClient(create_app(Settings(_env_file=None, db_path=restored))) as recovered:
        recovered.cookies.update(env[0].cookies)
        assert recovered.get('/api/v1/pets/current').status_code == 401


def test_session_csrf_and_owner_isolation_even_for_other_admin(env):
    client, database, csrf, _, password, _ = env
    assert client.post('/api/v1/pets', json={'kind': 'Pixel', 'name': 'Мур'}).status_code == 403
    pet = adopt(env)
    create_account(database, 'pet-admin', password, 'Admin', role='super_admin', first_admin=True)
    login = client.post('/api/v1/auth/login', headers={'X-SmallTV-Client': 'web'},
                        json={'username': 'pet-admin', 'password': password})
    assert client.get('/api/v1/pets/current').status_code == 404
    assert client.post(f"/api/v1/pets/{pet['id']}/actions", headers={'X-CSRF-Token': login.json()['csrf_token']},
                       json={'action_id': str(uuid4()), 'action': 'feed'}).status_code == 404
    assert client.post('/api/v1/pets', headers={'X-CSRF-Token': login.json()['csrf_token']},
                       json={'kind': 'Null', 'name': 'Private'}).status_code == 422
    client.cookies.clear()
    assert client.get('/api/v1/pets/current').status_code == 401
    assert client.get('/api/v1/pets/catalog', headers={'Authorization': 'Bearer fake-device-key'}).status_code == 401


def test_session_recheck_blocks_disable_racing_mutation(env):
    client, database, csrf, _, _, owner = env
    pet = adopt(env)
    principal = auth.Principal(auth.UserView(id=owner, display_name='Owner', role='user', username='pet-owner'),
                               token_hash(client.cookies.get(auth.COOKIE)), csrf['X-CSRF-Token'])
    def disabled():
        with database.transaction() as connection:
            connection.execute('UPDATE local_accounts SET is_active=0 WHERE user_id=?', (owner,))
        return principal
    client.app.dependency_overrides[pets.write_user] = disabled
    assert send(env, pet).status_code == 401
    with database.connection() as connection:
        assert connection.execute('SELECT COUNT(*) FROM pet_actions').fetchone()[0] == 0


@pytest.mark.parametrize('name', ['', '   ', 'a'*25, 'Мур\n', 'М\u200bур'])
def test_invalid_pet_names_do_not_create_rows(env, name):
    response = env[0].post('/api/v1/pets', headers=env[2], json={'kind': 'Pixel', 'name': name})
    assert response.status_code == 422
    with env[1].connection() as connection:
        assert connection.execute('SELECT COUNT(*) FROM pets').fetchone()[0] == 0


def test_bounded_history_does_not_delete_old_actions(env, monkeypatch):
    pet = adopt(env)
    monkeypatch.setattr(pets, 'MAX_ACTIONS', 1)
    assert send(env, pet).status_code == 201
    env[3][0] += 60
    assert send(env, pet).status_code == 429
    assert current(env)['last_care_at'] == 1000


def test_request_cannot_override_owner_time_stats_or_restart_confirmation(env):
    extra = {'kind': 'Pixel', 'name': 'Мур', 'owner_id': 'foreign'}
    assert env[0].post('/api/v1/pets', headers=env[2], json=extra).status_code == 422
    pet = adopt(env, name='  e\u0301  ')
    assert pet['name'] == '\u00e9'
    for extra in ({'occurred_at': 0}, {'satiety': 100}, {'confirm_restart': True}):
        payload = {'action_id': str(uuid4()), 'action': 'feed'} | extra
        assert env[0].post(f"/api/v1/pets/{pet['id']}/actions", headers=env[2], json=payload).status_code == 422
    assert current(env)['satiety'] == 80


def test_seventh_schema_upgrades_and_restores_without_losing_device_data(tmp_path):
    old_sources = tmp_path / 'old-migrations'
    old_sources.mkdir()
    for source in sorted(MIGRATIONS.glob('*.sql'))[:7]:
        (old_sources / source.name).write_text(source.read_text(encoding='utf-8'), encoding='utf-8')
    database = Database(tmp_path / 'old.sqlite3', old_sources)
    database.initialize()
    with database.transaction() as connection:
        connection.execute("INSERT INTO users (id, display_name) VALUES ('u', 'Legacy owner')")
        connection.execute("INSERT INTO devices (id, owner_id, name) VALUES ('d', 'u', 'Legacy display')")
        connection.execute("INSERT INTO device_settings (device_id, brightness) VALUES ('d', 17)")
        connection.execute("INSERT INTO device_presence VALUES ('d', 1000)")
        history = [tuple(row) for row in connection.execute('SELECT * FROM schema_migrations ORDER BY version')]
    snapshot = tmp_path / 'old-backup.sqlite3'
    backup_database(database.path, snapshot)
    upgraded = Database(database.path)
    upgraded.initialize()
    upgraded.initialize()
    with upgraded.connection() as connection:
        assert [tuple(row) for row in connection.execute('SELECT * FROM schema_migrations WHERE version <= 7 ORDER BY version')] == history
        assert connection.execute('SELECT COUNT(*) FROM pets').fetchone()[0] == 0
        assert connection.execute('SELECT brightness FROM device_settings').fetchone()[0] == 17
        assert connection.execute('SELECT last_seen_at FROM device_presence').fetchone()[0] == 1000
        assert connection.execute('PRAGMA foreign_key_check').fetchall() == []
    restored = tmp_path / 'old-restored.sqlite3'
    restore_database(snapshot, restored, active_database=database.path)
    with Database(restored).connection() as connection:
        assert connection.execute('SELECT COUNT(*) FROM pet_states').fetchone()[0] == 0
        assert connection.execute('SELECT brightness FROM device_settings').fetchone()[0] == 17
