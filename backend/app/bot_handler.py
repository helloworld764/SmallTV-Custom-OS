"""Trusted Telegram-update adapter. Only the local polling worker calls this."""

import json
import re
import time

from fastapi import HTTPException
from pydantic import ValidationError

from . import weather, bot_pets, bot_devices, bot_controls
from .security import token_hash
from .telegram import CATEGORIES, active_link, preferences, throttle, unlink

MENU = {'keyboard': [[{'text': 'Управление'}, {'text': 'Мои устройства'}], [{'text': 'Питомец'}],
                     [{'text': 'Настройки'}, {'text': 'Помощь'}]], 'resize_keyboard': True}
LABELS = {'codex': 'Codex', 'connection': 'Связь устройств', 'pet': 'Питомец', 'system': 'Система'}


def buttons(rows):
    return {'inline_keyboard': [[{'text': text, 'callback_data': data} for text, data in row] for row in rows]}


def identity(update):
    """Reject groups, bot senders, forwarded/edited messages and invalid identities."""
    if not isinstance(update, dict) or type(update.get('update_id')) is not int or not 0 <= update['update_id'] < 2**53:
        return None
    callback = update.get('callback_query')
    if isinstance(callback, dict) and (not isinstance(callback.get('id'),str) or not 0 < len(callback['id']) <= 256):
        return None
    message = callback.get('message') if isinstance(callback, dict) else update.get('message')
    sender = callback.get('from') if isinstance(callback, dict) else message.get('from') if isinstance(message, dict) else None
    if not isinstance(message, dict) or not isinstance(sender, dict):
        return None
    chat = message.get('chat', {})
    if not isinstance(chat, dict):
        return None
    who = sender.get('id')
    if (type(who) is not int or not 0 < who < 2**53 or sender.get('is_bot') is not False
            or chat.get('type') != 'private' or chat.get('id') != who
            or 'forward_origin' in message or 'forward_from' in message):
        return None
    text = callback.get('data') if isinstance(callback, dict) else message.get('text')
    if not isinstance(text, str) or not 0 < len(text) <= 256:
        return None
    return who, text.strip(), callback.get('id') if isinstance(callback, dict) else None


