"""Owner-scoped device reads and settings updates; enrollment is a separate router."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .auth import Principal, current_user, write_user


router = APIRouter(prefix="/api/v1/devices", tags=["devices"])
Theme = Literal["BlackArch Red", "Dark Blue", "Purple", "Light"]
Screen = Literal["clock", "weather", "monitor", "codex", "null", "tamagotchi"]


class DeviceView(BaseModel):
    id: str
    owner_id: str
    name: str
    created_at: str
    is_preview: bool = False


class SettingsView(BaseModel):
    device_id: str
    brightness: int
    theme: str
    start_screen: str


class SettingsPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    brightness: int | None = Field(default=None, strict=True, ge=0, le=100)
    theme: Theme | None = None
    start_screen: Screen | None = None

    @model_validator(mode="after")
    def require_changes(self):
        if not self.model_fields_set or any(getattr(self, name) is None for name in self.model_fields_set):
            raise ValueError("Provide at least one setting; null values are not allowed")
        return self


def scope(principal: Principal) -> tuple[str, str]:
    return principal.user.id, principal.user.role


@router.get("", response_model=list[DeviceView])
def devices(
    request: Request, principal: Annotated[Principal, Depends(current_user)],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    with request.app.state.database.connection() as connection:
        return [dict(row) for row in connection.execute("""
            SELECT id, owner_id, name, created_at,
                EXISTS(SELECT 1 FROM device_previews WHERE device_id = devices.id) AS is_preview FROM devices
            WHERE owner_id = ? OR ? = 'super_admin' ORDER BY id LIMIT ? OFFSET ?
        """, (*scope(principal), limit, offset)).fetchall()]


@router.get("/{device_id}", response_model=DeviceView)
def device(device_id: str, request: Request, principal: Annotated[Principal, Depends(current_user)]):
    with request.app.state.database.connection() as connection:
        row = connection.execute("""
            SELECT id, owner_id, name, created_at,
                EXISTS(SELECT 1 FROM device_previews WHERE device_id = devices.id) AS is_preview FROM devices
            WHERE id = ? AND (owner_id = ? OR ? = 'super_admin')
        """, (device_id, *scope(principal))).fetchone()
    if row is None:
        raise HTTPException(404, "Device not found")
    return dict(row)


@router.get("/{device_id}/settings", response_model=SettingsView)
def settings(device_id: str, request: Request, principal: Annotated[Principal, Depends(current_user)]):
    with request.app.state.database.connection() as connection:
        row = connection.execute("""
            SELECT device_settings.* FROM device_settings
            JOIN devices ON devices.id = device_settings.device_id
            WHERE devices.id = ? AND (owner_id = ? OR ? = 'super_admin')
        """, (device_id, *scope(principal))).fetchone()
    if row is None:
        raise HTTPException(404, "Device settings not found")
    return dict(row)


@router.patch("/{device_id}/settings", response_model=SettingsView)
def update_settings(
    device_id: str, payload: SettingsPatch, request: Request,
    principal: Annotated[Principal, Depends(write_user)],
):
    with request.app.state.database.transaction() as connection:
        return save_settings(connection,device_id,payload,*scope(principal))


def save_settings(connection,device_id,payload,user_id,role='user'):
    """Shared write; the caller authorizes its identity inside this transaction."""
    changes=payload.model_dump(exclude_unset=True)
    allowed=connection.execute('''SELECT 1 FROM devices JOIN device_settings ON devices.id=device_settings.device_id
        WHERE devices.id=? AND (owner_id=? OR ?='super_admin')''',(device_id,user_id,role)).fetchone()
    if allowed is None:
        raise HTTPException(404,'Device settings not found')
    assignments=', '.join(f'{column} = ?' for column in changes)
    connection.execute(f'UPDATE device_settings SET {assignments} WHERE device_id=?',(*changes.values(),device_id))
    connection.execute("INSERT INTO audit_log(actor_id,action,resource_id) VALUES (?,'device.settings.updated',?)",(user_id,device_id))
    return dict(connection.execute('SELECT * FROM device_settings WHERE device_id=?',(device_id,)).fetchone())
