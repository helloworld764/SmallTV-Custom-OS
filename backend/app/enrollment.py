"""Software enrollment only; no claims about hardware identity or compatibility."""

import secrets
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from .auth import Principal, current_user, now, write_user
from .security import token_hash


router = APIRouter(prefix='/api/v1/devices', tags=['device enrollment'])
bearer = HTTPBearer(auto_error=False)


class EnrollmentRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=80)


class CodeView(BaseModel):
    id: str
    code: str
    expires_at: int
    expires_in: int = 300


class CodeStatus(BaseModel):
    id: str
    name: str
    state: Literal['pending', 'enrolled', 'expired', 'closed']
    device_id: str | None
    expires_at: int
    expires_in: int


class PreviewRequest(EnrollmentRequest):
    request_id: UUID


class PreviewView(BaseModel):
    id: str
    owner_id: str
    name: str
    created_at: str
    is_preview: bool = True


class RedeemRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    code: SecretStr = Field(min_length=1, max_length=128)


class CredentialView(BaseModel):
    device_id: str
    token: str


def audit(connection, actor, action, resource):
    connection.execute('INSERT INTO audit_log (actor_id, action, resource_id) VALUES (?, ?, ?)',
                       (actor, action, resource))


def owned_device(connection, device_id: str, principal: Principal):
    row = connection.execute("SELECT owner_id FROM devices WHERE id = ? AND (owner_id = ? OR ? = 'super_admin')",
                             (device_id, principal.user.id, principal.user.role)).fetchone()
    if row is None:
        raise HTTPException(404, 'Device not found')
    return row


@router.post('/previews', response_model=PreviewView, status_code=201)
def create_preview(payload: PreviewRequest, request: Request, response: Response,
                   principal: Annotated[Principal, Depends(write_user)]):
    """A session-only display preview; explicit and idempotent, never a device login."""
    owner, request_id, timestamp = principal.user.id, str(payload.request_id), now()
    with request.app.state.database.transaction() as connection:
        previous = connection.execute('''SELECT devices.*, device_previews.request_name FROM device_previews
            JOIN devices ON devices.id = device_previews.device_id
            WHERE device_previews.owner_id = ? AND request_id = ?''', (owner, request_id)).fetchone()
        if previous is not None:
            if previous['request_name'] != payload.name:
                raise HTTPException(409, 'Request identifier already used with another name')
            response.status_code = 200
            return dict(previous)
        count = connection.execute('SELECT COUNT(*) FROM device_previews WHERE owner_id = ? AND created_at > ?',
                                   (owner, timestamp - 60)).fetchone()[0]
        if count >= 5:
            raise HTTPException(429, 'Too many previews', headers={'Retry-After': '60'})
        identifier = str(uuid4())
        connection.execute('INSERT INTO devices(id, owner_id, name) VALUES (?, ?, ?)', (identifier, owner, payload.name))
        connection.execute('INSERT INTO device_settings(device_id) VALUES (?)', (identifier,))
        connection.execute('''INSERT INTO device_previews(device_id, request_id, owner_id, created_at, request_name)
            VALUES (?, ?, ?, ?, ?)''', (identifier, request_id, owner, timestamp, payload.name))
        audit(connection, owner, 'device.preview.created', identifier)
        return dict(connection.execute('SELECT * FROM devices WHERE id = ?', (identifier,)).fetchone())


@router.get('/enrollment-codes/{code_id}', response_model=CodeStatus)
def code_status(code_id: str, request: Request, principal: Annotated[Principal, Depends(current_user)]):
    with request.app.state.database.connection() as connection:
        row = connection.execute('''SELECT * FROM device_enrollment_codes
            WHERE id = ? AND (owner_id = ? OR ? = 'super_admin')''',
            (code_id, principal.user.id, principal.user.role)).fetchone()
    if row is None:
        raise HTTPException(404, 'Enrollment code not found')
    # Legacy consumed codes have no device mapping: don't call them cancelled.
    state = ('enrolled' if row['device_id'] else 'closed' if row['consumed_at'] is not None
             else 'expired' if row['expires_at'] <= now() else 'pending')
    return CodeStatus(id=row['id'], name=row['device_name'], state=state,
                      device_id=row['device_id'], expires_at=row['expires_at'],
                      expires_in=max(0, row['expires_at'] - now()) if state == 'pending' else 0)


