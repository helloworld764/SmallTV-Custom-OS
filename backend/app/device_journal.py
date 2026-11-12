"""Private, paged event history; timeout events describe heartbeat freshness only."""
import base64,binascii,json
from typing import Annotated,Literal
from fastapi import APIRouter,Depends,HTTPException,Query,Request
from .auth import Principal,current_user,now

router=APIRouter(prefix='/api/v1/devices',tags=['device journal'])
SETTINGS=('brightness','theme','start_screen','clock_format','clock_seconds','clock_style','device_style','clock_layout','advanced')
SCREENS=('clock','weather','monitor','codex','null','tamagotchi','markets')
KINDS=('device.renamed','settings.changed','screen.changed','connection.online','connection.stale','connection.offline','connection.restored','connection.unknown','connection.revoked','connection.reset')
Group=Literal['all','name','settings','screen','connection']

def ready(c):
    # Domain helpers are also used to seed older, deliberately partial migration fixtures.
    return (c.execute('SELECT max(version) FROM schema_migrations').fetchone()[0] or 0)>=26

def safe_details(kind,value):
    if not isinstance(value,dict):return {}
    result={}
    if kind=='settings.changed':
        fields=value.get('fields',[])
        result['fields']=[key for key in SETTINGS if isinstance(fields,list) and key in fields]
        brightness=value.get('brightness')
        if type(brightness) is int and 0<=brightness<=100:result['brightness']=brightness
    if kind=='screen.changed':
        if value.get('screen') in SCREENS:result['screen']=value['screen']
        if value.get('action') in ('next','previous','screen','home'):result['action']=value['action']
    return result

def record(c,device_id,kind,at=None,details=None,identifier=None):
    if not ready(c):return
    if kind not in KINDS:raise ValueError('Unsupported journal event')
    at=now() if at is None else at
    if identifier is None:
        # Device-local ordering preserves fast commands within the same second.
        prefix='record:'+device_id+':'
        previous=c.execute('SELECT id FROM device_journal WHERE device_id=? AND id>=? AND id<? ORDER BY id DESC LIMIT 1',(device_id,prefix,prefix+'~')).fetchone()
        sequence=int(previous['id'].rsplit(':',1)[1])+1 if previous else 1
        identifier=prefix+f'{sequence:020d}'
    c.execute('INSERT OR IGNORE INTO device_journal VALUES (?,?,?,?,?)',
              (identifier,device_id,kind,at,json.dumps(safe_details(kind,details or {}))))

def timeouts(anchor,now):
    if not anchor or anchor['clock_unknown'] or now<anchor['last_seen_at']:return []
    entries=[]
    for state,seconds in [('stale',anchor['stale_seconds']),('offline',anchor['offline_seconds'])]:
        at=anchor['last_seen_at']+seconds
        if now>=at:entries.append({'id':f'presence:{anchor["last_seen_at"]}:{state}','kind':'connection.'+state,'occurred_at':at,'details':{}})
    return entries

def observe_heartbeat(c,device_id,settings,received_at):
    if not ready(c):return
    old=c.execute('SELECT * FROM device_journal_presence WHERE device_id=?',(device_id,)).fetchone()
    unknown=bool(old and received_at<old['last_seen_at'])
    if old is None:record(c,device_id,'connection.online',received_at)
    elif unknown:record(c,device_id,'connection.unknown',received_at)
    else:
        elapsed=timeouts(old,received_at)
        for event in elapsed:record(c,device_id,event['kind'],event['occurred_at'],identifier=event['id']+':'+device_id)
        if elapsed or old['clock_unknown']:record(c,device_id,'connection.restored',received_at)
    c.execute('INSERT INTO device_journal_presence VALUES (?,?,?,?,?) ON CONFLICT(device_id) DO UPDATE SET last_seen_at=excluded.last_seen_at,stale_seconds=excluded.stale_seconds,offline_seconds=excluded.offline_seconds,clock_unknown=excluded.clock_unknown',
              (device_id,received_at,settings.device_stale_seconds,settings.device_offline_seconds,int(unknown)))

