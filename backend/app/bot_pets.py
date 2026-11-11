"""Small, owner-scoped pet UI over the existing domain operations."""

from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException
from pydantic import ValidationError

from . import pets

KINDS = ('Pixel', 'Byte', 'Glitch')
STATES = ('neutral', 'happy', 'eating', 'playing', 'sleeping', 'waking', 'hungry_sad', 'tired', 'blink')
PHOTOS = Path(__file__).resolve().parents[2] / 'web' / 'simulator' / 'pets' / 'bot'
REACTIONS = {'ADOPT':'happy', 'FEED':'eating', 'PLAY':'playing', 'SLEEP':'sleeping', 'WAKE':'waking', 'RETURN':'happy'}
EMOTIONS = {'content':'neutral', 'hungry':'hungry_sad', 'tired':'tired', 'sad':'hungry_sad', 'sleepy':'sleeping', 'missing':'hungry_sad', 'unknown':'neutral'}
EMOTION_TEXT = {'content':'Всё хорошо', 'hungry':'Хочется перекусить', 'tired':'Пора отдохнуть', 'sad':'Немного грустно', 'sleepy':'Спит', 'missing':'Питомец пропал', 'unknown':'Состояние неизвестно'}
ERRORS = {
    'Server clock precedes saved pet state':'Время сервера изменилось. Уход временно недоступен.',
    'Pet return is not available yet':'Вернуть питомца пока нельзя. Дождитесь указанного срока.',
    'Pet is absent; wait until return is available':'Питомец пропал. Дождитесь возможности возвращения.',
    'Wake the pet before this action':'Питомец спит. Сначала разбудите его.',
    'Pet is already full':'Питомец уже сыт.',
    'Pet cannot benefit from play right now':'Для игры нужно больше энергии или свободное место в настроении.',
    'Pet does not need to start sleeping':'Питомец уже спит или пока не устал.',
    'Pet is already awake':'Питомец уже бодрствует.',
    'Pet action history limit reached; operator review required':'Лимит истории ухода достигнут. Нужна проверка владельца сервера.',
}


def keyboard(rows, photo=None):
    markup = {'inline_keyboard': [[{'text':text, 'callback_data':data} for text,data in row] for row in rows]}
    if photo:
        markup['_storm_photo'] = photo
    return markup


def photo_path(key):
    """Only approved catalogue thumbnails; never arbitrary user/server paths."""
    if not isinstance(key, str):
        return None
    for kind in KINDS:
        for state in STATES:
            if key == f'{kind}-{state}':
                return PHOTOS / (key+'.png')
    return None


def is_pet_input(connection, link, text, callback):
    if text in ('Питомец','/pet','pet','pet-create','pet-cancel') or text.startswith(('petkind:','pact:','pconfirm:')):
        return True
    return (not callback and not text.startswith('/') and text not in ('Управление','Мои устройства','Настройки','Помощь')
            and connection.execute('SELECT 1 FROM telegram_pet_drafts WHERE telegram_id=? AND user_id=?',
                                   (link['telegram_id'],link['user_id'])).fetchone() is not None)


def new_intent(connection, pet, action, now):
    token = uuid4().hex
    connection.execute('INSERT INTO telegram_pet_intents(id,user_id,pet_id,action,round,expires_at) VALUES (?,?,?,?,?,?)',
                       (token, pet['owner_id'], pet['id'], action, pet['round'], now+300))
    # Bound stale keyboards without deleting the authoritative pet action history.
    connection.execute('''DELETE FROM telegram_pet_intents WHERE user_id=? AND id IN (
        SELECT id FROM telegram_pet_intents WHERE user_id=? ORDER BY rowid DESC LIMIT -1 OFFSET 60)''',
        (pet['owner_id'],pet['owner_id']))
    return ('pconfirm:' if action == 'return' else 'pact:')+token


