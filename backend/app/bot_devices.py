"""Owner-only device controls. Old buttons cannot retarget another selected TV."""

import re
from uuid import uuid4

from fastapi import HTTPException
from pydantic import ValidationError

from .devices import SettingsPatch, save_settings

THEMES={'BlackArch Red':'Красно-чёрная','Dark Blue':'Синяя','Purple':'Фиолетовая','Light':'Светлая'}
SCREENS={'clock':'Часы','tamagotchi':'Tamagotchi','weather':'Погода — заглушка','monitor':'Debian Monitor — заглушка',
         'codex':'Codex — заглушка','null':'Null — заглушка'}
TOP_LEVEL=('Мои устройства','Питомец','Настройки','Помощь')
CLOCK_FORMATS={'24h':'24 часа','12h':'12 часов (AM/PM)'}
CLOCK_STYLES={'classic':'Классический','minimal':'Минимализм','terminal':'Терминал'}
CLOCK_SECONDS={'1':'Показывать секунды','0':'Без секунд'}


def buttons(rows):
    return {'inline_keyboard':[[{'text':label,'callback_data':data} for label,data in row] for row in rows]}


def is_device_input(connection,link,text,callback):
    if text in ('Мои устройства','/devices','devices','/device','device-settings') or text.startswith(('device:','dnav:','dset:')):
        return True
    return (not callback and not text.startswith('/') and text not in TOP_LEVEL
            and connection.execute('SELECT 1 FROM telegram_device_drafts WHERE telegram_id=? AND user_id=?',
                                   (link['telegram_id'],link['user_id'])).fetchone() is not None)


def owned(connection,uid,device_id):
    row=connection.execute('''SELECT devices.name,device_settings.* FROM devices
        JOIN device_settings ON device_settings.device_id=devices.id WHERE devices.id=? AND owner_id=?''',
        (device_id,uid)).fetchone()
    return dict(row) if row else None


def intent(connection,uid,device_id,field,value,now,expected=None):
    key=uuid4().hex
    connection.execute('INSERT INTO telegram_device_intents(id,user_id,device_id,field,value,expires_at,expected_value) VALUES (?,?,?,?,?,?,?)',
                       (key,uid,device_id,field,str(value),now+300,expected))
    connection.execute('''DELETE FROM telegram_device_intents WHERE user_id=? AND id IN (
        SELECT id FROM telegram_device_intents WHERE user_id=? ORDER BY rowid DESC LIMIT -1 OFFSET 100)''',(uid,uid))
    return ('dnav:' if field=='menu' else 'dset:')+key


def card(connection,link,now,prefix=''):
    device=owned(connection,link['user_id'],link['selected_device_id'])
    if not device:
        return 'Выберите своё устройство в списке.',buttons([[('Мои устройства','devices')]])
    uid,did=link['user_id'],device['device_id']
    text=(f'Устройство: {device["name"]}\nТема: {device["theme"]}\nЯркость: {device["brightness"]}%\n'
          f'Стартовый экран: {SCREENS.get(device["start_screen"],device["start_screen"])}\n\n'
          f'Часы: {CLOCK_FORMATS[device["clock_format"]]}, {"с секундами" if device["clock_seconds"] else "без секунд"}\n'
          f'Стиль часов: {CLOCK_STYLES[device["clock_style"]]}\n\n'
          'Это настройки на сервере. Симулятор показывает их в превью. Применение на физическом SmallTV пока не подтверждается.')
    rows=[[(label,intent(connection,uid,did,'menu',field,now))] for label,field in
          [('Тема','theme'),('Яркость','brightness'),('Стартовый экран','start_screen'),
           ('Формат часов','clock_format'),('Секунды','clock_seconds'),('Стиль часов','clock_style')]]
    rows += [[('Обновить','device-settings'),('Список устройств','devices')]]
    return (prefix+'\n\n' if prefix else '')+text,buttons(rows)


