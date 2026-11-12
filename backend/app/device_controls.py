"""Server-side desired display state; never a device delivery acknowledgement."""
import json
import time
from typing import Annotated, Literal
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, model_validator
from .auth import Principal, write_user
from .display_options import screen_for, options
from .device_journal import record

router=APIRouter(prefix='/api/v1/devices',tags=['device control'])
SCREEN_ORDER=('clock','weather','tamagotchi','markets','codex')

class ControlCommand(BaseModel):
    model_config=ConfigDict(extra='forbid')
    request_id: UUID
    action: Literal['next','previous','screen','dimmer','brighter','home']
    screen: Literal['clock','weather','tamagotchi','markets','codex'] | None = None

    @model_validator(mode='after')
    def screen_matches_action(self):
        if (self.action=='screen') != (self.screen is not None):
            raise ValueError('A screen is required only for screen actions')
        return self

def effective_screen(settings,now=None):
    return screen_for(settings,now)

def apply_control(connection,device_id,command,user_id,role='user',now=None):
    now=int(time.time()) if now is None else now
    row=connection.execute('''SELECT device_settings.* FROM device_settings JOIN devices ON devices.id=device_settings.device_id
        WHERE devices.id=? AND (owner_id=? OR ?='super_admin')''',(device_id,user_id,role)).fetchone()
    if row is None: raise HTTPException(404,'Device settings not found')
    settings=dict(row)
    previous_screen=effective_screen(settings,now)
    previous_brightness=settings['brightness']
    fingerprint=json.dumps({'device_id':device_id,'action':command.action,'screen':command.screen},sort_keys=True)
    connection.execute('DELETE FROM device_control_requests WHERE created_at<?',(now-86400,))
    old=connection.execute('SELECT * FROM device_control_requests WHERE actor_id=? AND request_id=?',
                           (user_id,str(command.request_id))).fetchone()
    if old:
        if old['fingerprint']!=fingerprint: raise HTTPException(409,'Request identifier already used for another command')
        return {**json.loads(old['result']),'replayed':True}
    action=command.action
    if action in ('next','previous'):
        active=effective_screen(settings,now)
        order=options(settings.get('advanced',{})).screens
        index=order.index(active) if active in order else (-1 if action=='next' else 0)
        settings['current_screen']=order[(index+(1 if action=='next' else -1))%len(order)]
    elif action=='screen':
        if command.screen not in options(settings.get('advanced',{})).screens:raise HTTPException(409,'Screen is hidden in device settings')
        settings['current_screen']=command.screen
    elif action=='home':settings['current_screen']=None
    elif action=='dimmer':settings['brightness']=max(0,settings['brightness']-10)
    elif action=='brighter':settings['brightness']=min(100,settings['brightness']+10)
    if action in ('next','previous','screen','home'):settings['screen_changed_at']=now
    connection.execute('UPDATE device_settings SET current_screen=?,brightness=?,screen_changed_at=? WHERE device_id=?',
                       (settings['current_screen'],settings['brightness'],settings['screen_changed_at'],device_id))
    settings['clock_seconds']=bool(settings['clock_seconds'])
    result={'device_id':device_id,'settings':settings,'replayed':False,'saved_on_server':True}
    connection.execute('INSERT INTO device_control_requests VALUES (?,?,?,?,?,?)',
                       (user_id,str(command.request_id),device_id,fingerprint,json.dumps(result),now))
    connection.execute("INSERT INTO audit_log(actor_id,action,resource_id) VALUES (?,'device.control.updated',?)",(user_id,device_id))
    if action in ('next','previous','screen','home') and (settings['current_screen']!=row['current_screen'] or effective_screen(settings,now)!=previous_screen):
        record(connection,device_id,'screen.changed',now,{'action':action,'screen':effective_screen(settings,now)})
    elif settings['brightness']!=previous_brightness:
        record(connection,device_id,'settings.changed',now,{'fields':['brightness'],'brightness':settings['brightness']})
    return result

@router.post('/{device_id}/control')
def control(device_id:str,payload:ControlCommand,request:Request,principal:Annotated[Principal,Depends(write_user)]):
    with request.app.state.database.transaction() as connection:
        return apply_control(connection,device_id,payload,principal.user.id,principal.user.role)
