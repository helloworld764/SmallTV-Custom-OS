"""Restricted identities for future Windows Codex agents; no task/event monitoring."""

import secrets
from typing import Annotated, Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field

from .auth import Principal, current_user, now, write_user
from .security import token_hash


router = APIRouter(prefix='/api/v1/agents/codex', tags=['Codex agent identities'])
bearer = HTTPBearer(auto_error=False, scheme_name='CodexAgentBearer')


class AgentRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=80)


class AgentView(BaseModel):
    id: str
    owner_id: str
    name: str
    created_at: str


class AgentCredential(BaseModel):
    agent_id: str
    token: str


class AgentIdentity(BaseModel):
    agent_id: str
    kind: Literal['codex_windows'] = 'codex_windows'


class HeartbeatRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')


class AgentPresence(BaseModel):
    agent_id: str
    state: Literal['UNKNOWN', 'ONLINE', 'STALE', 'OFFLINE']
    last_seen_at: int | None
    server_time: int
    stale_after_seconds: int
    offline_after_seconds: int


def audit(connection, actor: str, action: str, resource: str):
    connection.execute('INSERT INTO audit_log (actor_id, action, resource_id) VALUES (?, ?, ?)',
                       (actor, action, resource))


def owned_agent(connection, agent_id: str, principal: Principal):
    row = connection.execute("SELECT id, owner_id, name, created_at FROM codex_agents WHERE id = ? AND (owner_id = ? OR ? = 'super_admin')",
                             (agent_id, principal.user.id, principal.user.role)).fetchone()
    if row is None:
        raise HTTPException(404, 'Agent not found')
    return dict(row)


@router.post('', response_model=AgentCredential, status_code=201)
def register(payload: AgentRequest, request: Request, principal: Annotated[Principal, Depends(write_user)]):
    agent_id = str(uuid4())
    token = 'smalltv_agent_' + secrets.token_urlsafe(32)
    with request.app.state.database.transaction() as connection:
        if connection.execute('SELECT COUNT(*) FROM codex_agents WHERE owner_id = ?', (principal.user.id,)).fetchone()[0] >= 20:
            raise HTTPException(429, 'Agent registration limit reached; reuse an existing registration')
        connection.execute('INSERT INTO codex_agents (id, owner_id, name) VALUES (?, ?, ?)', (agent_id, principal.user.id, payload.name))
        connection.execute('INSERT INTO agent_credentials VALUES (?, ?, ?, NULL)', (agent_id, token_hash(token), now()))
        audit(connection, principal.user.id, 'agent.registered', agent_id)
    return AgentCredential(agent_id=agent_id, token=token)


@router.get('', response_model=list[AgentView])
def list_agents(request: Request, principal: Annotated[Principal, Depends(current_user)],
                limit: Annotated[int, Query(ge=1, le=100)] = 50,
                offset: Annotated[int, Query(ge=0)] = 0):
    with request.app.state.database.connection() as connection:
        return [dict(row) for row in connection.execute("""
            SELECT id, owner_id, name, created_at FROM codex_agents
            WHERE owner_id = ? OR ? = 'super_admin' ORDER BY id LIMIT ? OFFSET ?
        """, (principal.user.id, principal.user.role, limit, offset)).fetchall()]


def authenticated_agent(request: Request,
                        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]):
    if credentials is None or not credentials.credentials.startswith('smalltv_agent_') or len(credentials.credentials) > 128:
        raise HTTPException(401, 'Agent authentication required', headers={'WWW-Authenticate': 'Bearer'})
    with request.app.state.database.connection() as connection:
        row = connection.execute('''SELECT codex_agents.id FROM agent_credentials
            JOIN codex_agents ON codex_agents.id = agent_credentials.agent_id
            JOIN local_accounts ON local_accounts.user_id = codex_agents.owner_id
            WHERE token_hash = ? AND revoked_at IS NULL AND is_active = 1''',
            (token_hash(credentials.credentials),)).fetchone()
    if row is None:
        raise HTTPException(401, 'Agent authentication required', headers={'WWW-Authenticate': 'Bearer'})
    return row['id']


@router.get('/identity', response_model=AgentIdentity)
def identity(agent_id: Annotated[str, Depends(authenticated_agent)]):
    return AgentIdentity(agent_id=agent_id)


