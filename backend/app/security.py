"""Password hashing and local account provisioning; no plaintext persistence."""

import hashlib
import hmac
import re
import secrets
from uuid import uuid4

from .database import Database


ITERATIONS = 600_000


def normalize_username(username: str) -> str:
    username = username.strip().lower()
    if re.fullmatch(r"[a-z0-9][a-z0-9_.-]{2,63}", username) is None:
        raise ValueError("Username must be 3-64 ASCII letters, digits, dots, underscores or hyphens")
    return username


def hash_password(password: str) -> str:
    if not 12 <= len(password) <= 1024:
        raise ValueError("Password must contain 12-1024 characters")
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, ITERATIONS)
    return f"pbkdf2_sha256${ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations, salt, expected = encoded.split("$")
        if algorithm != "pbkdf2_sha256" or int(iterations) != ITERATIONS:
            return False
        salt_bytes, expected_bytes = bytes.fromhex(salt), bytes.fromhex(expected)
        if len(salt_bytes) != 16 or len(expected_bytes) != 32:
            return False
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt_bytes, ITERATIONS)
        return hmac.compare_digest(digest, expected_bytes)
    except (ValueError, TypeError):
        return False


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def csrf_token(session_token: str) -> str:
    # Independent value bound to this random session secret. No extra key to deploy.
    return hmac.new(session_token.encode("utf-8"), b"smalltv-csrf-v1", "sha256").hexdigest()


def create_account(
    database: Database, username: str, password: str, display_name: str,
    *, role: str = "user", first_admin: bool = False,
) -> str:
    """Local provisioning primitive; never exposed as a registration endpoint."""
    username = normalize_username(username)
    if not 1 <= len(display_name.strip()) <= 80 or role not in {"user", "super_admin"}:
        raise ValueError("Invalid display name or role")
    if first_admin and role != "super_admin":
        raise ValueError("Bootstrap must create a super_admin")
    encoded = hash_password(password)
    user_id = str(uuid4())
    with database.transaction() as connection:
        if first_admin and connection.execute(
            "SELECT 1 FROM users WHERE role = 'super_admin' LIMIT 1"
        ).fetchone():
            raise ValueError("An administrator already exists; bootstrap refused")
        connection.execute(
            "INSERT INTO users (id, display_name, role) VALUES (?, ?, ?)",
            (user_id, display_name.strip(), role),
        )
        connection.execute(
            "INSERT INTO local_accounts (user_id, username, password_hash) VALUES (?, ?, ?)",
            (user_id, username, encoded),
        )
        connection.execute(
            "INSERT INTO audit_log (actor_id, action, resource_id) VALUES (?, 'account.created', ?)",
            (user_id, user_id),
        )
    return user_id
