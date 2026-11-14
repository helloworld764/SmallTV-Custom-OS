"""Read-only, owner-scoped projection of recent Codex completions for previews."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel

from .auth import Principal, current_user, now
from .enrollment import owned_device


router = APIRouter(prefix='/api/v1/devices', tags=['display notifications'])
TTL_SECONDS = 60
PAGE_SIZE = 20
MAX_CURSOR = 9007199254740991  # Exact integer in browser JavaScript.


class DisplayEvent(BaseModel):
    id: str
    cursor: int
    kind: Literal['DONE', 'ERROR', 'INTERRUPTED']
    ttl_seconds: int


class DisplayPage(BaseModel):
    device_id: str
    server_time: int
    next_cursor: int
    events: list[DisplayEvent]


@router.get('/{device_id}/notifications', response_model=DisplayPage)
def notifications(device_id: str, request: Request,
                  principal: Annotated[Principal, Depends(current_user)],
                  after: Annotated[int, Query(ge=0, le=MAX_CURSOR)] = 0):
    checked_at = now()
    with request.app.state.database.connection() as connection:
        connection.execute('BEGIN')  # One read snapshot for scope, high-water mark and page.
        owner = owned_device(connection, device_id, principal)['owner_id']
        return notifications_for_owner(connection,device_id,owner,checked_at,after)

def notifications_for_owner(connection,device_id,owner,checked_at,after):
    high = connection.execute('''SELECT COALESCE(MAX(e.rowid), 0) FROM codex_events AS e
        JOIN codex_agents AS a ON a.id = e.agent_id WHERE a.owner_id = ?''', (owner,)).fetchone()[0]
    # A cursor is a hint, not authority; a restored DB may have a lower high-water mark.
    cursor = 0 if after > high else after
    rows = connection.execute('''SELECT e.rowid AS cursor, e.agent_id, e.event_id,
        e.kind, e.occurred_at, e.received_at FROM codex_events AS e
        JOIN codex_agents AS a ON a.id = e.agent_id
        JOIN local_accounts AS account ON account.user_id = a.owner_id
        WHERE a.owner_id = ? AND account.is_active = 1 AND e.rowid > ?
        AND e.kind IN ('DONE', 'ERROR', 'INTERRUPTED')
        AND e.received_at > ? AND e.received_at <= ? AND e.occurred_at > ?
        ORDER BY e.rowid LIMIT ?''',
        (owner, cursor, checked_at - TTL_SECONDS, checked_at, checked_at - TTL_SECONDS, PAGE_SIZE)).fetchall()
    events = [DisplayEvent(id=f"{row['agent_id']}:{row['event_id']}", cursor=row['cursor'], kind=row['kind'],
                           ttl_seconds=min(row['received_at'], row['occurred_at']) + TTL_SECONDS - checked_at)
              for row in rows]
    return DisplayPage(device_id=device_id, server_time=checked_at,
                       next_cursor=rows[-1]['cursor'] if len(rows) == PAGE_SIZE else high, events=events)
