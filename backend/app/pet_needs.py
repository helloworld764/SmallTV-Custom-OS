"""Observe low-stat episodes without writing game state or replaying backlogs."""

import json
import time

from fastapi import HTTPException

from . import pet_engine, pets
from .bot_notifications import enqueue

LOW=20*pet_engine.UNIT
RECOVER=30*pet_engine.UNIT
SCAN_SECONDS=60
MAX_GAP=300
NOTICE_COOLDOWN=6*3600
FIELDS=('satiety','energy','mood')
LABELS={'satiety':'Пора покормить','energy':'Пора отдохнуть','mood':'Хочется поиграть'}


def need_text(name,state,fields):
    details='; '.join(f'{LABELS[key]} ({state[key]/pet_engine.UNIT:.0f}/100)' for key in fields)
    return f'{name}: {details}. Откройте карточку питомца, чтобы выбрать уход.'


def current_notice(connection,user_id,encoded_fields,now):
    """Drop recovered/stale warnings and never claim an already-resolved need."""
    try:
        fields=json.loads(encoded_fields)
        if not isinstance(fields,list) or any(key not in FIELDS for key in fields): return None
        row=pets.find_pet(connection,user_id)
        state=pet_engine.project(row,now)
    except (ValueError,TypeError,HTTPException):
        return None
    if state['lifecycle']!='ACTIVE' or state['sleeping']: return None
    fields=[key for key in FIELDS if key in fields and state[key]<=LOW]
    return need_text(row['name'],state,fields) if fields else None


def scan_needs(database,now=None):
    now=int(time.time()) if now is None else now
    with database.transaction() as connection:
        candidates=connection.execute('''SELECT pets.id,pets.owner_id FROM pets
            LEFT JOIN pet_need_tracking ON pet_need_tracking.pet_id=pets.id
            WHERE last_scan IS NULL OR last_scan<=? ORDER BY coalesce(last_scan,0),pets.id LIMIT 100''',
            (now-SCAN_SECONDS,)).fetchall()
        for candidate in candidates:
            row=pets.find_pet(connection,candidate['owner_id'],candidate['id'])
            state=pet_engine.project(row,now)
            previous=connection.execute('SELECT * FROM pet_need_tracking WHERE pet_id=?',(row['id'],)).fetchone()
            baseline=(previous is None or previous['round']!=row['round'] or now-previous['last_scan']>MAX_GAP
                      or state['lifecycle']!='ACTIVE' or state['sleeping'])
            flags={key:int(state[key]<=LOW) if baseline else
                   (0 if state[key]>=RECOVER else int(bool(previous[key]) or state[key]<=LOW)) for key in FIELDS}
            new_fields=[] if baseline else [key for key in FIELDS if flags[key] and not previous[key]]
            last_notice=previous['last_notice_at'] if previous else None
            if new_fields and (last_notice is None or now-last_notice>=NOTICE_COOLDOWN):
                markup={'inline_keyboard':[[{'text':'Питомец','callback_data':'pet'}]]}
                if enqueue(connection,row['owner_id'],need_text(row['name'],state,new_fields),markup,now,now+300,now,
                           consent_at=previous['last_scan'],need_fields=new_fields):
                    last_notice=now
            connection.execute('''INSERT INTO pet_need_tracking VALUES (?,?,?,?,?,?,?)
                ON CONFLICT(pet_id) DO UPDATE SET round=excluded.round,last_scan=excluded.last_scan,
                satiety=excluded.satiety,energy=excluded.energy,mood=excluded.mood,last_notice_at=excluded.last_notice_at''',
                (row['id'],row['round'],now,flags['satiety'],flags['energy'],flags['mood'],last_notice))
