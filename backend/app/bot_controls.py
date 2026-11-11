"""Quick remote scoped to the selected TV with replay-safe Telegram updates."""
from .bot_devices import buttons, intent, owned
from .device_controls import ControlCommand, apply_control, effective_screen
from .device_presence import presence_values
from .display_options import options
from uuid import uuid5, NAMESPACE_URL

SCREENS={'clock':'🕒 Часы','weather':'🌤 Погода','tamagotchi':'🐾 Питомец','markets':'💱 Курсы'}

def accepts(text):
    return text in ('Управление','/control','remote','remote-devices') or text.startswith(('remote-device:','rctl:'))

def card(connection,link,settings,now,prefix=''):
    device=owned(connection,link['user_id'],link['selected_device_id'])
    if not device:return 'Выберите устройство для управления.',buttons([[('📱 Выбрать устройство','remote-devices')]])
    did=device['device_id'];uid=link['user_id']
    is_preview=connection.execute('SELECT 1 FROM device_previews WHERE device_id=?',(did,)).fetchone()
    state='Локальное превью' if is_preview else presence_values(connection,did,settings,now).state
    active=effective_screen(device,now)
    visible=options(device.get('advanced',{})).screens
    title=SCREENS.get(active,{'monitor':'Мониторинг','codex':'Codex'}.get(active,active))
    text=f'Устройство: {device["name"]}\nЭкран: {title}\nЯркость: {device["brightness"]}%\nСвязь устройства: {state}\n\nПрименение на физическом SmallTV пока не подтверждается.'
    def token(action):return 'rctl:'+intent(connection,uid,did,'menu','remote:'+action,now).split(':',1)[1]
    rows=[[('◀️ Назад',token('previous')),('Вперёд ▶️',token('next'))]]
    for pair in [('clock','weather'),('tamagotchi','markets')]:
        rows.append([(('✓ ' if active==screen else '')+SCREENS[screen],token('screen:'+screen)) for screen in pair if screen in visible])
    rows=[row for row in rows if row]
    rows += [[('🔅 Темнее',token('dimmer')),('🔆 Ярче',token('brighter'))],
             [('🏠 Главный экран',token('home'))],[('Обновить','remote'),('📱 Выбрать устройство','remote-devices')]]
    return (prefix+'\n\n' if prefix else '')+text,buttons(rows)

def handle(connection,link,text,settings,now,update_id):
    uid,who=link['user_id'],link['telegram_id']
    connection.execute('DELETE FROM telegram_device_drafts WHERE telegram_id=?',(who,))
    connection.execute('DELETE FROM telegram_device_intents WHERE expires_at<=?',(now,))
    if text=='remote-devices':
        devices=connection.execute('SELECT id,name FROM devices WHERE owner_id=? ORDER BY id LIMIT 25',(uid,)).fetchall()
        return 'Выберите устройство для пульта.' if devices else 'Устройств пока нет. Добавьте локальное превью в веб-панели.',buttons([[('✓ '+r['name'] if r['id']==link['selected_device_id'] else r['name'],'remote-device:'+r['id'])] for r in devices])
    if text.startswith('remote-device:'):
        did=text.split(':',1)[1]
        if not owned(connection,uid,did):return card(connection,link,settings,now,'Устройство недоступно этому аккаунту.')
        connection.execute('UPDATE telegram_links SET selected_device_id=? WHERE telegram_id=?',(did,who))
        return card(connection,dict(link)|{'selected_device_id':did},settings,now)
    if text.startswith('rctl:'):
        row=connection.execute('SELECT * FROM telegram_device_intents WHERE id=? AND user_id=? AND expires_at>?',
                               (text.split(':',1)[1],uid,now)).fetchone()
        if not row or row['device_id']!=link['selected_device_id'] or not owned(connection,uid,row['device_id']) or row['field']!='menu' or not row['value'].startswith('remote:'):
            return card(connection,link,settings,now,'Кнопка устарела, устройство сменилось или доступ отозван.')
        if row['consumed']:return card(connection,link,settings,now,'Эта команда уже выполнена. Используйте свежую карточку.')
        action=row['value'][7:];screen=None
        if action.startswith('screen:'): action,screen=action.split(':',1)
        command=ControlCommand(request_id=uuid5(NAMESPACE_URL,f'storm:telegram:{who}:{update_id}'),action=action,screen=screen)
        from fastapi import HTTPException
        try:apply_control(connection,row['device_id'],command,uid,now=now)
        except HTTPException as error:
            if error.status_code!=409:raise
            return card(connection,link,settings,now,'Этот экран скрыт в настройках веб-панели.')
        return card(connection,link,settings,now)
    return card(connection,link,settings,now)
