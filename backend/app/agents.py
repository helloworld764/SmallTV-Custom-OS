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


@router.get('/{agent_id}', response_model=AgentView)
def agent_details(agent_id: str, request: Request, principal: Annotated[Principal, Depends(current_user)]):
    with request.app.state.database.connection() as connection:
        return owned_agent(connection, agent_id, principal)


@router.post('/{agent_id}/credentials/revoke', status_code=204)
def revoke(agent_id: str, request: Request, principal: Annotated[Principal, Depends(write_user)]):
    with request.app.state.database.transaction() as connection:
        owned_agent(connection, agent_id, principal)
        connection.execute('UPDATE agent_credentials SET revoked_at = ? WHERE agent_id = ?', (now(), agent_id))
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
        audit(connection, principal.user.id, 'agent.credentials.rotated', agent_id)
    return AgentCredential(agent_id=agent_id, token=token)
