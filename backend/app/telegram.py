"""Account linking and notification preferences. No Telegram HTTP ingress."""

import secrets
import time
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from .auth import Principal, current_user, write_user
from .security import token_hash

router = APIRouter(prefix='/api/v1/telegram', tags=['telegram'])
CATEGORIES = ('codex', 'connection', 'pet', 'system')


def active_link(connection, telegram_id):
    return connection.execute('''SELECT telegram_links.*, users.display_name
        FROM telegram_links JOIN users ON users.id=telegram_links.user_id
        JOIN local_accounts ON local_accounts.user_id=users.id
        WHERE telegram_id=? AND is_active=1''', (telegram_id,)).fetchone()


def throttle(connection, bucket, now, limit, seconds=60):
    row = connection.execute('SELECT * FROM telegram_throttle WHERE bucket=?', (bucket,)).fetchone()
    if row and 0 <= now-row['window_start'] < seconds and row['attempts'] >= limit:
        return False
    connection.execute('''INSERT INTO telegram_throttle VALUES (?, ?, 1)
        ON CONFLICT(bucket) DO UPDATE SET window_start=excluded.window_start,
        attempts=CASE WHEN ?-window_start BETWEEN 0 AND ? THEN attempts+1 ELSE 1 END''',
        (bucket, now if row is None or not 0 <= now-row['window_start'] < seconds else row['window_start'], now, seconds-1))
    return True


def preferences(connection, user_id):
    row = connection.execute('SELECT * FROM telegram_preferences WHERE user_id=?', (user_id,)).fetchone()
    return {key: bool(row[key]) if row else key != 'enabled' for key in ('enabled', *CATEGORIES)}


def notification_allowed(connection, user_id, category, occurred_at):
    row = connection.execute('SELECT * FROM telegram_preferences WHERE user_id=?',(user_id,)).fetchone()
    if (row is None or category not in CATEGORIES or not row['enabled'] or not row[category]
            or occurred_at is None or row['enabled_since'] is None or row['enabled_since'] > occurred_at):
        return False
    return category != 'pet' or (row['pet_enabled_since'] is not None and row['pet_enabled_since'] <= occurred_at)


@router.get('/status')
def status(request: Request, principal: Annotated[Principal, Depends(current_user)]):
    with request.app.state.database.connection() as connection:
        linked = connection.execute('SELECT 1 FROM telegram_links WHERE user_id=?', (principal.user.id,)).fetchone()
        return {'linked': bool(linked), 'notifications': preferences(connection, principal.user.id)}


@router.post('/link-code', status_code=201)
def issue_code(request: Request, principal: Annotated[Principal, Depends(write_user)]):
    now = int(time.time())
    with request.app.state.database.transaction() as connection:
        if connection.execute('SELECT 1 FROM telegram_links WHERE user_id=?', (principal.user.id,)).fetchone():
            raise HTTPException(409, 'Unlink the existing Telegram account first')
        old = connection.execute('SELECT created_at FROM telegram_link_codes WHERE user_id=?', (principal.user.id,)).fetchone()
        if old and now-old['created_at'] < 30:
            raise HTTPException(429, 'Wait before requesting another code', headers={'Retry-After': '30'})
        code = secrets.token_urlsafe(18)
        connection.execute('''INSERT INTO telegram_link_codes VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET code_hash=excluded.code_hash,
            created_at=excluded.created_at, expires_at=excluded.expires_at''',
            (principal.user.id, token_hash(code), now, now+300))
    return {'code': code, 'expires_in': 300}


@router.delete('/link-code', status_code=204)
def cancel_code(request: Request, principal: Annotated[Principal, Depends(write_user)]):
    with request.app.state.database.transaction() as connection:
        connection.execute('DELETE FROM telegram_link_codes WHERE user_id=?', (principal.user.id,))
    return Response(status_code=204)


def unlink(connection, user_id):
    connection.execute('DELETE FROM telegram_device_intents WHERE user_id=?',(user_id,))
    connection.execute('DELETE FROM telegram_device_drafts WHERE user_id=?',(user_id,))
    connection.execute('DELETE FROM telegram_replies WHERE user_id=?', (user_id,))
    connection.execute('DELETE FROM telegram_pet_drafts WHERE user_id=?', (user_id,))
    connection.execute('DELETE FROM telegram_pet_intents WHERE user_id=?', (user_id,))
    connection.execute('DELETE FROM telegram_links WHERE user_id=?', (user_id,))
    connection.execute('DELETE FROM telegram_link_codes WHERE user_id=?', (user_id,))
    connection.execute('UPDATE telegram_preferences SET enabled=0 WHERE user_id=?', (user_id,))
    connection.execute("INSERT INTO audit_log(actor_id, action) VALUES (?, 'telegram.unlinked')", (user_id,))


@router.delete('/link', status_code=204)
def unlink_account(request: Request, principal: Annotated[Principal, Depends(write_user)]):
    with request.app.state.database.transaction() as connection:
        unlink(connection, principal.user.id)
    return Response(status_code=204)
