from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3

from fastapi.testclient import TestClient
import pytest

from backend.app.config import Settings
from backend.app.database import Database, MIGRATIONS, MigrationError
from backend.app.main import create_app


@pytest.fixture
def database(tmp_path):
    database = Database(tmp_path / 'данные' / 'smalltv.sqlite3')
    database.initialize()
    return database


def seed(connection):
    connection.execute("INSERT INTO users (id, display_name) VALUES (?, ?)", ('u1', 'Владелец'))
    connection.execute(
        "INSERT INTO devices (id, owner_id, name) VALUES (?, ?, ?)", ('d1', 'u1', 'SmallTV')
    )
    connection.execute("INSERT INTO device_settings (device_id) VALUES (?)", ('d1',))


def migration_copy(tmp_path):
    directory = tmp_path / 'migrations'
    directory.mkdir()
    for source in MIGRATIONS.glob('*.sql'):
        (directory / source.name).write_text(source.read_text(encoding='utf-8'), encoding='utf-8')
    return directory


def test_initialize_creates_empty_registry_and_is_idempotent(database):
    database.initialize()
    with database.connection() as connection:
        assert connection.execute('SELECT COUNT(*) FROM schema_migrations').fetchone()[0] == 1
        for table in ('users', 'devices', 'device_settings'):
            assert connection.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] == 0
        assert connection.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        assert connection.execute('PRAGMA foreign_key_check').fetchall() == []


def test_connections_enforce_foreign_keys_and_full_synchronous(database):
    for _ in range(2):
        with database.connection() as connection:
            assert connection.execute('PRAGMA foreign_keys').fetchone()[0] == 1
            assert connection.execute('PRAGMA synchronous').fetchone()[0] == 2
            assert connection.execute('PRAGMA busy_timeout').fetchone()[0] == 5000


def test_records_and_settings_survive_reopen(database):
    with database.transaction() as connection:
        seed(connection)
        connection.execute("UPDATE device_settings SET brightness = 25 WHERE device_id = ?", ('d1',))
    reopened = Database(database.path)
    reopened.initialize()
    with reopened.connection() as connection:
        assert connection.execute('SELECT display_name FROM users').fetchone()[0] == 'Владелец'
        row = connection.execute('SELECT * FROM device_settings').fetchone()
        assert (row['brightness'], row['theme'], row['start_screen']) == (25, 'BlackArch Red', 'clock')


def test_startup_migrates_and_restart_preserves_data(tmp_path):
    path = tmp_path / 'application.sqlite3'
    settings = Settings(_env_file=None, db_path=path)
    app = create_app(settings)
    assert not path.exists()  # Creating an app object alone does not write a DB.
    with TestClient(app) as client:
        assert client.get('/health').json() == {'status': 'ok'}
        with app.state.database.transaction() as connection:
            seed(connection)
    restarted = create_app(settings)
    with TestClient(restarted) as client:
        assert client.get('/health').status_code == 200
        assert client.get('/simulator/').status_code == 200
        with restarted.state.database.connection() as connection:
            assert connection.execute('SELECT owner_id FROM devices').fetchone()[0] == 'u1'
        # The new DB must never become a public static asset.
        assert client.get('/simulator/%2e%2e/%2e%2e/application.sqlite3').status_code == 404


@pytest.mark.parametrize('sql', [
    "INSERT INTO devices (id, owner_id, name) VALUES ('orphan', 'missing', 'TV')",
    "INSERT INTO device_settings (device_id) VALUES ('missing')",
    "DELETE FROM users WHERE id = 'u1'",
    "DELETE FROM devices WHERE id = 'd1'",
    "INSERT INTO users (id, display_name) VALUES ('u1', 'Duplicate')",
    "INSERT INTO device_settings (device_id) VALUES ('d1')",
    "UPDATE users SET role = 'root' WHERE id = 'u1'",
    "UPDATE users SET display_name = '  ' WHERE id = 'u1'",
    "UPDATE devices SET name = '' WHERE id = 'd1'",
    "UPDATE device_settings SET brightness = -1 WHERE device_id = 'd1'",
    "UPDATE device_settings SET brightness = 101 WHERE device_id = 'd1'",
    "UPDATE device_settings SET brightness = 5.5 WHERE device_id = 'd1'",
    "UPDATE device_settings SET start_screen = 'invalid' WHERE device_id = 'd1'",
])
def test_database_rejects_invalid_relationships_and_settings(database, sql):
    with database.transaction() as connection:
        seed(connection)
    with pytest.raises(sqlite3.IntegrityError):
        with database.transaction() as connection:
            connection.execute(sql)
    with database.connection() as connection:
        assert connection.execute('SELECT COUNT(*) FROM devices').fetchone()[0] == 1
        assert connection.execute('SELECT brightness FROM device_settings').fetchone()[0] == 70


