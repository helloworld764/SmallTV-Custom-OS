"""Shared durable notification queue. Call inside an existing transaction."""

import json

from .telegram import notification_allowed


def enqueue(connection,user_id,text,markup,occurred_at,expires_at,now,consent_at=None,need_fields=None):
    consent_at = occurred_at if consent_at is None else consent_at
    if expires_at <= now:
        return False
    link=connection.execute('''SELECT telegram_id,linked_at FROM telegram_links
        JOIN local_accounts ON local_accounts.user_id=telegram_links.user_id
        WHERE telegram_links.user_id=? AND is_active=1''',(user_id,)).fetchone()
    if not link or link['linked_at']>consent_at or not notification_allowed(connection,user_id,'pet',consent_at):
        return False
    connection.execute('DELETE FROM telegram_replies WHERE expires_at<=?',(now,))
    if connection.execute('SELECT count(*) FROM telegram_replies WHERE telegram_id=?',(link['telegram_id'],)).fetchone()[0]>=10:
        return False
    identifier=connection.execute('SELECT next_id FROM telegram_notice_sequence WHERE id=1').fetchone()[0]
    connection.execute('UPDATE telegram_notice_sequence SET next_id=next_id+1 WHERE id=1')
    connection.execute('''INSERT INTO telegram_replies
        (update_id,telegram_id,user_id,text,markup,expires_at,notification_category,notification_at,need_fields)
        VALUES (?,?,?,?,?,?,'pet',?,?)''',
        (-identifier,link['telegram_id'],user_id,text,json.dumps(markup,ensure_ascii=False),expires_at,occurred_at,
         json.dumps(need_fields) if need_fields else None))
    return True
