"""Bounded read-only display data for a device key, independent of browser cookies."""
import json
import hashlib
import io
from functools import lru_cache
from pathlib import Path
from PIL import Image
from .raster import encode
from typing import Annotated,Literal
from uuid import UUID
from fastapi import APIRouter,Depends,HTTPException,Query,Request,Response
from fastapi.security import HTTPAuthorizationCredentials
from . import auth,pets,weather,weather_forecast,markets
from .enrollment import authenticated_device,bearer,require_device_key
from .display_options import options,screen_for
from .codex_screen import codex_for_owner
from .display_events import notifications_for_owner,MAX_CURSOR

router=APIRouter(prefix='/api/v1/devices',tags=['device display protocol'])
MAX_DISPLAY_BYTES=16384
ASSETS=Path(__file__).resolve().parents[2]/'firmware/assets/catalog-v1'
KINDS=('Pixel','Byte','Glitch')
STATES=('neutral','happy','eating','playing','sleeping','waking','hungry_sad','tired','blink')
Screen=Literal['clock','weather','codex','tamagotchi','markets','monitor']

def authorize(c,device_id,identity,credentials):
    if device_id!=identity:raise HTTPException(404,'Device not found')
    require_device_key(c,identity,credentials)
    return c.execute('SELECT owner_id FROM devices WHERE id=?',(device_id,)).fetchone()['owner_id']

def bounded(payload):
    encoded=json.dumps(payload,ensure_ascii=False,separators=(',',':')).encode('utf-8')
    if len(encoded)>MAX_DISPLAY_BYTES:raise HTTPException(503,'Display payload exceeds protocol budget')
    return Response(encoded,media_type='application/json',headers={'Cache-Control':'no-store'})

@router.get('/{device_id}/display')
def display(device_id:str,request:Request,identity:Annotated[str,Depends(authenticated_device)],
            credentials:Annotated[HTTPAuthorizationCredentials,Depends(bearer)],screen:Screen|None=None):
    db=request.app.state.database;checked=auth.now()
    with db.connection() as c:
        c.execute('BEGIN');owner=authorize(c,device_id,identity,credentials)
        settings=dict(c.execute('SELECT * FROM device_settings WHERE device_id=?',(device_id,)).fetchone())
    advanced=options(settings['advanced']).model_dump(mode='json')
    selected=screen or screen_for(settings,checked)
    if selected=='null':selected='tamagotchi'
    if screen and screen not in advanced['screens'] and screen!='monitor':raise HTTPException(409,'Screen is hidden')
    # Saved palette catalogue is editor data; the display needs only selected colors.
    advanced.pop('palettes',None)
    values={key:settings[key] for key in ('brightness','theme','start_screen','clock_format','clock_seconds','device_style','clock_layout','current_screen','screen_changed_at')}
    values['clock_seconds']=bool(values['clock_seconds']);values['advanced']=advanced
    data={}
    if selected=='clock':data={'weather':weather.weather_for_owner(request,owner)}
    elif selected=='weather':data=weather_forecast.forecast_for_owner(request,owner)
    elif selected=='markets':data=markets.cache.read()
    with db.connection() as c:
        c.execute('BEGIN');authorize(c,device_id,identity,credentials) # Recheck after provider I/O/revocation.
        if selected=='codex':data=codex_for_owner(c,request.app.state.settings,device_id,owner,checked)
        elif selected=='tamagotchi':
            try:
                pet=pets.view(pets.find_pet(c,owner),checked).model_dump(mode='json')
                state='sleeping' if pet['sleeping'] else {'hungry':'hungry_sad','sad':'hungry_sad','tired':'tired'}.get(pet['emotion'],'neutral')
                reaction=pet.get('reaction')
                if reaction:state={'FEED':'eating','PLAY':'playing','WAKE':'waking','ADOPT':'happy','RETURN':'happy'}.get(reaction['kind'],state)
                data={'available':True,'pet':pet,'asset':f'/api/v1/devices/{device_id}/display/assets/{pet["kind"]}/{state}'}
            except HTTPException as error:
                if error.status_code!=404:raise
                data={'available':False,'reason':'no_pet'}
        elif selected=='monitor':data={'available':False,'reason':'server_monitor_not_connected'}
    return bounded({'schema_version':1,'device_id':device_id,'server_time':checked,'poll_after_seconds':5,
                    'screen':selected,'settings':values,'data':data})

@router.get('/{device_id}/display/events')
def events(device_id:str,request:Request,identity:Annotated[str,Depends(authenticated_device)],
           credentials:Annotated[HTTPAuthorizationCredentials,Depends(bearer)],
           after:Annotated[int,Query(ge=0,le=MAX_CURSOR)]=0):
    with request.app.state.database.connection() as c:
        c.execute('BEGIN');owner=authorize(c,device_id,identity,credentials)
        page=notifications_for_owner(c,device_id,owner,auth.now(),after)
    return bounded({'schema_version':1,**page.model_dump(mode='json')})

@router.get('/{device_id}/display/assets/{kind}/{state}')
def asset(device_id:str,kind:str,state:str,request:Request,identity:Annotated[str,Depends(authenticated_device)],
          credentials:Annotated[HTTPAuthorizationCredentials,Depends(bearer)]):
    with request.app.state.database.connection() as c:authorize(c,device_id,identity,credentials)
    if kind not in KINDS or state not in STATES:raise HTTPException(404,'Asset not found')
    path=ASSETS/f'{kind}-{state}.spt'
    if not path.is_file():raise HTTPException(503,'Asset catalog unavailable')
    with path.open('rb') as stream:raw=stream.read(17421)
    if len(raw)>17420:raise HTTPException(503,'Asset exceeds protocol budget')
    return Response(raw,media_type='application/octet-stream',headers={'Cache-Control':'no-store','X-Content-SHA256':hashlib.sha256(raw).hexdigest()})

@router.get('/{device_id}/display/wallpaper/{identifier}')
def wallpaper(device_id:str,identifier:UUID,request:Request,identity:Annotated[str,Depends(authenticated_device)],
              credentials:Annotated[HTTPAuthorizationCredentials,Depends(bearer)],format:Literal['stp1','png']='stp1'):
    with request.app.state.database.connection() as c:
        c.execute('BEGIN');authorize(c,device_id,identity,credentials)
        row=c.execute('SELECT image FROM device_wallpapers WHERE device_id=? AND id=?',(device_id,str(identifier))).fetchone()
        if row is None:raise HTTPException(404,'Wallpaper not found')
    raw=bytes(row['image'])
    if format=='stp1':raw=compact_wallpaper(raw)
    return Response(raw,media_type='image/png' if format=='png' else 'application/octet-stream',headers={'Cache-Control':'no-store','X-Content-SHA256':hashlib.sha256(raw).hexdigest()})

@lru_cache(maxsize=8)
def compact_wallpaper(raw):
    with Image.open(io.BytesIO(raw)) as image:return encode(image,240,trim_transparency=False)
