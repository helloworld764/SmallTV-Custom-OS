"""Cookie sessions, login throttling and CSRF dependencies."""

from dataclasses import dataclass
import hmac
import re
import secrets
import time
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from .security import csrf_token, hash_password, normalize_username, token_hash, verify_password


router = APIRouter(prefix="/api/v1/auth", tags=["auth"])
COOKIE = "smalltv_session"
DUMMY_HASH = hash_password(secrets.token_urlsafe(32))


def now() -> int:
    return int(time.time())


class UserView(BaseModel):
    id: str
    display_name: str
    role: Literal["user", "super_admin"]
    username: str


class SessionView(BaseModel):
    user: UserView
    csrf_token: str


class Login(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=3, max_length=64)
    password: SecretStr = Field(min_length=1, max_length=1024)

    @field_validator("username")
    @classmethod
    def canonical_username(cls, value: str) -> str:
        return normalize_username(value)


@dataclass(frozen=True)
class Principal:
    user: UserView
    session_hash: str
    csrf: str


def same_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    expected = f"{request.url.scheme}://{request.url.netloc}"
    if (origin is not None and origin != expected) or request.headers.get("sec-fetch-site") == "cross-site":
        raise HTTPException(403, "Cross-origin request rejected")


def login_guard(
    request: Request, x_smalltv_client: Annotated[str | None, Header()] = None,
) -> None:
    same_origin(request)
    # A custom header prevents cross-origin HTML form login; no CORS is enabled.
    if x_smalltv_client != "web":
        raise HTTPException(403, "X-SmallTV-Client: web is required")


def current_user(request: Request) -> Principal:
    token = request.cookies.get(COOKIE, "")
    if not token or len(token) > 128:
        raise HTTPException(401, "Authentication required")
    with request.app.state.database.connection() as connection:
        row = connection.execute("""
            SELECT users.id, users.display_name, users.role, local_accounts.username
            FROM auth_sessions
            JOIN users ON users.id = auth_sessions.user_id
            JOIN local_accounts ON local_accounts.user_id = users.id
            WHERE token_hash = ? AND expires_at > ? AND local_accounts.is_active = 1
        """, (token_hash(token), now())).fetchone()
    if row is None:
        raise HTTPException(401, "Authentication required")
    return Principal(UserView(**dict(row)), token_hash(token), csrf_token(token))


def write_user(
    request: Request, principal: Annotated[Principal, Depends(current_user)],
    x_csrf_token: Annotated[str | None, Header()] = None,
) -> Principal:
    same_origin(request)
    if (x_csrf_token is None or re.fullmatch(r"[0-9a-f]{64}", x_csrf_token) is None
            or not hmac.compare_digest(x_csrf_token, principal.csrf)):
        raise HTTPException(403, "Invalid CSRF token")
    return principal


def consume_login_attempt(request: Request, username: str) -> None:
    timestamp = now()
    # Neither trust proxy headers nor store raw IP/username in the throttle table.
    client_ip = request.client.host if request.client else "unknown"
    buckets = [(token_hash("ip:" + client_ip), 10), (token_hash("user:" + username), 5)]
    with request.app.state.database.transaction() as connection:
        for key, limit in buckets:
            row = connection.execute(
                "SELECT attempts, window_start FROM login_throttle WHERE bucket_hash = ?", (key,)
            ).fetchone()
            if row and timestamp - row['window_start'] < 60 and row['attempts'] >= limit:
                raise HTTPException(429, "Too many login attempts", headers={"Retry-After": "60"})
        for key, _ in buckets:
            connection.execute("""
                INSERT INTO login_throttle (bucket_hash, attempts, window_start) VALUES (?, 1, ?)
                ON CONFLICT(bucket_hash) DO UPDATE SET
                    attempts = CASE WHEN ? - window_start >= 60 THEN 1 ELSE attempts + 1 END,
                    window_start = CASE WHEN ? - window_start >= 60 THEN ? ELSE window_start END
            """, (key, timestamp, timestamp, timestamp, timestamp))


@router.post("/login", response_model=SessionView, dependencies=[Depends(login_guard)])
def login(payload: Login, request: Request, response: Response) -> SessionView:
    consume_login_attempt(request, payload.username)
    database = request.app.state.database
    with database.connection() as connection:
        account = connection.execute("""
            SELECT users.id, users.display_name, users.role, local_accounts.username,
                   password_hash, is_active
            FROM local_accounts JOIN users ON users.id = local_accounts.user_id
            WHERE username = ?
        """, (payload.username,)).fetchone()
    valid = verify_password(payload.password.get_secret_value(), account['password_hash'] if account else DUMMY_HASH)
    if not valid or account is None or not account['is_active']:
        raise HTTPException(401, "Invalid credentials")
    token = secrets.token_urlsafe(32)
    timestamp = now()
    settings = request.app.state.settings
    with database.transaction() as connection:
        old_token = request.cookies.get(COOKIE)
        if old_token:
            connection.execute("DELETE FROM auth_sessions WHERE token_hash = ?", (token_hash(old_token),))
        connection.execute(
            "INSERT INTO auth_sessions (token_hash, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
            (token_hash(token), account['id'], timestamp, timestamp + settings.session_ttl_seconds),
        )
        connection.execute(
            "INSERT INTO audit_log (actor_id, action) VALUES (?, 'auth.login')", (account['id'],)
        )
    response.set_cookie(COOKIE, token, max_age=settings.session_ttl_seconds,
                        httponly=True, secure=settings.cookie_secure, samesite="strict", path="/")
    return SessionView(user=UserView(**{key: account[key] for key in UserView.model_fields}),
                       csrf_token=csrf_token(token))


@router.get("/session", response_model=SessionView)
def session(principal: Annotated[Principal, Depends(current_user)]) -> SessionView:
    return SessionView(user=principal.user, csrf_token=principal.csrf)


@router.post("/logout", status_code=204)
def logout(request: Request, principal: Annotated[Principal, Depends(write_user)]) -> Response:
    with request.app.state.database.transaction() as connection:
        connection.execute("DELETE FROM auth_sessions WHERE token_hash = ?", (principal.session_hash,))
        connection.execute(
            "INSERT INTO audit_log (actor_id, action) VALUES (?, 'auth.logout')", (principal.user.id,)
        )
    response = Response(status_code=204)
    response.delete_cookie(COOKIE, path="/", httponly=True,
                           secure=request.app.state.settings.cookie_secure, samesite="strict")
    return response