def process_update(database, settings, update, now=None):
    parsed = identity(update)
    if parsed is None:
        return None
    who, text, callback_id = parsed
    now = int(time.time()) if now is None else now
    # Slow provider I/O is outside SQLite's write transaction. Recheck identity below.
    match = city_error = prepared_user = None
    with database.connection() as connection:
        prior = active_link(connection, who)
        already = connection.execute('SELECT 1 FROM telegram_processed WHERE update_id=?', (update['update_id'],)).fetchone()
        if prior and not already and (text.startswith('/city ') or (not callback_id and prior['input_mode'] == 'city' and not text.startswith('/') and text not in ('Управление', 'Мои устройства', 'Питомец', 'Настройки', 'Помощь'))):
            prepared_user = prior['user_id']
            city = text[6:] if text.startswith('/city ') else text
            attempts = connection.execute('SELECT * FROM telegram_throttle WHERE bucket=?', (f'city:{who}',)).fetchone()
            city_allowed = not attempts or now-attempts['window_start'] >= 60 or attempts['attempts'] < 3
        else:
            city = None
    if city is not None:
        if not city_allowed:
            city_error = 'Слишком часто. Подождите минуту.'
        else:
            try:
                value = weather.CityChange(city=city)
                match = weather.geocode(value.city, settings.weather_timeout_seconds)
            except (HTTPException, ValidationError):
                city_error = 'Город не найден или поиск временно недоступен. Уточните название и повторите.'
    with database.transaction() as connection:
        if connection.execute('SELECT 1 FROM telegram_processed WHERE update_id=?', (update['update_id'],)).fetchone():
            return None
        connection.execute('INSERT INTO telegram_processed VALUES (?, ?)', (update['update_id'], now))
        connection.execute('DELETE FROM telegram_processed WHERE processed_at < ?', (now-7*86400,))
        connection.execute('DELETE FROM telegram_throttle WHERE window_start < ?', (now-86400,))
        if not throttle(connection, f'chat:{who}', now, 30):
            return None
        link = active_link(connection, who)
        uid = link['user_id'] if link else None
        if text.startswith('/link '):
            connection.execute('DELETE FROM telegram_device_drafts WHERE telegram_id=?',(who,))
            connection.execute('DELETE FROM telegram_pet_drafts WHERE telegram_id=?',(who,))
            reply, uid = claim(connection, who, text[6:], now)
            markup = MENU
        elif not link:
            reply = 'Storm Bot. Войдите в Storm Control Panel, откройте раздел Telegram и получите код. Отправьте сюда /link и код через пробел. Код действует 5 минут. Пароль и токен бота сюда вводить не нужно.'
            markup = MENU
        elif prepared_user == uid and city is not None:
            connection.execute('DELETE FROM telegram_device_drafts WHERE telegram_id=?',(who,))
            connection.execute('DELETE FROM telegram_pet_drafts WHERE telegram_id=?',(who,))
            if not throttle(connection, f'city:{who}', now, 3):
                reply = 'Слишком часто. Подождите минуту.'
            elif match:
                weather.store_preference(connection, uid, match)
                connection.execute("UPDATE telegram_links SET input_mode='' WHERE telegram_id=?", (who,))
                reply = f'Город сохранён: {match["name"]} ({match["timezone"]}). Он общий с веб-панелью.'
            else:
                reply = city_error
            markup = MENU
        elif bot_controls.accepts(text):
            connection.execute("UPDATE telegram_links SET input_mode='' WHERE telegram_id=?",(who,))
            connection.execute('DELETE FROM telegram_pet_drafts WHERE telegram_id=?',(who,))
            reply,markup=bot_controls.handle(connection,link,text,settings,now)
        elif bot_devices.is_device_input(connection, link, text, bool(callback_id)):
            connection.execute("UPDATE telegram_links SET input_mode='' WHERE telegram_id=?",(who,))
            connection.execute('DELETE FROM telegram_pet_drafts WHERE telegram_id=?',(who,))
            reply,markup=bot_devices.handle(connection,link,text,now,bool(callback_id))
        elif bot_pets.is_pet_input(connection, link, text, bool(callback_id)):
            connection.execute('DELETE FROM telegram_device_drafts WHERE telegram_id=?',(who,))
            connection.execute("UPDATE telegram_links SET input_mode='' WHERE telegram_id=?", (who,))
            reply, markup = bot_pets.handle(connection, link, text, now, bool(callback_id))
        else:
            connection.execute('DELETE FROM telegram_device_drafts WHERE telegram_id=?',(who,))
            connection.execute('DELETE FROM telegram_pet_drafts WHERE telegram_id=?', (who,))
            reply, markup = dispatch(connection, link, text, now)
            if text == 'unlink':
                uid = None
        # Durable command replies contain no raw incoming text or linking codes.
        connection.execute('DELETE FROM telegram_replies WHERE expires_at <= ?', (now,))
        count = connection.execute('SELECT count(*) FROM telegram_replies WHERE telegram_id=?', (who,)).fetchone()[0]
        if count >= 10:
            connection.execute('DELETE FROM telegram_replies WHERE update_id=(SELECT min(update_id) FROM telegram_replies WHERE telegram_id=?)', (who,))
        photo = markup.pop('_storm_photo', None)
        connection.execute('''INSERT INTO telegram_replies
            (update_id,telegram_id,user_id,text,markup,expires_at,photo_asset) VALUES (?,?,?,?,?,?,?)''',
            (update['update_id'],who,uid,reply,json.dumps(markup,ensure_ascii=False),now+300,photo))
    return callback_id


