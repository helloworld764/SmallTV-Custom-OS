"""Private single-image appearances; changing appearance never resets the game."""
import hashlib,time
from typing import Annotated
from uuid import UUID,uuid4
from fastapi import APIRouter,Depends,HTTPException,Request,Response
from pydantic import BaseModel,ConfigDict,Field,field_validator
from starlette.concurrency import run_in_threadpool
from . import pets
from .auth import Principal,current_user,write_user
from .image_processing import MAX_UPLOAD,normalize_pet

router=APIRouter(prefix='/api/v1/pets',tags=['private pet images'])
PROMPT=('Создай одного персонажа для пиксельной игры Tamagotchi на экране 240×240 и набор всех его базовых состояний.\n'
 'Описание персонажа: [ВСТАВЬ СЮДА ОПИСАНИЕ СВОЕГО ПИТОМЦА].\n\n'
 'Качественный 2D pixel art, чёткие пиксельные контуры, простой узнаваемый силуэт и выразительные глаза. '
 'Персонаж целиком, спереди, по центру, квадратная композиция 1:1, около 70% кадра. '
 'Во всех состояниях сохраняй одного и того же персонажа: силуэт, цвета, пропорции, детали, масштаб и положение в кадре. '
 'Без текста, интерфейса, посторонних предметов, теней на полу и фона. Прозрачный PNG. Читаемость при уменьшении до 96×96.\n\n'
 'Подготовь 9 отдельных PNG, по одному состоянию на файл:\n'
 '1. neutral.png — спокойная нейтральная поза, открытые глаза.\n'
 '2. happy.png — радость, довольная улыбка.\n'
 '3. eating.png — удовольствие от еды, жующие щёчки.\n'
 '4. playing.png — радость игры, небольшая игривая поза.\n'
 '5. sleeping.png — сон, закрытые глаза, расслабленная поза.\n'
 '6. waking.png — пробуждение и потягивание.\n'
 '7. hungry_sad.png — голод и лёгкая грусть.\n'
 '8. tired.png — усталость, сонные полуприкрытые глаза.\n'
 '9. blink.png — короткое моргание в нейтральной позе.\n\n'
 'Сначала выдай neutral.png отдельно, затем остальные файлы. Не объединяй состояния в коллаж или общий лист. '
 'Не меняй дизайн персонажа между изображениями; эмоции и позы должны оставаться узнаваемыми и ненавязчивыми.')

class Confirm(BaseModel):
    model_config=ConfigDict(extra='forbid')
    draft_id:UUID
    name:str=Field(strict=True,min_length=1,max_length=96)
    @field_validator('name')
    @classmethod
    def valid_name(cls,value):return pets.Adopt.valid_name(value)

def limit_upload(c,uid,now):
    row=c.execute('SELECT * FROM pet_image_limits WHERE user_id=?',(uid,)).fetchone()
    fresh=row and 0<=now-row['window_start']<60
    if fresh and row['attempts']>=5:raise HTTPException(429,'Too many image uploads',headers={'Retry-After':'60'})
    c.execute('INSERT INTO pet_image_limits VALUES (?,?,?) ON CONFLICT(user_id) DO UPDATE SET window_start=excluded.window_start,attempts=excluded.attempts',
              (uid,row['window_start'] if fresh else now,row['attempts']+1 if fresh else 1))

def store_draft(c,uid,images,now):
    image,telegram_image=images;digest=hashlib.sha256(image).hexdigest()
    c.execute('DELETE FROM pet_image_drafts WHERE confirmed_pet_id IS NULL AND expires_at<=?',(now,))
    previous=c.execute('SELECT * FROM pet_image_drafts WHERE user_id=? AND confirmed_pet_id IS NULL',(uid,)).fetchone()
    if previous and previous['sha256']==digest:return {'id':previous['id'],'expires_at':previous['expires_at']}
    c.execute('UPDATE telegram_custom_pet_inputs SET draft_id=NULL WHERE user_id=?',(uid,))
    c.execute('DELETE FROM pet_image_drafts WHERE user_id=? AND confirmed_pet_id IS NULL',(uid,))
    identifier=str(uuid4())
    c.execute('INSERT INTO pet_image_drafts(id,user_id,image,telegram_image,sha256,expires_at) VALUES (?,?,?,?,?,?)',
              (identifier,uid,image,telegram_image,digest,now+900))
    return {'id':identifier,'expires_at':now+900}

