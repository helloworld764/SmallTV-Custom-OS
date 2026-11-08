"""Verified local SQLite snapshots, never overwriting a destination or active DB."""

from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
import math
import os
from pathlib import Path
import sqlite3
import tempfile
import time
from uuid import uuid4

from .database import (
    Database, MIGRATIONS, MIGRATION_TABLE_SQL, MigrationError,
    check_migration_history, read_migrations, statements,
)


class BackupError(RuntimeError):
    """Safe, operator-facing failure without database contents or credentials."""


@dataclass(frozen=True)
class SnapshotReport:
    path: Path
    schema_version: int


def default_backup_path(directory: Path = Path('backups')) -> Path:
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    return Path(directory) / f'smalltv-{stamp}-{uuid4().hex[:12]}.sqlite3'


def _deadline(timeout: float) -> float:
    if not math.isfinite(timeout) or not 0 < timeout <= 300:
        raise BackupError('Timeout must be greater than zero and at most 300 seconds')
    return time.monotonic() + timeout


def _readonly(path: Path, deadline: float) -> sqlite3.Connection:
    path = Path(path).resolve(strict=True)
    if not path.is_file():
        raise BackupError('Source must be an existing database file')
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise BackupError('Database operation timed out')
    connection = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True,
                                 isolation_level=None, timeout=min(remaining, 5))
    try:
        connection.execute('PRAGMA trusted_schema = OFF')
        connection.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
    except BaseException:
        connection.close()
        raise
    return connection


def _schema(connection: sqlite3.Connection) -> list:
    return connection.execute("""
        SELECT type, name, tbl_name, sql FROM sqlite_schema
        WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name
    """).fetchall()


def _publish(stage: Path, destination: Path) -> None:
    if os.name == 'nt':
        # On Windows rename is atomic and refuses an existing destination.
        os.rename(stage, destination)
    else:
        # POSIX rename overwrites files; link instead provides O_EXCL semantics.
        os.link(stage, destination)


def _verify(connection: sqlite3.Connection, migrations: Path) -> int:
    if connection.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
        raise BackupError('SQLite integrity check failed')
    if connection.execute('PRAGMA foreign_key_check').fetchone() is not None:
        raise BackupError('Database contains foreign key violations')
    sources = read_migrations(migrations)
    version = check_migration_history(connection, sources)
    if version == 0:
        raise BackupError('Database has no applied Storm OS migrations')
    # Match the trusted schema, including views/triggers. Do not run restore-time
    # DELETE operations against an arbitrary or modified SQLite schema.
    with closing(sqlite3.connect(':memory:', isolation_level=None)) as expected:
        expected.execute(MIGRATION_TABLE_SQL)
        for _, _, _, script in sources[:version]:
            for statement in statements(script):
                expected.execute(statement)
        if _schema(connection) != _schema(expected):
            raise BackupError('Database schema differs from the trusted migrations')
    return version


def verify_backup(path: Path, *, migrations: Path = MIGRATIONS, timeout: float = 30) -> SnapshotReport:
    deadline = _deadline(timeout)
    try:
        with closing(_readonly(path, deadline)) as connection:
            version = _verify(connection, migrations)
        return SnapshotReport(Path(path), version)
    except (sqlite3.Error, MigrationError) as error:
        raise BackupError('Database is unreadable, damaged or incompatible with this backend') from error


def _snapshot(source: Path, destination: Path, migrations: Path, timeout: float, restore: bool) -> SnapshotReport:
    deadline = _deadline(timeout)
    destination = Path(destination).absolute()
    if destination.suffix not in {'.sqlite3', '.sqlite', '.db'}:
        raise BackupError('Destination must end in .sqlite3, .sqlite or .db')
    if destination.exists() or destination.is_symlink():
        raise BackupError('Destination already exists; no files were overwritten')
    stage = None
    try:
        with closing(_readonly(source, deadline)) as connection:
            _verify(connection, migrations)
            destination.parent.mkdir(parents=True, exist_ok=True)
            descriptor, name = tempfile.mkstemp(prefix=f'.{destination.stem}-',
                                               suffix=destination.suffix, dir=destination.parent)
            os.close(descriptor)
            stage = Path(name)
            with closing(sqlite3.connect(stage, isolation_level=None)) as target:
                target.execute('PRAGMA synchronous = FULL')
                target.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)

                def progress(status, remaining, total):
                    if time.monotonic() >= deadline:
                        raise BackupError('Database operation timed out')

                connection.backup(target, pages=128, progress=progress, sleep=0.05)
                # Publish a self-contained DB, not a file dependent on a WAL sidecar.
                if target.execute('PRAGMA journal_mode = DELETE').fetchone()[0] != 'delete':
                    raise BackupError('Could not create a self-contained snapshot')
                _verify(target, migrations)

        if restore:
            restored = Database(stage, migrations)
            restored.initialize()  # Upgrade only this new copy, never the source.
            with restored.transaction() as connection:
                connection.execute('DELETE FROM auth_sessions')
                revoked_at = int(time.time())
                connection.execute('UPDATE device_credentials SET revoked_at = ? WHERE revoked_at IS NULL', (revoked_at,))
                connection.execute('UPDATE agent_credentials SET revoked_at = ? WHERE revoked_at IS NULL', (revoked_at,))
                connection.execute('DELETE FROM agent_presence')
                connection.execute('DELETE FROM device_presence')
                connection.execute('UPDATE device_enrollment_codes SET consumed_at = ? WHERE consumed_at IS NULL', (revoked_at,))
        with closing(_readonly(stage, deadline)) as verified:
            version = _verify(verified, migrations)
        # Windows _commit()/fsync requires a descriptor opened for writing.
        # This is our new staging file, never the readonly source.
        with stage.open('r+b') as stream:
            os.fsync(stream.fileno())
        # Atomic no-overwrite publication. No fallback that could
        # truncate a pre-existing file or expose a half-written final snapshot.
        _publish(stage, destination)
        return SnapshotReport(Path(destination), version)
    except FileExistsError as error:
        raise BackupError('Destination already exists; no files were overwritten') from error
    except (sqlite3.Error, MigrationError) as error:
        raise BackupError('Database is unreadable, damaged, busy or incompatible; no snapshot published') from error
    finally:
        if stage is not None:
            # Only the temporary file created by this invocation is removed.
            stage.unlink(missing_ok=True)


def backup_database(source: Path, destination: Path, *, migrations: Path = MIGRATIONS,
                    timeout: float = 30) -> SnapshotReport:
    return _snapshot(source, destination, migrations, timeout, restore=False)


def restore_database(source: Path, destination: Path, *, active_database: Path,
                     migrations: Path = MIGRATIONS, timeout: float = 30) -> SnapshotReport:
    active = Path(active_database).resolve()
    if Path(destination).resolve() == active:
        raise BackupError('Refusing to restore into the configured active database path')
    if Path(source).resolve() == active:
        raise BackupError('Create a backup first; the active database is not a restore source')
    return _snapshot(source, destination, migrations, timeout, restore=True)
