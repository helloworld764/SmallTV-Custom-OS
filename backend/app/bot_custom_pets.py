"""Opt-in photo → name → preview → confirmation, with I/O outside DB locks."""
from uuid import uuid4
from fastapi import HTTPException
from pydantic import ValidationError
from . import custom_pets,pets
from .telegram import active_link
from .image_processing import normalize_pet,MAX_UPLOAD
from .bot_transport import TelegramError

def media(message):
    if not isinstance(message,dict):return None
    doc=message.get('document')
    if isinstance(doc,dict):return doc
    photos=message.get('photo')
    if isinstance(photos,list) and 0<len(photos)<=20:
        valid=[p for p in photos if isinstance(p,dict) and type(p.get('width')) is int and type(p.get('height')) is int and type(p.get('file_size')) is int and 0<p['file_size']<=MAX_UPLOAD]
        if valid:return {**max(valid,key=lambda p:p['width']*p['height']),'mime_type':'image/jpeg'}
    return None

def prepare(database,client,update,now):
    from .bot_handler import identity
    parsed=identity(update)
    if not parsed or parsed[1]!='pet-upload' or parsed[2]:return None
    who=parsed[0]
    with database.transaction() as c:
        link=active_link(c,who)
        row=c.execute('SELECT * FROM telegram_custom_pet_inputs WHERE telegram_id=? AND expires_at>?',(who,now)).fetchone()
        if not link or not row or row['user_id']!=link['user_id'] or c.execute('SELECT 1 FROM telegram_processed WHERE update_id=?',(update['update_id'],)).fetchone():return None
        uid,token=link['user_id'],row['token']
        try:custom_pets.limit_upload(c,uid,now)
        except HTTPException:return (uid,token,None,'Слишком много загрузок. Подождите минуту.')
    info=media(update['message'])
    if (not info or not isinstance(info.get('file_id'),str) or not 0<len(info['file_id'])<=256
            or type(info.get('file_size')) is not int or not 0<info['file_size']<=MAX_UPLOAD
            or info.get('mime_type') not in ('image/png','image/jpeg')):
        return uid,token,None,'Пришлите PNG/JPEG до 2 МБ. Для сохранения качества лучше отправить PNG как файл.'
    try:
        raw=client.download_image(info['file_id'])
        images=normalize_pet(raw,info['mime_type'])
        return uid,token,images,None
    except (TelegramError,HTTPException):return uid,token,None,'Не удалось загрузить изображение. Нужен обычный PNG/JPEG до 2 МБ и 4 млн пикселей, без анимации.'

def handle(c,link,text,now,callback,prepared=None):
    from .bot_pets import keyboard,card
    uid,who=link['user_id'],link['telegram_id']
    cancel=keyboard([[('Отмена','pet-cancel')]])
    row=c.execute('SELECT * FROM telegram_custom_pet_inputs WHERE telegram_id=? AND user_id=?',(who,uid)).fetchone()
    if text=='pcustom-start':
        c.execute('DELETE FROM telegram_pet_drafts WHERE telegram_id=?',(who,))
        c.execute('INSERT INTO telegram_custom_pet_inputs(telegram_id,user_id,token,step,expires_at) VALUES (?,?,?,\'photo\',?) ON CONFLICT(telegram_id) DO UPDATE SET user_id=excluded.user_id,token=excluded.token,step=\'photo\',draft_id=NULL,name=NULL,expires_at=excluded.expires_at',
                  (who,uid,uuid4().hex,now+900))
        return ('Свой питомец: пришлите PNG/JPEG до 2 МБ, затем введите имя и подтвердите превью. На каждый шаг — 15 минут. '
                'Новая картинка изменит только образ и имя существующего питомца, сохранив прогресс и ожидание возвращения. '
                'Прозрачный PNG лучше отправить как файл, без сжатия Telegram. Реакции для одного кадра — значки и подписи.\n\n'
                'Шаблон для внешнего генератора:\n'+custom_pets.PROMPT),cancel
    if text.startswith('pcustom-confirm:'):
        identifier=text.split(':',1)[1]
        previous=c.execute('SELECT confirmed_pet_id FROM pet_image_drafts WHERE id=? AND user_id=?',(identifier,uid)).fetchone()
        if previous and previous['confirmed_pet_id']:return card(c,uid,now,'Этот образ уже подтверждён.')
        if not row or row['expires_at']<=now or row['step']!='confirm' or row['draft_id']!=identifier:
            return 'Превью устарело. Начните создание своего образа заново.',keyboard([[('Создать своего','pcustom-start')]])
        try:custom_pets.confirm_owned(c,uid,identifier,row['name'],now)
        except HTTPException:return 'Образ не применён. Черновик устарел или недоступен.',keyboard([[('Начать заново','pcustom-start')]])
        c.execute('DELETE FROM telegram_custom_pet_inputs WHERE telegram_id=?',(who,))
        return card(c,uid,now,'Свой образ и имя сохранены!')
    if not row or row['expires_at']<=now:
        c.execute('DELETE FROM telegram_custom_pet_inputs WHERE telegram_id=?',(who,))
        return 'Сначала нажмите «Создать своего». Старый образ не изменён.',keyboard([[('Создать своего','pcustom-start')]])
    if text=='pet-upload':
        if not prepared or prepared[0]!=uid or prepared[1]!=row['token']:
            return 'Загрузка отменена или устарела. Начните заново.',keyboard([[('Создать своего','pcustom-start')]])
        if prepared[3]:return prepared[3],cancel
        draft=custom_pets.store_draft(c,uid,prepared[2],now)
        c.execute("UPDATE telegram_custom_pet_inputs SET draft_id=?,name=NULL,step='name',expires_at=? WHERE telegram_id=?",(draft['id'],now+900,who))
        return 'Изображение подготовлено. Теперь напишите имя питомца: 1–24 символа. Можно прислать другое изображение до подтверждения.',keyboard([[('Отмена','pet-cancel')]],'draft:'+draft['id'])
    if row['step']=='name' and not callback:
        try:name=pets.Adopt(kind='Pixel',name=text).name
        except ValidationError:return 'Имя — 1–24 символа, без управляющих и невидимых знаков. Попробуйте другое.',cancel
        valid=c.execute('SELECT 1 FROM pet_image_drafts WHERE id=? AND user_id=? AND confirmed_pet_id IS NULL AND expires_at>?',(row['draft_id'],uid,now)).fetchone()
        if not valid:return 'Черновик истёк. Пришлите изображение заново.',cancel
        c.execute('UPDATE pet_image_drafts SET expires_at=? WHERE id=?',(now+900,row['draft_id']))
        c.execute("UPDATE telegram_custom_pet_inputs SET name=?,step='confirm',expires_at=? WHERE telegram_id=?",(name,now+900,who))
        return f'{name} · свой питомец\nПодтвердить образ и имя? Прогресс существующего питомца сохранится. Пока ничего не применено.',keyboard([[('Подтвердить','pcustom-confirm:'+row['draft_id'])],[('Отмена','pet-cancel')]],'draft:'+row['draft_id'])
    return 'Пришлите изображение, затем имя и подтвердите превью кнопкой. Для отмены нажмите «Отмена».',cancel