def test_failure_rolls_back_all_writes_and_releases_connection(database):
    with pytest.raises(RuntimeError, match='abort'):
        with database.transaction() as connection:
            seed(connection)
            raise RuntimeError('abort')
    with database.connection() as connection:
        assert connection.execute('SELECT COUNT(*) FROM users').fetchone()[0] == 0
    # A subsequent writer can acquire the lock and commit normally.
    with database.transaction() as connection:
        seed(connection)


def test_two_owners_have_separate_devices_and_settings(database):
    with database.transaction() as connection:
        seed(connection)
        connection.execute("INSERT INTO users (id, display_name) VALUES ('u2', 'Другой')")
        connection.execute("INSERT INTO devices (id, owner_id, name) VALUES ('d2', 'u2', 'TV2')")
        connection.execute("INSERT INTO device_settings (device_id, brightness) VALUES ('d2', 10)")
        rows = connection.execute('''
            SELECT devices.id, brightness FROM devices
            JOIN device_settings ON devices.id = device_settings.device_id
            WHERE owner_id = ?
        ''', ('u2',)).fetchall()
        assert [tuple(row) for row in rows] == [('d2', 10)]
    # This verifies data relationships, not authentication/RBAC (not implemented).


def test_pending_migration_failure_rolls_back_ddl_and_preserves_existing_data(database, tmp_path):
    with database.transaction() as connection:
        seed(connection)
    migrations = migration_copy(tmp_path)
    broken = migrations / '0002_extra.sql'
    broken.write_text('CREATE TABLE extra (id TEXT); INSERT INTO missing VALUES (1);', encoding='utf-8')
    with pytest.raises(sqlite3.OperationalError):
        Database(database.path, migrations).initialize()
    with database.connection() as connection:
        assert connection.execute("SELECT name FROM sqlite_master WHERE name = 'extra'").fetchone() is None
        assert connection.execute('SELECT COUNT(*) FROM schema_migrations').fetchone()[0] == 1
        assert connection.execute('SELECT COUNT(*) FROM users').fetchone()[0] == 1
    broken.write_text('CREATE TABLE extra (id TEXT);', encoding='utf-8')
    upgraded = Database(database.path, migrations)
    upgraded.initialize()
    upgraded.initialize()
    with upgraded.connection() as connection:
        assert connection.execute('SELECT COUNT(*) FROM schema_migrations').fetchone()[0] == 2


def test_changed_migration_is_rejected(database, tmp_path):
    migrations = migration_copy(tmp_path)
    file = migrations / '0001_registry.sql'
    file.write_text(file.read_text(encoding='utf-8') + '\n-- edited\n', encoding='utf-8')
    with pytest.raises(MigrationError, match='changed'):
        Database(database.path, migrations).initialize()


def test_migration_hash_survives_crlf_conversion(database, tmp_path):
    migrations = migration_copy(tmp_path)
    file = migrations / '0001_registry.sql'
    file.write_bytes(file.read_bytes().replace(b'\r\n', b'\n').replace(b'\n', b'\r\n'))
    Database(database.path, migrations).initialize()


def test_newer_database_is_rejected(database):
    with database.transaction() as connection:
        connection.execute('INSERT INTO schema_migrations (version, name, checksum) VALUES (2, ?, ?)',
                           ('0002_future.sql', 'not-available'))
    with pytest.raises(MigrationError, match='newer'):
        database.initialize()


def test_missing_migrations_do_not_create_database(tmp_path):
    database = Database(tmp_path / 'missing.sqlite3', tmp_path / 'missing-migrations')
    with pytest.raises(MigrationError, match='No SQL'):
        database.initialize()
    assert not database.path.exists()


def test_incomplete_initial_migration_rolls_back_everything(tmp_path):
    migrations = tmp_path / 'sql'
    migrations.mkdir()
    (migrations / '0001_broken.sql').write_text('CREATE TABLE partial (id TEXT); SELECT', encoding='utf-8')
    database = Database(tmp_path / 'broken.sqlite3', migrations)
    with pytest.raises(MigrationError, match='Incomplete'):
        database.initialize()
    with database.connection() as connection:
        assert connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall() == []


def test_parallel_initialization_is_idempotent(tmp_path):
    path = tmp_path / 'parallel.sqlite3'
    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(lambda _: Database(path).initialize(), range(2)))
    with Database(path).connection() as connection:
        assert connection.execute('SELECT COUNT(*) FROM schema_migrations').fetchone()[0] == 1


def test_startup_fails_for_unusable_database_path(tmp_path):
    app = create_app(Settings(_env_file=None, db_path=tmp_path))
    with pytest.raises(sqlite3.OperationalError):
        with TestClient(app):
            pytest.fail('Startup must not succeed with an unusable database')