def reset_presence(c,device_id,kind,at):
    if not ready(c):return
    # Persist any timeouts already reached before a key is reset/revoked.
    anchor=c.execute('SELECT * FROM device_journal_presence WHERE device_id=?',(device_id,)).fetchone()
    for event in timeouts(anchor,at):record(c,device_id,event['kind'],event['occurred_at'],identifier=event['id']+':'+device_id)
    c.execute('DELETE FROM device_journal_presence WHERE device_id=?',(device_id,))
    record(c,device_id,kind,at)

def encode_cursor(event):
    return base64.urlsafe_b64encode(json.dumps([event['occurred_at'],event['id']],separators=(',',':')).encode()).decode().rstrip('=')

def decode_cursor(value):
    if value is None:return None
    try:
        raw=base64.b64decode(value+'='*(-len(value)%4),altchars=b'-_',validate=True)
        pair=json.loads(raw)
        if not isinstance(pair,list) or len(pair)!=2 or type(pair[0]) is not int or not 0<=pair[0]<=253402300799 or not isinstance(pair[1],str) or not 1<=len(pair[1])<=128:raise ValueError()
        return tuple(pair)
    except (ValueError,TypeError,binascii.Error,UnicodeError):raise HTTPException(422,'Invalid journal cursor') from None

def group_matches(kind,group):
    return group=='all' or (group=='name' and kind=='device.renamed') or kind.startswith(group+'.')

def page(c,device_id,now,group='all',since=None,until=None,cursor=None,limit=50):
    before=decode_cursor(cursor)
    args=[device_id]
    clauses=['device_id=?']
    if group!='all':clauses.append('kind LIKE ?');args.append('device.renamed' if group=='name' else group+'.%')
    if since is not None:clauses.append('occurred_at>=?');args.append(since)
    if until is not None:clauses.append('occurred_at<=?');args.append(until)
    if before:clauses.append('(occurred_at<? OR (occurred_at=? AND id<?))');args.extend((before[0],before[0],before[1]))
    rows=c.execute('SELECT id,kind,occurred_at,details FROM device_journal WHERE '+' AND '.join(clauses)+' ORDER BY occurred_at DESC,id DESC LIMIT ?',(*args,limit+1)).fetchall()
    entries=[]
    for row in rows:
        item=dict(row)
        try:details=json.loads(item['details'])
        except (ValueError,TypeError):details={}
        item['details']=safe_details(item['kind'],details);entries.append(item)
    anchor=c.execute('SELECT * FROM device_journal_presence WHERE device_id=?',(device_id,)).fetchone()
    for item in timeouts(anchor,now):
        item['id']+=':'+device_id
        key=(item['occurred_at'],item['id'])
        if group_matches(item['kind'],group) and (since is None or key[0]>=since) and (until is None or key[0]<=until) and (before is None or key<before):entries.append(item)
    ordered=sorted({item['id']:item for item in entries}.values(),key=lambda item:(item['occurred_at'],item['id']),reverse=True)
    items=ordered[:limit]
    return {'device_id':device_id,'server_time':now,'items':items,'next_cursor':encode_cursor(items[-1]) if len(ordered)>limit else None}

@router.get('/{device_id}/journal')
def journal(device_id:str,request:Request,principal:Annotated[Principal,Depends(current_user)],
            group:Group='all',since:Annotated[int|None,Query(ge=0,le=253402300799)]=None,
            until:Annotated[int|None,Query(ge=0,le=253402300799)]=None,
            cursor:Annotated[str|None,Query(max_length=256)]=None,
            limit:Annotated[int,Query(ge=1,le=100)]=50):
    from .enrollment import owned_device
    if since is not None and until is not None and since>until:raise HTTPException(422,'Invalid journal time range')
    with request.app.state.database.connection() as c:
        c.execute('BEGIN')  # One consistent read snapshot; no journal/presence writes.
        owned_device(c,device_id,principal)
        return page(c,device_id,now(),group,since,until,cursor,limit)
