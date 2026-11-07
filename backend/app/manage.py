"""Local management: python -m backend.app.manage create-admin --username NAME."""

import argparse
import getpass
import sqlite3
import warnings

from .config import Settings
from .database import Database
from .security import create_account, normalize_username


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Storm OS local account management")
    commands = parser.add_subparsers(dest="command", required=True)
    bootstrap = commands.add_parser("create-admin", help="Create the first administrator only")
    bootstrap.add_argument("--username", required=True)
    bootstrap.add_argument("--display-name")
    args = parser.parse_args(argv)
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
