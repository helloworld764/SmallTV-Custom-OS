"""Durable completion of timed sleep; projections work even with no worker."""

import json
import time

from . import pets
from .telegram import notification_allowed


def complete_due_sleeps(database, now=None):
    now = int(time.time()) if now is None else now
    with database.transaction() as connection:
        sessions = connection.execute("SELECT * FROM pet_sleep_sessions WHERE status='pending' AND due_at<=? ORDER BY due_at,id LIMIT 100", (now,)).fetchall()
        for session in sessions:
            row = pets.find_pet(connection,session['user_id'],session['pet_id'])
            if row['round'] != session['round']:
                connection.execute("UPDATE pet_sleep_sessions SET status='cancelled' WHERE id=?",(session['id'],))
                continue
            if pets.view(row,now).clock_state != 'OK':
                continue
            # The pure time projection performs wake-up, including when this worker
            # is stopped. Never rebase game time/stats merely to deliver an event.
            connection.execute("UPDATE pet_sleep_sessions SET status='completed' WHERE id=?",(session['id'],))
            # No stale backlog after an outage, no subscription before account linking.
            if now-session['due_at'] >= 1800:
                continue
            link = connection.execute('''SELECT telegram_links.telegram_id,linked_at FROM telegram_links
                JOIN local_accounts ON local_accounts.user_id=telegram_links.user_id
                WHERE telegram_links.user_id=? AND is_active=1''',(session['user_id'],)).fetchone()
            if not link or link['linked_at'] > session['due_at'] or not notification_allowed(connection,session['user_id'],'pet',session['due_at']):
                continue
            if connection.execute('SELECT count(*) FROM telegram_replies WHERE telegram_id=?',(link['telegram_id'],)).fetchone()[0] >= 10:
                continue
            text = f'{row["name"]}: час сна завершён, питомец проснулся! Энергия восстановлена до 100/100 в момент пробуждения. Откройте свежую карточку, чтобы увидеть текущее состояние.'
            markup = json.dumps({'inline_keyboard':[[{'text':'Питомец','callback_data':'pet'}]]},ensure_ascii=False)
            connection.execute('''INSERT INTO telegram_replies
                (update_id,telegram_id,user_id,text,markup,expires_at,notification_category,notification_at)
                VALUES (?,?,?,?,?,?, 'pet',?)''',
                (-session['id'],link['telegram_id'],session['user_id'],text,markup,session['due_at']+1800,session['due_at']))
