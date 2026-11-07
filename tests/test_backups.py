from contextlib import closing
from hashlib import sha256
from pathlib import Path
import os
import secrets
import sqlite3
import subprocess
import sys
import time

from fastapi.testclient import TestClient
import pytest

from backend.app import backups, manage
from backend.app.auth import COOKIE
from backend.app.backups import BackupError, backup_database, default_backup_path, restore_database, verify_backup
from backend.app.config import Settings
from backend.app.database import Database, MIGRATIONS, read_migrations
from backend.app.main import create_app
from backend.app.security import create_account, token_hash


@pytest.fixture
def source(tmp_path):
    database = Database(tmp_path / 'данные с пробелом' / 'active.sqlite3')
    database.initialize()
    password, token = secrets.token_urlsafe(24), secrets.token_urlsafe(32)
    owner = create_account(database, 'backup-test', password, 'Backup test', role='super_admin', first_admin=True)
    now = int(time.time())
    with database.transaction() as connection:
        connection.execute("INSERT INTO devices (id, owner_id, name) VALUES ('backup-tv', ?, 'Test TV')", (owner,))
        connection.execute("INSERT INTO device_settings (device_id, brightness) VALUES ('backup-tv', 35)")
        connection.execute('INSERT INTO auth_sessions (token_hash, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)',
                           (token_hash(token), owner, now, now + 3600))
    return database, password, token


