"""Short-lived SQLite connections and atomic, checksummed SQL migrations."""

from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path
import re
import sqlite3
from collections.abc import Iterator


MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"


class MigrationError(RuntimeError):
    """Migration history and available source files are incompatible."""


def statements(script: str) -> Iterator[str]:
    """Split using SQLite's parser, preserving semicolons inside SQL strings.

    executescript() is intentionally avoided: it can implicitly commit an
    existing transaction. Migration files must contain complete SQL statements.
    """
    pending = ""
    for char in script:
        pending += char
        if char == ";" and sqlite3.complete_statement(pending):
            yield pending
            pending = ""
    if pending.strip():
        raise MigrationError("Incomplete SQL statement in migration")


class Database:
    def __init__(self, path: Path, migrations: Path = MIGRATIONS):
        self.path = Path(path)
        self.migrations = Path(migrations)

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        # Connections are created and closed on the caller's thread, never shared.
        connection = sqlite3.connect(self.path, timeout=5.0, isolation_level=None)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA synchronous = FULL")
            yield connection
        finally:
            connection.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    def initialize(self) -> None:
        files = sorted(self.migrations.glob("*.sql"))
        if not files:
            raise MigrationError("No SQL migrations found")
        sources = []
        for version, file in enumerate(files, start=1):
            match = re.fullmatch(r"(\d{4})_[a-z0-9_]+\.sql", file.name)
            if match is None or int(match[1]) != version:
                raise MigrationError("Migration numbers must be consecutive from 0001")
            # Universal newline conversion keeps hashes stable across Windows/Linux.
            script = file.read_text(encoding="utf-8")
            sources.append((version, file.name, sha256(script.encode("utf-8")).hexdigest(), script))

        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.transaction() as connection:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    checksum TEXT NOT NULL,
                    applied_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                )
            """)
            applied = connection.execute(
                "SELECT version, name, checksum FROM schema_migrations ORDER BY version"
            ).fetchall()
            if len(applied) > len(sources):
                raise MigrationError("Database schema is newer than this backend")
            for row, source in zip(applied, sources):
                if tuple(row) != source[:3]:
                    raise MigrationError("Applied migration changed or history is inconsistent")

            for version, name, checksum, script in sources[len(applied):]:
                for statement in statements(script):
                    connection.execute(statement)
                connection.execute(
                    "INSERT INTO schema_migrations (version, name, checksum) VALUES (?, ?, ?)",
                    (version, name, checksum),
                )
            if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise MigrationError("Database contains foreign key violations")