def claim(connection, who, code, now):
    if not throttle(connection, f'claim:{who}', now, 5):
        return 'Слишком много попыток. Подождите минуту.', None
    if connection.execute('SELECT 1 FROM telegram_links WHERE telegram_id=?', (who,)).fetchone():
        return 'Telegram уже связан с аккаунтом. Сначала отключите связь в панели.', None
    row = connection.execute('''SELECT telegram_link_codes.user_id FROM telegram_link_codes
        JOIN local_accounts ON local_accounts.user_id=telegram_link_codes.user_id
        WHERE code_hash=? AND expires_at>? AND is_active=1''', (token_hash(code), now)).fetchone() if re.fullmatch(r'[A-Za-z0-9_-]{24}', code) else None
    if row is None or connection.execute('SELECT 1 FROM telegram_links WHERE user_id=?', (row['user_id'],)).fetchone():
        return 'Код недействителен или истёк. Получите новый в панели.', None
    uid = row['user_id']
    connection.execute('INSERT INTO telegram_links(telegram_id,user_id,linked_at) VALUES (?, ?, ?)', (who, uid, now))
    connection.execute('DELETE FROM telegram_link_codes WHERE user_id=?', (uid,))
    connection.execute("INSERT INTO audit_log(actor_id,action) VALUES (?, 'telegram.linked')", (uid,))
    return 'Аккаунт подключён. Рассылка по умолчанию выключена. Выберите раздел меню.', uid


def dispatch(connection, link, text, now):
    uid, who = link['user_id'], link['telegram_id']
    if text not in ('/city', 'city') and not text.startswith('/city '):
        connection.execute("UPDATE telegram_links SET input_mode='' WHERE telegram_id=?", (who,))
    if text in ('/start', '/menu', 'menu'):
        return f'Здравствуйте, {link["display_name"]}! Storm Bot', MENU
    if text in ('Настройки', '/settings', 'settings'):
        place = weather._preference(connection, uid)
        return f'Город погоды: {place["city"]}.', buttons([[('Настройки устройства','device-settings')], [('Изменить город', 'city')], [('Уведомления', 'notifications')], [('Отключить Telegram', 'unlink-confirm')]])
    if text in ('/city', 'city'):
        connection.execute("UPDATE telegram_links SET input_mode='city' WHERE telegram_id=?", (who,))
        return 'Введите название города. Для отмены нажмите «Настройки».', MENU
    if text in ('notifications', '/notifications') or text.startswith('notify:'):
        if text.startswith('notify:'):
            key = text[7:]
            if key not in ('enabled', *CATEGORIES):
                return 'Неизвестная настройка.', MENU
            connection.execute('INSERT INTO telegram_preferences(user_id) VALUES (?) ON CONFLICT(user_id) DO NOTHING', (uid,))
            connection.execute(f'UPDATE telegram_preferences SET {key}=1-{key} WHERE user_id=?', (uid,))
            if key in ('enabled','pet'):
                since = 'enabled_since' if key == 'enabled' else 'pet_enabled_since'
                connection.execute(f'UPDATE telegram_preferences SET {since}=CASE WHEN {key}=1 THEN ? ELSE NULL END WHERE user_id=?',(now,uid))
        pref = preferences(connection, uid)
        rows = [[('Выключить все' if pref['enabled'] else 'Включить уведомления', 'notify:enabled')]]
        rows += [[(('✓ ' if pref[key] else '— ')+LABELS[key], 'notify:'+key)] for key in CATEGORIES]
        status = '🔔 Уведомления включены.' if pref['enabled'] else '🔕 Все уведомления выключены.'
        return (status+'\n\nГалочки — сохранённый выбор категорий. Общий выключатель временно отключает все категории, сохраняя выбор.'
                '\n\nРаботают сообщения о завершении сна и потребностях питомца. Предупреждения о потребностях — при 20/100 и ниже, не чаще раза в 6 часов.'), buttons(rows)
    if text == 'unlink-confirm':
        return 'Отключить этот Telegram от аккаунта?', buttons([[('Да, отключить', 'unlink'), ('Отмена', 'settings')]])
    if text == 'unlink':
        unlink(connection, uid)
        return 'Связь отключена. Для повторного подключения получите новый код в панели.', MENU
    return 'Storm Bot работает в личном чате. «Управление» → быстрый пульт экранов и яркости. «Мои устройства» → настройки устройства. «Питомец» → состояние и уход. «Настройки» → город и категории уведомлений. Привязка аккаунта: код из веб-панели → /link код сюда. Настройки сохраняются на сервере; применение на физическом SmallTV пока не подтверждается. Пароль от аккаунта боту не нужен.', MENU