@router.post('/heartbeat', status_code=204)
def heartbeat(payload: HeartbeatRequest, request: Request,
              agent_id: Annotated[str, Depends(authenticated_agent)],
              credentials: Annotated[HTTPAuthorizationCredentials, Depends(bearer)]):
    with request.app.state.database.transaction() as connection:
        # Recheck under the write lock: revoke/rotate/disable may have raced
        # with the dependency lookup. Never refresh a superseded credential.
        valid = connection.execute('''SELECT 1 FROM agent_credentials
            JOIN codex_agents ON codex_agents.id = agent_credentials.agent_id
            JOIN local_accounts ON local_accounts.user_id = codex_agents.owner_id
            WHERE agent_id = ? AND token_hash = ? AND revoked_at IS NULL AND is_active = 1''',
            (agent_id, token_hash(credentials.credentials))).fetchone()
        if valid is None:
            raise HTTPException(401, 'Agent authentication required', headers={'WWW-Authenticate': 'Bearer'})
        received_at = now()
        previous = connection.execute('SELECT last_seen_at FROM agent_presence WHERE agent_id = ?', (agent_id,)).fetchone()
        if previous is not None and 0 <= received_at - previous['last_seen_at'] < 5:
            raise HTTPException(429, 'Heartbeat received too frequently',
                                headers={'Retry-After': str(5 - (received_at - previous['last_seen_at']))})
        connection.execute('''INSERT INTO agent_presence VALUES (?, ?)
            ON CONFLICT(agent_id) DO UPDATE SET last_seen_at = excluded.last_seen_at''', (agent_id, received_at))
    return Response(status_code=204)


@router.get('/{agent_id}/presence', response_model=AgentPresence)
def presence(agent_id: str, request: Request, principal: Annotated[Principal, Depends(current_user)]):
    with request.app.state.database.connection() as connection:
        owned_agent(connection, agent_id, principal)
        row = connection.execute('''SELECT agent_presence.last_seen_at,
            agent_credentials.revoked_at, local_accounts.is_active
            FROM codex_agents JOIN agent_credentials ON agent_credentials.agent_id = codex_agents.id
            JOIN local_accounts ON local_accounts.user_id = codex_agents.owner_id
            LEFT JOIN agent_presence ON agent_presence.agent_id = codex_agents.id
            WHERE codex_agents.id = ?''', (agent_id,)).fetchone()
    checked_at = now()
    settings = request.app.state.settings
    last_seen = row['last_seen_at']
    if row['revoked_at'] is not None or not row['is_active']:
        state = 'OFFLINE'
    elif last_seen is None or last_seen > checked_at:
        state = 'UNKNOWN'
    elif checked_at - last_seen >= settings.agent_offline_seconds:
        state = 'OFFLINE'
    elif checked_at - last_seen >= settings.agent_stale_seconds:
        state = 'STALE'
    else:
        state = 'ONLINE'
    return AgentPresence(agent_id=agent_id, state=state, last_seen_at=last_seen,
                         server_time=checked_at, stale_after_seconds=settings.agent_stale_seconds,
                         offline_after_seconds=settings.agent_offline_seconds)


@router.get('/{agent_id}', response_model=AgentView)
def agent_details(agent_id: str, request: Request, principal: Annotated[Principal, Depends(current_user)]):
    with request.app.state.database.connection() as connection:
        return owned_agent(connection, agent_id, principal)


@router.post('/{agent_id}/credentials/revoke', status_code=204)
def revoke(agent_id: str, request: Request, principal: Annotated[Principal, Depends(write_user)]):
    with request.app.state.database.transaction() as connection:
        owned_agent(connection, agent_id, principal)
        connection.execute('UPDATE agent_credentials SET revoked_at = ? WHERE agent_id = ?', (now(), agent_id))
        connection.execute('DELETE FROM agent_presence WHERE agent_id = ?', (agent_id,))
        audit(connection, principal.user.id, 'agent.credentials.revoked', agent_id)
    return Response(status_code=204)


@router.post('/{agent_id}/credentials/rotate', response_model=AgentCredential)
def rotate(agent_id: str, request: Request, principal: Annotated[Principal, Depends(write_user)]):
    token = 'smalltv_agent_' + secrets.token_urlsafe(32)
    with request.app.state.database.transaction() as connection:
        owned_agent(connection, agent_id, principal)
        connection.execute('''INSERT INTO agent_credentials VALUES (?, ?, ?, NULL)
            ON CONFLICT(agent_id) DO UPDATE SET token_hash = excluded.token_hash,
            created_at = excluded.created_at, revoked_at = NULL''', (agent_id, token_hash(token), now()))
        connection.execute('DELETE FROM agent_presence WHERE agent_id = ?', (agent_id,))
        audit(connection, principal.user.id, 'agent.credentials.rotated', agent_id)
    return AgentCredential(agent_id=agent_id, token=token)
