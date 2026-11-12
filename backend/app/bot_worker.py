"""Explicit local polling process: python -m backend.app.bot_worker run."""

import argparse
from contextlib import contextmanager
import json
import os
import re
import time

from .bot_handler import process_update, identity
from .bot_pets import photo_path
from .pet_sleep import complete_due_sleeps
from .pet_needs import scan_needs, current_notice
from .bot_transport import TelegramClient, TelegramError
from .config import Settings
from .database import Database
from .telegram import active_link, notification_allowed
from . import bot_custom_pets,custom_pets
from fastapi import HTTPException


@contextmanager
def worker_lock(path):
    """OS releases the non-blocking lock on exit/crash, Windows and Debian."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as handle:
        handle.seek(0, 2)
        if handle.tell() == 0:
            handle.write(b'0')
            handle.flush()
        handle.seek(0)
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == 'nt':
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


def initialize_bot(database, client):
    info = client.call('getMe')
    webhook = client.call('getWebhookInfo')
    if (not isinstance(info, dict) or info.get('is_bot') is not True
            or type(info.get('id')) is not int or not 0 < info['id'] < 2**53):
        raise ValueError('Invalid Telegram bot identity')
    if not isinstance(webhook, dict) or webhook.get('url'):
        raise ValueError('An existing webhook must be managed separately before polling')
    with database.transaction() as connection:
        row = connection.execute('SELECT bot_id FROM telegram_worker_state WHERE id=1').fetchone()
        if row['bot_id'] not in (None, info['id']):
            raise ValueError('This database is already associated with another Telegram bot')
        connection.execute('UPDATE telegram_worker_state SET bot_id=? WHERE id=1', (info['id'],))


def deliver_replies(database, client, now=None, edits_only=False):
    now = int(time.time()) if now is None else now
    with database.connection() as connection:
        rows = connection.execute('SELECT * FROM telegram_replies WHERE (?=0 OR edit_message_id IS NOT NULL) ORDER BY edit_message_id IS NULL,update_id LIMIT 20',
                                  (int(edits_only),)).fetchall()
    for row in rows:
        with database.connection() as connection:
            link = active_link(connection, row['telegram_id'])
            allowed = row['user_id'] is None or (link and link['user_id'] == row['user_id'])
            if row['notification_category']:
                allowed = allowed and notification_allowed(connection,row['user_id'],row['notification_category'],row['notification_at'])
            text=row['text']
            if row['need_fields']:
                text=current_notice(connection,row['user_id'],row['need_fields'],now)
                allowed=allowed and text is not None
            private_photo=None
            if row['photo_asset'] and row['photo_asset'].startswith(('custom:','draft:')):
                prefix,identifier=row['photo_asset'].split(':',1)
                if allowed and row['user_id']:
                    try:private_photo=custom_pets.private_image(connection,row['user_id'],identifier,now,draft=prefix=='draft',telegram=True)
                    except HTTPException:allowed=False
                else:allowed=False
        if row['expires_at'] > now and allowed:
            try:
                photo = photo_path(row['photo_asset'])
                markup = json.loads(row['markup'])
                entities=markup.pop('_storm_entities',None)
                if row['edit_message_id'] is not None:
                    client.call('editMessageText', {'chat_id': row['telegram_id'], 'message_id': row['edit_message_id'],
                                                  'text': text, 'reply_markup': markup})
                elif private_photo:
                    client.send_photo_bytes({'chat_id':row['telegram_id'],'caption':text,'reply_markup':markup},private_photo)
                elif photo and photo.is_file():
                    client.send_photo({'chat_id': row['telegram_id'], 'caption': text, 'reply_markup': markup}, photo)
                else:
                    client.call('sendMessage', {'chat_id': row['telegram_id'], 'text': text, 'reply_markup': markup,
                                               **({'entities':entities} if entities else {})})
            except TelegramError as error:
                if error.status not in (400, 403):
                    raise
                # Permanent delivery denial: disable future notification consent.
                if row['edit_message_id'] is None or error.status == 403:
                    with database.transaction() as connection:
                        connection.execute('UPDATE telegram_preferences SET enabled=0 WHERE user_id=?', (row['user_id'],))
        with database.transaction() as connection:
            connection.execute('DELETE FROM telegram_replies WHERE update_id=?', (row['update_id'],))


def poll_once(database, settings, client):
    with database.transaction() as connection:
        connection.execute('DELETE FROM pet_image_drafts WHERE confirmed_pet_id IS NULL AND expires_at<=?',(int(time.time()),))
    complete_due_sleeps(database)
    scan_needs(database)
    deliver_replies(database, client)
    with database.connection() as connection:
        offset = connection.execute('SELECT next_offset FROM telegram_worker_state WHERE id=1').fetchone()[0]
    updates = client.call('getUpdates', {'offset': offset, 'timeout': 25, 'limit': 20,
                                        'allowed_updates': ['message', 'callback_query']})
    if not isinstance(updates, list) or len(updates) > 20:
        raise TelegramError()
    for update in updates:
        if not isinstance(update, dict) or type(update.get('update_id')) is not int or not 0 <= update['update_id'] < 2**53:
            raise TelegramError()
        parsed = identity(update)
        with database.connection() as connection:
            seen = connection.execute('SELECT 1 FROM telegram_processed WHERE update_id=?',(update['update_id'],)).fetchone()
        if parsed and parsed[2] and not seen:
            try:
                client.call('answerCallbackQuery', {'callback_query_id': parsed[2]})
            except TelegramError:
                pass
        prepared=bot_custom_pets.prepare(database,client,update,int(time.time())) if parsed and not seen and parsed[1]=='pet-upload' else None
        process_update(database, settings, update,prepared_image=prepared)
        with database.transaction() as connection:
            connection.execute('UPDATE telegram_worker_state SET next_offset=max(next_offset, ?) WHERE id=1', (update['update_id']+1,))
        deliver_replies(database, client, edits_only=True)
    complete_due_sleeps(database)
    scan_needs(database)
    deliver_replies(database, client)


def main(argv=None):
    parser = argparse.ArgumentParser(description='Storm Bot local polling worker')
    parser.add_argument('command', choices=('check', 'run'))
    args = parser.parse_args(argv)
    try:
        settings = Settings()
    except Exception:
        print('Configuration is invalid. Check local settings; values are not printed.')
        return 1
    token = settings.telegram_bot_token.get_secret_value() if settings.telegram_bot_token else ''
    valid = bool(re.fullmatch(r'[0-9]{5,20}:[A-Za-z0-9_-]{25,100}', token))
    if args.command == 'check':
        print(json.dumps({'token_configured': bool(token), 'token_format_valid': valid,
                          'network_checked': False}))
        return 0 if valid else 1
    if not valid:
        print('Set SMALLTV_TELEGRAM_BOT_TOKEN in the local .env; do not pass it as an argument.')
        return 1
    database = Database(settings.db_path)
    database.initialize()
    client = TelegramClient(token)
    try:
        with worker_lock(database.path.with_suffix('.telegram.lock')):
            initialize_bot(database, client)
            print('Storm Bot polling started. Press Ctrl+C to stop.')
            delay = 1
            while True:
                try:
                    poll_once(database, settings, client)
                    delay = 1
                except TelegramError as error:
                    if error.status in (401, 409):
                        print('Telegram rejected polling (authentication or competing worker). Stopped.')
                        return 1
                    delay = min(30, delay*2)
                    time.sleep(max(delay, error.retry_after))
    except KeyboardInterrupt:
        print('Storm Bot stopped.')
        return 0
    except (OSError, TelegramError, ValueError):
        print('Storm Bot could not start. Check connectivity, bot configuration and whether another worker is running.')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
