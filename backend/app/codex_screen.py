"""Owner-scoped display snapshot. Reported completion and live reachability are separate."""
from typing import Annotated
from fastapi import APIRouter, Depends, Request
from .auth import Principal,current_user,now
from .enrollment import owned_device

router=APIRouter(prefix='/api/v1/devices',tags=['Codex screen'])

def agent_state(row,checked,settings):
    seen=row['last_seen_at']
    if row['revoked_at'] is not None or not row['is_active']:return 'OFFLINE'
    if seen is None or seen>checked:return 'UNKNOWN'
    if checked-seen>=settings.agent_offline_seconds:return 'OFFLINE'
    if checked-seen>=settings.agent_stale_seconds:return 'STALE'
    return 'ONLINE'

@router.get('/{device_id}/codex')
def snapshot(device_id:str,request:Request,principal:Annotated[Principal,Depends(current_user)]):
    checked=now();settings=request.app.state.settings
    with request.app.state.database.connection() as c:
        c.execute('BEGIN')
        owner=owned_device(c,device_id,principal)['owner_id']
        return codex_for_owner(c,settings,device_id,owner,checked)

def codex_for_owner(c,settings,device_id,owner,checked):
    rows=c.execute('''SELECT a.id,a.name,p.last_seen_at,k.revoked_at,u.is_active FROM codex_agents a
        JOIN agent_credentials k ON k.agent_id=a.id JOIN local_accounts u ON u.user_id=a.owner_id
        LEFT JOIN agent_presence p ON p.agent_id=a.id WHERE a.owner_id=? ORDER BY a.id LIMIT 20''',(owner,)).fetchall()
    candidates=[]
    for row in rows:
        run=c.execute('''SELECT r.* FROM codex_runs r JOIN codex_events e ON e.agent_id=r.agent_id
            AND e.run_id=r.run_id AND e.sequence=1 WHERE r.agent_id=?
            ORDER BY r.started_at DESC,e.rowid DESC LIMIT 1''',(row['id'],)).fetchone()
        presence=agent_state(row,checked,settings)
        trustworthy=run is None or 0<=run['started_at']<=run['updated_at']<=checked
        state=('IDLE' if run is None else run['last_reported_state']) if presence=='ONLINE' and trustworthy else ('OFFLINE' if presence=='OFFLINE' else 'UNKNOWN')
        candidates.append((row,run,presence,state))
    candidates.sort(key=lambda x:(x[3]=='WORKING',x[2]=='ONLINE',x[1]['updated_at'] if x[1] else -1,x[0]['id']),reverse=True)
    base={'device_id':device_id,'server_time':checked,'stale_after_seconds':settings.agent_stale_seconds,
          'offline_after_seconds':settings.agent_offline_seconds,'agent_count':len(rows)}
    if not candidates:return {**base,'state':'UNKNOWN','presence':'UNKNOWN','agent_name':None,'last_seen_at':None,
                              'run':None,'reason':'no_agent'}
    row,run,presence,state=candidates[0]
    report=None
    if run:
        end=checked if state=='WORKING' else run['updated_at']
        duration=None if run['last_reported_state']=='WORKING' and state!='WORKING' else max(0,end-run['started_at'])
        if not 0<=run['started_at']<=run['updated_at']<=checked:duration=None
        report={'run_id':run['run_id'],'last_reported_state':run['last_reported_state'],'started_at':run['started_at'],
                'updated_at':run['updated_at'],'duration_seconds':duration}
    return {**base,'state':state,'presence':presence,'agent_name':row['name'],'last_seen_at':row['last_seen_at'],
            'run':report,'reason':None if presence=='ONLINE' else 'agent_unreachable'}