def handle(connection,link,text,now,callback=False):
    uid,who=link['user_id'],link['telegram_id']
    connection.execute('DELETE FROM telegram_device_intents WHERE expires_at<=?',(now,))
    draft=connection.execute('SELECT * FROM telegram_device_drafts WHERE telegram_id=? AND user_id=?',(who,uid)).fetchone()
    if text in ('Мои устройства','/devices','devices'):
        connection.execute('DELETE FROM telegram_device_drafts WHERE telegram_id=?',(who,))
        devices=connection.execute('SELECT id,name FROM devices WHERE owner_id=? ORDER BY id LIMIT 25',(uid,)).fetchall()
        rows=[[(('✓ ' if row['id']==link['selected_device_id'] else '')+row['name'][:40],'device:'+row['id'])]
              for row in devices if len(('device:'+row['id']).encode())<=64]
        return ('Выберите своё устройство (первые 25):' if rows else 'Устройств пока нет. Подключение физического SmallTV потребует прошивки.'),buttons(rows)
    if text.startswith('device:'):
        did=text[7:]
        connection.execute('DELETE FROM telegram_device_drafts WHERE telegram_id=?',(who,))
        if not owned(connection,uid,did):
            return 'Устройство недоступно этому аккаунту.',buttons([[('Мои устройства','devices')]])
        connection.execute('UPDATE telegram_links SET selected_device_id=? WHERE telegram_id=?',(did,who))
        return card(connection,dict(link)|{'selected_device_id':did},now)
    if text in ('/device','device-settings'):
        connection.execute('DELETE FROM telegram_device_drafts WHERE telegram_id=?',(who,))
        return card(connection,link,now)
    if text.startswith(('dnav:','dset:')):
        record=connection.execute('SELECT * FROM telegram_device_intents WHERE id=? AND user_id=? AND expires_at>?',
                                  (text.split(':',1)[1],uid,now)).fetchone()
        if (not record or record['device_id']!=link['selected_device_id'] or not owned(connection,uid,record['device_id'])):
            return card(connection,link,now,'Кнопка устарела, устройство сменилось или доступ отозван. Откройте свежие настройки.')
        connection.execute('DELETE FROM telegram_device_drafts WHERE telegram_id=?',(who,))
        did=record['device_id']
        if text.startswith('dnav:'):
            if record['field']!='menu': return card(connection,link,now,'Кнопка недоступна.')
            field=record['value']
            if field=='brightness-custom':
                connection.execute('INSERT INTO telegram_device_drafts VALUES (?,?,?,?,?)',(who,uid,did,now+300,owned(connection,uid,did)['brightness']))
                return 'Введите яркость целым числом от 0 до 100. На ввод есть 5 минут. 0 скрывает содержимое экрана, индикатор связи сохраняется.',buttons([[('Отмена','device-settings')]])
            device=owned(connection,uid,did)
            options={'theme':THEMES,'start_screen':SCREENS,'clock_format':CLOCK_FORMATS,
                     'clock_seconds':CLOCK_SECONDS,'clock_style':CLOCK_STYLES,
                     'brightness':{str(v):f'{v}%' for v in (0,10,25,50,75,100)}}.get(field)
            if options is None: return card(connection,link,now,'Настройка недоступна.')
            rows=[[(('✓ ' if str(device[field])==str(value) else '')+label,intent(connection,uid,did,field,value,now,str(device[field])))]
                  for value,label in options.items()]
            if field=='brightness': rows += [[('Своя яркость',intent(connection,uid,did,'menu','brightness-custom',now))]]
            rows += [[('Назад','device-settings')]]
            return f'Устройство: {device["name"]}. Выберите значение. Экраны с отметкой «заглушка» ещё не реализованы.',buttons(rows)
        if record['field']=='menu': return card(connection,link,now,'Кнопка недоступна.')
        if record['consumed']: return card(connection,link,now,'Эта настройка уже была сохранена. Показываю текущие значения.')
        if str(owned(connection,uid,did)[record['field']])!=record['expected_value']:
            return card(connection,link,now,'Эта настройка уже изменилась. Выберите значение из свежей карточки.')
        try:
            value=int(record['value']) if record['field']=='brightness' else record['value']
            if record['field']=='clock_seconds':
                if value not in ('0','1'): raise ValueError('Invalid seconds option')
                value=value=='1'
            payload=SettingsPatch(**{record['field']:value})
            save_settings(connection,did,payload,uid)
        except (ValueError,ValidationError,HTTPException):
            return card(connection,link,now,'Настройка недоступна. Обновите карточку.')
        connection.execute('UPDATE telegram_device_intents SET consumed=1 WHERE id=?',(record['id'],))
        return card(connection,link,now,'Настройка сохранена на сервере.')
    if draft and not callback:
        if draft['expires_at']<=now or draft['device_id']!=link['selected_device_id'] or not owned(connection,uid,draft['device_id']):
            connection.execute('DELETE FROM telegram_device_drafts WHERE telegram_id=?',(who,))
            return card(connection,link,now,'Ввод устарел или устройство сменилось. Откройте яркость заново.')
        if not re.fullmatch(r'0|[1-9][0-9]?|100',text):
            return 'Нужно целое число от 0 до 100. Например: 42.',buttons([[('Отмена','device-settings')]])
        if owned(connection,uid,draft['device_id'])['brightness']!=draft['expected_brightness']:
            connection.execute('DELETE FROM telegram_device_drafts WHERE telegram_id=?',(who,))
            return card(connection,link,now,'Яркость уже изменилась. Откройте ввод заново, чтобы не потерять новую настройку.')
        save_settings(connection,draft['device_id'],SettingsPatch(brightness=int(text)),uid)
        connection.execute('DELETE FROM telegram_device_drafts WHERE telegram_id=?',(who,))
        return card(connection,link,now,'Яркость сохранена на сервере.')
    return card(connection,link,now)