def table_data(path):
    with closing(sqlite3.connect(path)) as connection:
        names = [row[0] for row in connection.execute("SELECT name FROM sqlite_schema WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        return {name: connection.execute(f'SELECT * FROM "{name}" ORDER BY rowid').fetchall() for name in names}


def test_backup_copies_all_data_and_preserves_source(source, tmp_path):
    database, _, _ = source
    before = sha256(database.path.read_bytes()).hexdigest()
    target = tmp_path / 'копии' / 'snapshot.sqlite3'
    report = backup_database(database.path, target)
    assert report.schema_version == len(read_migrations())
    assert table_data(target) == table_data(database.path)
    assert before == sha256(database.path.read_bytes()).hexdigest()
    assert verify_backup(target).schema_version == report.schema_version
    assert list(target.parent.iterdir()) == [target]  # No unfinished files or sidecars.


def test_default_names_are_unique_and_never_reuse_a_previous_backup():
    first, second = default_backup_path(), default_backup_path()
    assert first != second
    assert first.parent == Path('backups') and first.suffix == '.sqlite3'


@pytest.mark.parametrize('operation', ['backup', 'restore'])
def test_existing_destination_is_never_overwritten(source, tmp_path, operation):
    database, _, _ = source
    snapshot = tmp_path / 'snapshot.sqlite3'
    backup_database(database.path, snapshot)
    target = tmp_path / 'existing.sqlite3'
    target.write_bytes(b'Existing file must survive')
    before = target.read_bytes()
    with pytest.raises(BackupError, match='already exists'):
        if operation == 'backup':
            backup_database(database.path, target)
        else:
            restore_database(snapshot, target, active_database=database.path)
    assert target.read_bytes() == before


def test_destination_race_cannot_truncate_another_file(source, tmp_path, monkeypatch):
    database, _, _ = source
    target = tmp_path / 'racing.sqlite3'
    publish = backups._publish

    def race(stage, destination):
        destination.write_bytes(b'Another process created this file')
        publish(stage, destination)

    monkeypatch.setattr(backups, '_publish', race)
    with pytest.raises(BackupError, match='already exists'):
        backup_database(database.path, target)
    assert target.read_bytes() == b'Another process created this file'
    assert not list(tmp_path.glob('.racing-*'))


def test_missing_source_is_not_created_and_produces_no_target(tmp_path):
    missing, target = tmp_path / 'missing.sqlite3', tmp_path / 'copy.sqlite3'
    with pytest.raises(FileNotFoundError):
        backup_database(missing, target)
    with pytest.raises(FileNotFoundError):
        verify_backup(missing)
    assert not missing.exists() and not target.exists()


@pytest.mark.parametrize('payload', [b'Not SQLite', b'SQLite format 3\x00' + bytes(100)])
def test_corrupt_source_is_rejected_without_publication(tmp_path, payload):
    path, target = tmp_path / 'broken.sqlite3', tmp_path / 'copy.sqlite3'
    path.write_bytes(payload)
    with pytest.raises(BackupError):
        verify_backup(path)
    with pytest.raises(BackupError):
        backup_database(path, target)
    assert not target.exists() and path.read_bytes() == payload


def test_restore_preserves_records_revokes_sessions_and_leaves_original_active(source, tmp_path):
    database, password, token = source
    snapshot, target = tmp_path / 'snapshot.sqlite3', tmp_path / 'restored.sqlite3'
    backup_database(database.path, snapshot)
    source_before, backup_before = table_data(database.path), snapshot.read_bytes()
    restore_database(snapshot, target, active_database=database.path)
    restored = table_data(target)
    for table, rows in source_before.items():
        assert restored[table] == ([] if table == 'auth_sessions' else rows)
    assert table_data(database.path) == source_before
    assert snapshot.read_bytes() == backup_before
    with TestClient(create_app(Settings(_env_file=None, db_path=target, cookie_secure=False))) as client:
        client.cookies.set(COOKIE, token)
        assert client.get('/api/v1/auth/session').status_code == 401
        client.cookies.clear()
        response = client.post('/api/v1/auth/login', headers={'X-SmallTV-Client': 'web'},
                               json={'username': 'backup-test', 'password': password})
        assert response.status_code == 200
        assert client.get('/api/v1/devices/backup-tv/settings').json()['brightness'] == 35
    with TestClient(create_app(Settings(_env_file=None, db_path=database.path, cookie_secure=False))) as original:
        original.cookies.set(COOKIE, token)
        assert original.get('/api/v1/auth/session').status_code == 200


def test_restore_refuses_configured_active_target_even_if_it_does_not_exist(source, tmp_path):
    database, _, _ = source
    snapshot = tmp_path / 'snapshot.sqlite3'
    backup_database(database.path, snapshot)
    active = tmp_path / 'future-active.sqlite3'
    with pytest.raises(BackupError, match='configured active'):
        restore_database(snapshot, active, active_database=active)
    assert not active.exists()
    with pytest.raises(BackupError, match='active database is not a restore source'):
        restore_database(database.path, tmp_path / 'copy.sqlite3', active_database=database.path)


def test_verify_is_readonly_and_accepts_a_readonly_file(source, tmp_path):
    database, _, _ = source
    snapshot = tmp_path / 'snapshot.sqlite3'
    backup_database(database.path, snapshot)
    before = snapshot.read_bytes()
    snapshot.chmod(0o444)
    try:
        verify_backup(snapshot)
        assert snapshot.read_bytes() == before
    finally:
        snapshot.chmod(0o600)


@pytest.mark.parametrize('change', [
    "UPDATE schema_migrations SET checksum = 'changed' WHERE version = 1",
    "INSERT INTO schema_migrations (version, name, checksum) VALUES (999, 'future', 'unknown')",
    "DROP TABLE device_settings",
    "CREATE TRIGGER unsafe_restore AFTER DELETE ON auth_sessions BEGIN DELETE FROM users; END",
])
def test_incompatible_or_modified_schema_is_rejected(source, tmp_path, change):
    database, _, _ = source
    with database.transaction() as connection:
        connection.execute(change)
    target = tmp_path / 'copy.sqlite3'
    with pytest.raises(BackupError):
        verify_backup(database.path)
    with pytest.raises(BackupError):
        backup_database(database.path, target)
    assert not target.exists()


def test_foreign_key_violations_are_rejected(source):
    database, _, _ = source
    with closing(sqlite3.connect(database.path)) as connection:
        connection.execute("INSERT INTO devices (id, owner_id, name) VALUES ('orphan', 'missing', 'Orphan')")
        connection.commit()
    with pytest.raises(BackupError, match='foreign key'):
        verify_backup(database.path)


def test_copy_failure_removes_only_its_temporary_file(source, tmp_path, monkeypatch):
    database, _, _ = source
    target = tmp_path / 'failed.sqlite3'
    sentinel = tmp_path / 'preserve.sqlite3'
    sentinel.write_bytes(b'Preserve unrelated file')
    original = backups._verify
    calls = 0

    def reject_copy(connection, migrations):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise BackupError('Injected copy failure')
        return original(connection, migrations)

    monkeypatch.setattr(backups, '_verify', reject_copy)
    with pytest.raises(BackupError, match='Injected'):
        backup_database(database.path, target)
    assert not target.exists()
    assert not list(tmp_path.glob('.failed-*'))
    assert sentinel.read_bytes() == b'Preserve unrelated file'


def test_flush_failure_never_publishes_a_snapshot(source, tmp_path, monkeypatch):
    database, _, _ = source
    target = tmp_path / 'flush.sqlite3'

    def fail_flush(descriptor):
        raise OSError('Injected flush failure')

    monkeypatch.setattr(backups.os, 'fsync', fail_flush)
    with pytest.raises(OSError, match='Injected flush'):
        backup_database(database.path, target)
    assert not target.exists()
    assert not list(tmp_path.glob('.flush-*'))


def test_backup_of_active_wal_database_includes_committed_data_and_not_uncommitted(source, tmp_path):
    database, _, _ = source
    with closing(sqlite3.connect(database.path, isolation_level=None)) as writer:
        assert writer.execute('PRAGMA journal_mode = WAL').fetchone()[0] == 'wal'
        writer.execute("UPDATE device_settings SET brightness = 22 WHERE device_id = 'backup-tv'")
        writer.execute('BEGIN IMMEDIATE')
        writer.execute("UPDATE device_settings SET brightness = 99 WHERE device_id = 'backup-tv'")
        target = tmp_path / 'wal-snapshot.sqlite3'
        backup_database(database.path, target)
        with closing(sqlite3.connect(target)) as copied:
            assert copied.execute('SELECT brightness FROM device_settings').fetchone()[0] == 22
            assert copied.execute('PRAGMA journal_mode').fetchone()[0] == 'delete'
        writer.rollback()
    assert not Path(str(target) + '-wal').exists()


def test_backup_works_while_fastapi_is_running(source, tmp_path):
    database, _, token = source
    with TestClient(create_app(Settings(_env_file=None, db_path=database.path, cookie_secure=False))) as client:
        client.cookies.set(COOKIE, token)
        backup_database(database.path, tmp_path / 'running.sqlite3')
        assert client.get('/health').status_code == 200
        assert client.get('/api/v1/devices/backup-tv/settings').status_code == 200


def test_restore_known_older_schema_upgrades_only_new_copy(tmp_path):
    old_migrations = tmp_path / 'old-sql'
    old_migrations.mkdir()
    first = MIGRATIONS / '0001_registry.sql'
    (old_migrations / first.name).write_bytes(first.read_bytes())
    old = Database(tmp_path / 'old.sqlite3', old_migrations)
    old.initialize()
    with old.transaction() as connection:
        connection.execute("INSERT INTO users (id, display_name) VALUES ('old-user', 'Existing')")
    assert verify_backup(old.path).schema_version == 1
    original = old.path.read_bytes()
    target = tmp_path / 'upgraded.sqlite3'
    report = restore_database(old.path, target, active_database=tmp_path / 'active.sqlite3')
    assert report.schema_version == len(read_migrations())
    assert old.path.read_bytes() == original
    with Database(target).connection() as connection:
        assert connection.execute('SELECT id FROM users').fetchone()[0] == 'old-user'
        assert connection.execute('SELECT COUNT(*) FROM auth_sessions').fetchone()[0] == 0


@pytest.mark.parametrize('timeout', [0, -1, float('inf'), float('nan'), 301])
def test_invalid_timeout_fails_before_creating_output(source, tmp_path, timeout):
    target = tmp_path / 'invalid.sqlite3'
    with pytest.raises(BackupError, match='Timeout'):
        backup_database(source[0].path, target, timeout=timeout)
    assert not target.exists()


def test_busy_database_fails_within_timeout_and_no_output(source, tmp_path):
    database, _, _ = source
    target = tmp_path / 'locked.sqlite3'
    with closing(sqlite3.connect(database.path, isolation_level=None)) as writer:
        writer.execute('BEGIN EXCLUSIVE')
        started = time.monotonic()
        with pytest.raises(BackupError):
            backup_database(database.path, target, timeout=0.05)
        assert time.monotonic() - started < 2
        writer.rollback()
    assert not target.exists()


def test_cli_commands_do_not_ask_for_password_or_modify_configuration(source, tmp_path, monkeypatch, capsys):
    database, _, password = source
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('SMALLTV_DB_PATH', str(database.path))
    monkeypatch.setattr(manage.getpass, 'getpass', lambda prompt: pytest.fail('Backup commands must not request passwords'))
    assert manage.main(['backup-db']) == 0
    copies = list((tmp_path / 'backups').glob('*.sqlite3'))
    assert len(copies) == 1
    assert manage.main(['verify-backup', '--source', str(copies[0])]) == 0
    target = tmp_path / 'restore.sqlite3'
    assert manage.main(['restore-db', '--source', str(copies[0]), '--output', str(target)]) == 0
    with pytest.raises(SystemExit) as error:
        manage.main(['backup-db', '--output', str(copies[0])])
    assert error.value.code == 1
    output = capsys.readouterr()
    assert password not in output.out + output.err
    assert not (tmp_path / '.env').exists()


def test_cli_module_runs_backup_verify_and_restore_in_separate_processes(source, tmp_path):
    database, password, _ = source
    snapshot, restored = tmp_path / 'cli-copy.sqlite3', tmp_path / 'cli-restored.sqlite3'
    environment = os.environ.copy()
    environment.update(SMALLTV_DB_PATH=str(database.path), PYTHONIOENCODING='utf-8')
    root = Path(__file__).resolve().parents[1]
    for args in [
        ['backup-db', '--output', str(snapshot)],
        ['verify-backup', '--source', str(snapshot)],
        ['restore-db', '--source', str(snapshot), '--output', str(restored)],
    ]:
        result = subprocess.run([sys.executable, '-m', 'backend.app.manage', *args], cwd=root,
                                env=environment, stdin=subprocess.DEVNULL, capture_output=True,
                                text=True, encoding='utf-8', timeout=15)
        assert result.returncode == 0, result.stdout + result.stderr
        assert password not in result.stdout + result.stderr
    assert verify_backup(restored).schema_version == len(read_migrations())
    assert table_data(restored)['users'] == table_data(database.path)['users']