@router.post('/enrollment-codes', response_model=CodeView, status_code=201)
def issue_code(payload: EnrollmentRequest, request: Request, principal: Annotated[Principal, Depends(write_user)]):
    timestamp, identifier = now(), str(uuid4())
    code = 'smalltv_enroll_' + secrets.token_urlsafe(24)
    with request.app.state.database.transaction() as connection:
        count = connection.execute('''SELECT COUNT(*) FROM device_enrollment_codes
            WHERE owner_id = ? AND consumed_at IS NULL AND expires_at > ?''',
            (principal.user.id, timestamp)).fetchone()[0]
        if count >= 5:
            raise HTTPException(429, 'Too many pending enrollment codes', headers={'Retry-After': '300'})
        connection.execute('''INSERT INTO device_enrollment_codes
            (id, code_hash, owner_id, device_name, created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?)''',
            (identifier, token_hash(code), principal.user.id, payload.name, timestamp, timestamp + 300))
        audit(connection, principal.user.id, 'device.enrollment.issued', identifier)
    return CodeView(id=identifier, code=code, expires_at=timestamp + 300)


@router.delete('/enrollment-codes/{code_id}', status_code=204)
def cancel_code(code_id: str, request: Request, principal: Annotated[Principal, Depends(write_user)]):
    with request.app.state.database.transaction() as connection:
        row = connection.execute("SELECT id FROM device_enrollment_codes WHERE id = ? AND (owner_id = ? OR ? = 'super_admin')",
                                 (code_id, principal.user.id, principal.user.role)).fetchone()
        if row is None:
            raise HTTPException(404, 'Enrollment code not found')
        connection.execute('UPDATE device_enrollment_codes SET consumed_at = COALESCE(consumed_at, ?) WHERE id = ?', (now(), code_id))
        audit(connection, principal.user.id, 'device.enrollment.cancelled', code_id)
    return Response(status_code=204)


def enrollment_attempt(request: Request):
    timestamp = now()
    key = token_hash('device-enrollment:' + (request.client.host if request.client else 'unknown'))
    with request.app.state.database.transaction() as connection:
        row = connection.execute('SELECT attempts, window_start FROM device_enrollment_throttle WHERE bucket_hash = ?', (key,)).fetchone()
        if row and timestamp - row['window_start'] < 60 and row['attempts'] >= 30:
            raise HTTPException(429, 'Too many enrollment attempts', headers={'Retry-After': '60'})
        connection.execute('''INSERT INTO device_enrollment_throttle VALUES (?, 1, ?)
            ON CONFLICT(bucket_hash) DO UPDATE SET
            attempts = CASE WHEN ? - window_start >= 60 THEN 1 ELSE attempts + 1 END,
            window_start = CASE WHEN ? - window_start >= 60 THEN ? ELSE window_start END''',
            (key, timestamp, timestamp, timestamp, timestamp))


@router.post('/enroll', response_model=CredentialView, status_code=201)
def enroll(payload: RedeemRequest, request: Request):
    enrollment_attempt(request)  # Committed even when the code is rejected.
    timestamp, device_id = now(), str(uuid4())
    token = 'smalltv_device_' + secrets.token_urlsafe(32)
    with request.app.state.database.transaction() as connection:
        code = connection.execute('''SELECT codes.* FROM device_enrollment_codes AS codes
            JOIN local_accounts ON local_accounts.user_id = codes.owner_id
            WHERE code_hash = ? AND consumed_at IS NULL AND expires_at > ? AND is_active = 1''',
            (token_hash(payload.code.get_secret_value()), timestamp)).fetchone()
        if code is None:
            raise HTTPException(400, 'Invalid or expired enrollment code')
        connection.execute('INSERT INTO devices (id, owner_id, name) VALUES (?, ?, ?)',
                           (device_id, code['owner_id'], code['device_name']))
        connection.execute('INSERT INTO device_settings (device_id) VALUES (?)', (device_id,))
        connection.execute('INSERT INTO device_credentials VALUES (?, ?, ?, NULL)', (device_id, token_hash(token), timestamp))
        connection.execute('UPDATE device_enrollment_codes SET consumed_at = ?, device_id = ? WHERE id = ?',
                           (timestamp, device_id, code['id']))
        audit(connection, code['owner_id'], 'device.enrolled', device_id)
    return CredentialView(device_id=device_id, token=token)