def confirm_owned(c,uid,identifier,name,now):
    name=pets.Adopt(kind='Pixel',name=name).name
    draft=c.execute('SELECT * FROM pet_image_drafts WHERE id=? AND user_id=?',(identifier,uid)).fetchone()
    if draft is None:raise HTTPException(404,'Pet image draft not found')
    if draft['confirmed_pet_id']:
        if draft['confirmed_name']!=name:raise HTTPException(409,'Draft already confirmed with another name')
        return {'duplicate':True,'pet':pets.view(pets.find_pet(c,uid),now).model_dump()}
    if draft['expires_at']<=now:raise HTTPException(410,'Pet image draft expired')
    if c.execute('SELECT count(*) FROM pet_image_drafts WHERE user_id=? AND confirmed_pet_id IS NOT NULL',(uid,)).fetchone()[0]>=100:
        raise HTTPException(429,'Appearance change history limit reached')
    previous=c.execute('SELECT id FROM pets WHERE owner_id=?',(uid,)).fetchone()
    if previous:
        pet_id=previous['id'];c.execute('UPDATE pets SET name=? WHERE id=?',(name,pet_id))
    else:pet_id=pets.adopt_owned(c,uid,pets.Adopt(kind='Pixel',name=name),now).id
    c.execute('INSERT INTO pet_appearances VALUES (?,?,?,?,?) ON CONFLICT(pet_id) DO UPDATE SET id=excluded.id,image=excluded.image,telegram_image=excluded.telegram_image,updated_at=excluded.updated_at',
              (pet_id,identifier,draft['image'],draft['telegram_image'],now))
    c.execute('UPDATE pet_image_drafts SET image=NULL,telegram_image=NULL,confirmed_pet_id=?,confirmed_name=? WHERE id=?',(pet_id,name,identifier))
    c.execute("INSERT INTO audit_log(actor_id,action,resource_id) VALUES (?,'pet.appearance.changed',?)",(uid,pet_id))
    return {'duplicate':False,'pet':pets.view(pets.find_pet(c,uid),now).model_dump()}

def private_image(c,uid,identifier,now,draft=False,telegram=False):
    column='telegram_image' if telegram else 'image'
    if draft:
        row=c.execute(f'SELECT {column} FROM pet_image_drafts WHERE id=? AND user_id=? AND confirmed_pet_id IS NULL AND expires_at>?',(identifier,uid,now)).fetchone()
    else:
        row=c.execute(f'SELECT a.{column} FROM pet_appearances a JOIN pets p ON p.id=a.pet_id WHERE a.id=? AND p.owner_id=?',(identifier,uid)).fetchone()
    if row is None:raise HTTPException(404,'Pet image not found')
    return bytes(row[0])

@router.get('/image-prompt')
def prompt(principal:Annotated[Principal,Depends(current_user)]):return {'prompt':PROMPT}

@router.post('/image-drafts',status_code=201)
async def upload(request:Request,principal:Annotated[Principal,Depends(write_user)]):
    now=int(time.time());db=request.app.state.database
    with db.transaction() as c:pets.require_live_session(c,principal);limit_upload(c,principal.user.id,now)
    mime=request.headers.get('content-type','').split(';',1)[0].lower()
    if mime not in ('image/png','image/jpeg'):raise HTTPException(415,'Only PNG/JPEG images are supported')
    raw=bytearray()
    async for chunk in request.stream():
        if len(raw)+len(chunk)>MAX_UPLOAD:raise HTTPException(413,'Image exceeds 2 MiB')
        raw.extend(chunk)
    images=await run_in_threadpool(normalize_pet,bytes(raw),mime)
    with db.transaction() as c:
        pets.require_live_session(c,principal)
        return store_draft(c,principal.user.id,images,int(time.time()))

@router.post('/image-confirm')
def confirm(payload:Confirm,request:Request,principal:Annotated[Principal,Depends(write_user)]):
    # Reuse precisely the existing Unicode/name validation; errors are sanitized by FastAPI.
    valid=pets.Adopt(kind='Pixel',name=payload.name)
    with request.app.state.database.transaction() as c:
        pets.require_live_session(c,principal)
        return confirm_owned(c,principal.user.id,str(payload.draft_id),valid.name,int(time.time()))

@router.get('/image-drafts/{identifier}/image')
def draft_image(identifier:UUID,request:Request,principal:Annotated[Principal,Depends(current_user)]):
    with request.app.state.database.connection() as c:raw=private_image(c,principal.user.id,str(identifier),int(time.time()),draft=True)
    return Response(raw,media_type='image/png',headers={'Cache-Control':'private, no-store'})

@router.get('/current/image/{identifier}')
def active_image(identifier:UUID,request:Request,principal:Annotated[Principal,Depends(current_user)]):
    with request.app.state.database.connection() as c:raw=private_image(c,principal.user.id,str(identifier),int(time.time()))
    return Response(raw,media_type='image/png',headers={'Cache-Control':'private, no-store'})

@router.delete('/image-drafts/{identifier}',status_code=204)
def cancel(identifier:UUID,request:Request,principal:Annotated[Principal,Depends(write_user)]):
    with request.app.state.database.transaction() as c:
        pets.require_live_session(c,principal)
        c.execute('DELETE FROM pet_image_drafts WHERE id=? AND user_id=? AND confirmed_pet_id IS NULL',(str(identifier),principal.user.id))
    return Response(status_code=204)
