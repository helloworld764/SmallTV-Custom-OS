"""Local management: python -m backend.app.manage create-admin --username NAME."""

import argparse
import getpass
import sqlite3
import warnings

from .config import Settings
from .database import Database
from .security import create_account, normalize_username
from .backups import BackupError, backup_database, default_backup_path, restore_database, verify_backup
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Storm OS local account management")
    commands = parser.add_subparsers(dest="command", required=True)
    bootstrap = commands.add_parser("create-admin", help="Create the first administrator only")
    bootstrap.add_argument("--username", required=True)
    bootstrap.add_argument("--display-name")
    backup = commands.add_parser("backup-db", help="Create and verify a new SQLite snapshot")
    output = backup.add_mutually_exclusive_group()
    output.add_argument("--output", type=Path)
    output.add_argument("--directory", type=Path, default=Path('backups'))
    verify = commands.add_parser("verify-backup", help="Verify a SQLite snapshot without changing it")
    verify.add_argument("--source", type=Path, required=True)
    restore = commands.add_parser("restore-db", help="Restore into a new file and revoke restored sessions")
    restore.add_argument("--source", type=Path, required=True)
    restore.add_argument("--output", type=Path, required=True)
    for command in (backup, verify, restore):
        command.add_argument("--timeout-seconds", type=float, default=30)
    args = parser.parse_args(argv)
    if args.command != 'create-admin':
        try:
            if args.command == 'backup-db':
                target = args.output if args.output is not None else default_backup_path(args.directory)
                report = backup_database(Settings().db_path, target, timeout=args.timeout_seconds)
            elif args.command == 'verify-backup':
                report = verify_backup(args.source, timeout=args.timeout_seconds)
            else:
                report = restore_database(args.source, args.output, active_database=Settings().db_path,
                                          timeout=args.timeout_seconds)
        except (BackupError, OSError, ValueError) as error:
            parser.exit(1, f"Database operation failed: {error}\n")
        print(f"Verified SQLite file (schema {report.schema_version}): {report.path}")
        if args.command == 'restore-db':
            print("Restored sessions revoked. Active database and configuration were not changed.")
        return 0
    try:
        username = normalize_username(args.username)
        # Refuse getpass's echoing fallback when there is no usable terminal.
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            password = getpass.getpass("Password (12+ characters): ")
            confirmation = getpass.getpass("Repeat password: ")
        if password != confirmation:
            raise ValueError("Passwords do not match")
        database = Database(Settings().db_path)
        database.initialize()
        create_account(database, username, password, args.display_name or username,
                       role="super_admin", first_admin=True)
    except (ValueError, sqlite3.IntegrityError) as error:
        # Validation/constraint messages contain neither passwords nor hashes.
        parser.exit(1, f"Account not created: {error}\n")
    except (getpass.GetPassWarning, EOFError):
        parser.exit(1, "A terminal with hidden password input is required\n")
    print("Administrator created. No session was opened.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