def card(connection, uid, now, prefix=''):
    try:
        row = pets.find_pet(connection,uid)
    except HTTPException:
        return 'Питомца ещё нет. Выберите одного из трёх базовых персонажей и придумайте имя.', keyboard([[('Создать питомца','pet-create')]])
    pet = pets.view(row,now)
    text = f'{pet.name} · {pet.kind}\nСытость: {pet.satiety:.0f}/100\nЭнергия: {pet.energy:.0f}/100\nНастроение: {pet.mood:.0f}/100\n'
    rows = []
    if pet.lifecycle == 'ACTIVE':
        text += EMOTION_TEXT[pet.emotion]
        if pet.sleeping and pet.sleep_ends_at:
            seconds = max(0,pet.sleep_ends_at-now)
            text += f'\nПроснётся через {seconds//60} мин {seconds%60} сек. Полный сон — 1 час и 100/100 энергии; раннее пробуждение не даёт прибавки.'
        actions = [('Разбудить','wake')] if pet.sleeping else [('Кормить','feed'),('Играть','play'),('Спать','sleep')]
        rows = [[(label,new_intent(connection,row,action,now))] for label,action in actions]
    elif pet.lifecycle == 'ABSENT':
        minutes = max(1,(pet.return_available_at-now+59)//60)
        text += f'Питомец пропал. Вернуть и начать заново можно через {minutes//60} ч {minutes%60} мин.'
    elif pet.lifecycle == 'RETURN_AVAILABLE':
        text += 'Можно вернуть питомца и начать заново с базовыми показателями.'
        rows = [[('Вернуть',new_intent(connection,row,'return',now))]]
    else:
        text += 'Время сервера не согласуется с сохранённым состоянием. Действия временно недоступны.'
    rows += [[('Обновить','pet')]]
    state = REACTIONS.get(pet.reaction.kind) if pet.reaction else EMOTIONS[pet.emotion]
    return (prefix+'\n\n' if prefix else '')+text, keyboard(rows,f'{pet.kind}-{state}')


def handle(connection, link, text, now, callback=False):
    uid,who = link['user_id'],link['telegram_id']
    connection.execute('DELETE FROM telegram_pet_intents WHERE expires_at<=?',(now,))
    draft = connection.execute('SELECT * FROM telegram_pet_drafts WHERE telegram_id=? AND user_id=?',(who,uid)).fetchone()
    if text in ('Питомец','/pet','pet','pet-cancel'):
        connection.execute('DELETE FROM telegram_pet_drafts WHERE telegram_id=?',(who,))
        if text == 'pet-cancel':
            connection.execute("DELETE FROM telegram_pet_intents WHERE user_id=? AND action='return' AND confirmed=1",(uid,))
        return card(connection,uid,now)
    if text == 'pet-create' or text.startswith('petkind:'):
        if connection.execute('SELECT 1 FROM pets WHERE owner_id=?',(uid,)).fetchone():
            return card(connection,uid,now,'Питомец уже создан. Созданием нового нельзя сбросить состояние.')
        if text == 'pet-create':
            return 'Выберите персонажа. Затем бот попросит имя.', keyboard([[(kind,'petkind:'+kind)] for kind in KINDS]+[[('Отмена','pet-cancel')]])
        kind = text[8:]
        if kind not in KINDS:
            return 'Этот персонаж недоступен.', keyboard([[('К питомцу','pet')]])
        connection.execute('''INSERT INTO telegram_pet_drafts VALUES (?,?,?,?) ON CONFLICT(telegram_id)
            DO UPDATE SET user_id=excluded.user_id,kind=excluded.kind,expires_at=excluded.expires_at''',(who,uid,kind,now+300))
        return f'Выбран {kind}. Напишите имя питомца: от 1 до 24 символов. На ввод есть 5 минут.', keyboard([[('Отмена','pet-cancel')]],kind+'-neutral')
    if text.startswith(('pact:','pconfirm:')):
        token = text.split(':',1)[1]
        intent = connection.execute('SELECT * FROM telegram_pet_intents WHERE id=? AND user_id=? AND expires_at>?',(token,uid,now)).fetchone()
        if not intent:
            return card(connection,uid,now,'Кнопка устарела или недоступна. Используйте свежую карточку.')
        row = pets.find_pet(connection,uid,intent['pet_id'])
        if row['round'] != intent['round']:
            return card(connection,uid,now,'Карточка относится к предыдущему старту. Обновите её.')
        if text.startswith('pconfirm:'):
            if intent['action'] != 'return':
                return card(connection,uid,now,'Кнопка недоступна.')
            connection.execute('UPDATE telegram_pet_intents SET confirmed=1 WHERE id=?',(token,))
            return 'Вернуть питомца и начать заново? Сытость, энергия и настроение станут 80/100. Имя и образ сохранятся.', keyboard([[('Да, вернуть','pact:'+token)],[('Отмена','pet-cancel')]])
        if intent['action'] == 'return' and not intent['confirmed']:
            return card(connection,uid,now,'Для возвращения сначала нажмите «Вернуть» и подтвердите новый старт.')
        try:
            receipt = pets.act_owned(connection,uid,row['id'],pets.PetAction(action_id=token,action=intent['action'],confirm_restart=intent['action']=='return'),now)
            prefix = 'Это действие уже было выполнено.' if receipt.duplicate else {'feed':'Питомец покушал!','play':'Поиграли!','sleep':'Сладких снов!','wake':'Доброе утро!','return':'Питомец вернулся. Начинаем заново!'}[intent['action']]
        except HTTPException as error:
            prefix = f'Подождите {error.headers["Retry-After"]} сек. перед этим действием.' if error.headers and 'Retry-After' in error.headers else ERRORS.get(error.detail,'Действие сейчас недоступно. Обновите карточку.')
        return card(connection,uid,now,prefix)
    if draft and not callback:
        if draft['expires_at'] <= now:
            connection.execute('DELETE FROM telegram_pet_drafts WHERE telegram_id=?',(who,))
            return 'Время ввода имени истекло. Выберите персонажа заново.', keyboard([[('Выбрать персонажа','pet-create')]])
        try:
            payload = pets.Adopt(kind=draft['kind'],name=text)
        except ValidationError:
            return 'Имя должно содержать от 1 до 24 символов, без управляющих и невидимых символов. Попробуйте другое.', keyboard([[('Отмена','pet-cancel')]])
        try:
            pets.adopt_owned(connection,uid,payload,now)
            prefix = 'Питомец создан!'
        except HTTPException:
            prefix = 'Питомец уже существует. Показываю его сохранённое состояние.'
        connection.execute('DELETE FROM telegram_pet_drafts WHERE telegram_id=?',(who,))
        return card(connection,uid,now,prefix)
    return card(connection,uid,now)