def authenticated_device(request: Request, credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]):
    if credentials is None or credentials.scheme.lower() != 'bearer' or not credentials.credentials.startswith('smalltv_device_') or len(credentials.credentials) > 128:
        raise HTTPException(401, 'Device authentication required', headers={'WWW-Authenticate': 'Bearer'})
    with request.app.state.database.connection() as connection:
        row = connection.execute('''SELECT devices.id FROM device_credentials
            JOIN devices ON devices.id = device_credentials.device_id
            JOIN local_accounts ON local_accounts.user_id = devices.owner_id
            WHERE token_hash = ? AND revoked_at IS NULL AND is_active = 1''',
            (token_hash(credentials.credentials),)).fetchone()
    if row is None:
        raise HTTPException(401, 'Device authentication required', headers={'WWW-Authenticate': 'Bearer'})
    return row['id']


def require_device_key(connection, device_id: str, credentials: HTTPAuthorizationCredentials):
    """Serialize heartbeat acceptance with credential rotation/revocation."""
    valid = connection.execute('''SELECT 1 FROM device_credentials
        JOIN devices ON devices.id = device_credentials.device_id
        JOIN local_accounts ON local_accounts.user_id = devices.owner_id
        WHERE device_id = ? AND token_hash = ? AND revoked_at IS NULL AND is_active = 1''',
        (device_id, token_hash(credentials.credentials))).fetchone()
    if valid is None:
        raise HTTPException(401, 'Device authentication required', headers={'WWW-Authenticate': 'Bearer'})


@router.get('/{device_id}/state')
def device_state(device_id: str, request: Request, identity: Annotated[str, Depends(authenticated_device)]):
    if device_id != identity:
        raise HTTPException(404, 'Device not found')
    with request.app.state.database.connection() as connection:
        settings = connection.execute('SELECT brightness, theme, start_screen, clock_format, clock_seconds, clock_style, device_style, clock_layout, current_screen, advanced, screen_changed_at FROM device_settings WHERE device_id = ?', (identity,)).fetchone()
    if settings is None:
        raise HTTPException(404, 'Device settings not found')
    values = dict(settings)
    values['clock_seconds'] = bool(values['clock_seconds'])
    from .display_options import options
    values['advanced'] = options(values['advanced']).model_dump(exclude_unset=True,mode='json')
    return {'device_id': identity, 'settings': values}


@router.post('/{device_id}/credentials/revoke', status_code=204)
def revoke(device_id: str, request: Request, principal: Annotated[Principal, Depends(write_user)]):
    with request.app.state.database.transaction() as connection:
        owned_device(connection, device_id, principal)
        connection.execute('UPDATE device_credentials SET revoked_at = ? WHERE device_id = ?', (now(), device_id))
        connection.execute('DELETE FROM device_presence WHERE device_id = ?', (device_id,))
        audit(connection, principal.user.id, 'device.credentials.revoked', device_id)
    return Response(status_code=204)


@router.post('/{device_id}/credentials/rotate', response_model=CredentialView)
def rotate(device_id: str, request: Request, principal: Annotated[Principal, Depends(write_user)]):
    token = 'smalltv_device_' + secrets.token_urlsafe(32)
    with request.app.state.database.transaction() as connection:
        owned_device(connection, device_id, principal)
        if connection.execute('SELECT 1 FROM device_previews WHERE device_id = ?', (device_id,)).fetchone():
            raise HTTPException(409, 'A preview does not use device credentials')
        connection.execute('''INSERT INTO device_credentials VALUES (?, ?, ?, NULL)
            ON CONFLICT(device_id) DO UPDATE SET token_hash = excluded.token_hash,
            created_at = excluded.created_at, revoked_at = NULL''', (device_id, token_hash(token), now()))
        connection.execute('DELETE FROM device_presence WHERE device_id = ?', (device_id,))
        audit(connection, principal.user.id, 'device.credentials.rotated', device_id)
    return CredentialView(device_id=device_id, token=token)
