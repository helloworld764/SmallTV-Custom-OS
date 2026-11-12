"""Private per-device image catalogue; decoded originals are never retained."""
import hashlib,json,time,unicodedata
from typing import Annotated
from uuid import UUID,uuid4
from fastapi import APIRouter,Depends,HTTPException,Query,Request,Response
from starlette.concurrency import run_in_threadpool
from .auth import Principal,current_user,write_user
from .enrollment import owned_device
from .pets import require_live_session
from .image_processing import MAX_UPLOAD,normalize_wallpaper
from .device_journal import record

router=APIRouter(prefix='/api/v1/devices',tags=['private wallpapers'])

def item(row):
    return {k:row[k] for k in ('id','label','created_at')}|{'width':240,'height':240,'bytes':len(row['image'])}

@router.get('/{device_id}/wallpapers')
def catalogue(device_id:str,request:Request,principal:Annotated[Principal,Depends(current_user)]):
    with request.app.state.database.connection() as c:
        owned_device(c,device_id,principal)
        return [item(row) for row in c.execute('SELECT * FROM device_wallpapers WHERE device_id=? ORDER BY created_at,id',(device_id,))]

@router.post('/{device_id}/wallpapers',status_code=201)
async def upload(device_id:str,request:Request,response:Response,principal:Annotated[Principal,Depends(write_user)],
                 label:Annotated[str,Query(min_length=1,max_length=40)]='Мои обои'):
    label=label.strip()
    if not label or any(unicodedata.category(ch).startswith('C') for ch in label):raise HTTPException(422,'Invalid wallpaper label')
    now=int(time.time());db=request.app.state.database
    with db.transaction() as c:
        require_live_session(c,principal);owned_device(c,device_id,principal)
        limit=c.execute('SELECT * FROM wallpaper_upload_limits WHERE user_id=?',(principal.user.id,)).fetchone()
        fresh=limit and 0<=now-limit['window_start']<60
        if fresh and limit['attempts']>=10:raise HTTPException(429,'Upload rate limit reached',headers={'Retry-After':str(60-(now-limit['window_start']))})
        c.execute('INSERT INTO wallpaper_upload_limits VALUES (?,?,?) ON CONFLICT(user_id) DO UPDATE SET window_start=excluded.window_start,attempts=excluded.attempts',
                  (principal.user.id,limit['window_start'] if fresh else now,limit['attempts']+1 if fresh else 1))
    mime=request.headers.get('content-type','').split(';',1)[0].strip().lower()
    if mime not in ('image/png','image/jpeg'):raise HTTPException(415,'Only PNG and JPEG images are supported')
    length=request.headers.get('content-length')
    if length:
        try:oversized=int(length)<1 or int(length)>MAX_UPLOAD
        except ValueError:raise HTTPException(400,'Invalid content length') from None
        if oversized:raise HTTPException(413,'Image exceeds 2 MiB limit')
    data=bytearray()
    async for chunk in request.stream():
        if len(data)+len(chunk)>MAX_UPLOAD:raise HTTPException(413,'Image exceeds 2 MiB limit')
        data.extend(chunk)
    image=await run_in_threadpool(normalize_wallpaper,bytes(data),mime)
    digest=hashlib.sha256(image).hexdigest()
    with db.transaction() as c:
        require_live_session(c,principal);owned_device(c,device_id,principal)
        previous=c.execute('SELECT * FROM device_wallpapers WHERE device_id=? AND sha256=?',(device_id,digest)).fetchone()
        if previous:
            response.status_code=200;return {'duplicate':True,'wallpaper':item(previous)}
        if c.execute('SELECT count(*) FROM device_wallpapers WHERE device_id=?',(device_id,)).fetchone()[0]>=5:raise HTTPException(409,'Wallpaper catalogue is full (5 images)')
        identifier=str(uuid4())
        c.execute('INSERT INTO device_wallpapers VALUES (?,?,?,?,?,?)',(identifier,device_id,label,digest,image,now))
        c.execute("INSERT INTO audit_log(actor_id,action,resource_id) VALUES (?,'wallpaper.uploaded',?)",(principal.user.id,device_id))
        row=c.execute('SELECT * FROM device_wallpapers WHERE id=?',(identifier,)).fetchone()
        return {'duplicate':False,'wallpaper':item(row)}

@router.get('/{device_id}/wallpapers/{wallpaper_id}')
def read_image(device_id:str,wallpaper_id:UUID,request:Request,principal:Annotated[Principal,Depends(current_user)]):
    with request.app.state.database.connection() as c:
        owned_device(c,device_id,principal)
        row=c.execute('SELECT image FROM device_wallpapers WHERE id=? AND device_id=?',(str(wallpaper_id),device_id)).fetchone()
        if row is None:raise HTTPException(404,'Wallpaper not found')
    return Response(bytes(row['image']),media_type='image/png',headers={'Cache-Control':'private, no-store','Content-Disposition':'inline; filename="storm-wallpaper.png"'})

@router.delete('/{device_id}/wallpapers/{wallpaper_id}',status_code=204)
def delete(device_id:str,wallpaper_id:UUID,request:Request,principal:Annotated[Principal,Depends(write_user)]):
    identifier=str(wallpaper_id)
    with request.app.state.database.transaction() as c:
        require_live_session(c,principal);owned_device(c,device_id,principal)
        row=c.execute('SELECT 1 FROM device_wallpapers WHERE id=? AND device_id=?',(identifier,device_id)).fetchone()
        if row is None:raise HTTPException(404,'Wallpaper not found')
        advanced=json.loads(c.execute('SELECT advanced FROM device_settings WHERE device_id=?',(device_id,)).fetchone()[0])
        if advanced.get('wallpaper_id')==identifier:
            if advanced.get('backdrop')=='wallpaper':raise HTTPException(409,'Choose and save another background before deleting the active wallpaper')
            advanced['wallpaper_id']=None
            c.execute('UPDATE device_settings SET advanced=? WHERE device_id=?',(json.dumps(advanced),device_id))
            record(c,device_id,'settings.changed',details={'fields':['advanced']})
        c.execute('DELETE FROM device_wallpapers WHERE id=? AND device_id=?',(identifier,device_id))
        c.execute("INSERT INTO audit_log(actor_id,action,resource_id) VALUES (?,'wallpaper.deleted',?)",(principal.user.id,device_id))
    return Response(status_code=204)
