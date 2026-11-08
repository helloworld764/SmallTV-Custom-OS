"""Server-observed device reachability; independent of simulator/API liveness."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel, ConfigDict

from .auth import Principal, current_user, now
from .enrollment import authenticated_device, bearer, owned_device, require_device_key


router = APIRouter(prefix='/api/v1/devices', tags=['device presence'])


class HeartbeatRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')


class DevicePresence(BaseModel):
    device_id: str
    state: Literal['UNKNOWN', 'ONLINE', 'STALE', 'OFFLINE']
    last_seen_at: int | None
    server_time: int
    stale_after_seconds: int
    offline_after_seconds: int


@router.post('/{device_id}/heartbeat', status_code=204)
def heartbeat(device_id: str, payload: HeartbeatRequest, request: Request,
              identity: Annotated[str, Depends(authenticated_device)],
              credentials: Annotated[HTTPAuthorizationCredentials, Depends(bearer)]):
    if device_id != identity:
        raise HTTPException(404, 'Device not found')
    with request.app.state.database.transaction() as connection:
        require_device_key(connection, identity, credentials)
        received_at = now()
        previous = connection.execute('SELECT last_seen_at FROM device_presence WHERE device_id = ?',
                                      (identity,)).fetchone()
        if previous is not None and 0 <= received_at - previous['last_seen_at'] < 5:
            raise HTTPException(429, 'Heartbeat received too frequently',
                                headers={'Retry-After': str(5 - (received_at - previous['last_seen_at']))})
        connection.execute('''INSERT INTO device_presence (device_id, last_seen_at) VALUES (?, ?)
            ON CONFLICT(device_id) DO UPDATE SET last_seen_at = excluded.last_seen_at''', (identity, received_at))
    return Response(status_code=204)


@router.get('/{device_id}/presence', response_model=DevicePresence)
def presence(device_id: str, request: Request, principal: Annotated[Principal, Depends(current_user)]):
    with request.app.state.database.connection() as connection:
        owned_device(connection, device_id, principal)
        row = connection.execute('''SELECT device_presence.last_seen_at,
            device_credentials.device_id AS credential_id, device_credentials.revoked_at,
            local_accounts.is_active FROM devices
            LEFT JOIN device_credentials ON device_credentials.device_id = devices.id
            LEFT JOIN local_accounts ON local_accounts.user_id = devices.owner_id
            LEFT JOIN device_presence ON device_presence.device_id = devices.id
            WHERE devices.id = ?''', (device_id,)).fetchone()
    checked_at = now()
    settings = request.app.state.settings
    last_seen = row['last_seen_at']
    if row['credential_id'] is not None and (row['revoked_at'] is not None or not row['is_active']):
        state = 'OFFLINE'
    elif row['credential_id'] is None or last_seen is None or last_seen > checked_at:
        state = 'UNKNOWN'
    elif checked_at - last_seen >= settings.device_offline_seconds:
        state = 'OFFLINE'
    elif checked_at - last_seen >= settings.device_stale_seconds:
        state = 'STALE'
    else:
        state = 'ONLINE'
    return DevicePresence(device_id=device_id, state=state, last_seen_at=last_seen,
                          server_time=checked_at, stale_after_seconds=settings.device_stale_seconds,
                          offline_after_seconds=settings.device_offline_seconds)
