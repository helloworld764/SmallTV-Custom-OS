"""Ordered, idempotent agent reports. No CLI adapter or notification delivery."""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel, ConfigDict, Field

from .agents import authenticated_agent, bearer, owned_agent, require_agent_key
from .auth import Principal, current_user, now


router = APIRouter(prefix='/api/v1/agents/codex', tags=['Codex reports'])
MAX_RUNS = 1000
MAX_EVENTS = 10000
EVENTS_PER_MINUTE = 120
RunState = Literal['WORKING', 'DONE', 'ERROR', 'INTERRUPTED']


class EventRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    event_id: UUID
    run_id: UUID
    sequence: int = Field(ge=1, le=10000, strict=True)
    kind: Literal['START', 'WORKING', 'DONE', 'ERROR', 'INTERRUPTED']
    occurred_at: int = Field(ge=0, le=253402300799, strict=True)


class EventReceipt(BaseModel):
    event_id: str
    run_id: str
    sequence: int
    duplicate: bool


class RunView(BaseModel):
    agent_id: str
    run_id: str
    last_reported_state: RunState
    last_sequence: int
    started_at: int
    updated_at: int


class EventView(BaseModel):
    event_id: str
    run_id: str
    sequence: int
    kind: Literal['START', 'WORKING', 'DONE', 'ERROR', 'INTERRUPTED']
    occurred_at: int
    received_at: int


@router.post('/events', response_model=EventReceipt, status_code=201)
def receive_event(payload: EventRequest, request: Request, response: Response,
                  agent_id: Annotated[str, Depends(authenticated_agent)],
                  credentials: Annotated[HTTPAuthorizationCredentials, Depends(bearer)]):
    event_id, run_id = str(payload.event_id), str(payload.run_id)
    with request.app.state.database.transaction() as connection:
        require_agent_key(connection, agent_id, credentials)
        previous = connection.execute('''SELECT run_id, sequence, kind, occurred_at FROM codex_events
            WHERE agent_id = ? AND event_id = ?''', (agent_id, event_id)).fetchone()
        if previous is not None:
            if tuple(previous) != (run_id, payload.sequence, payload.kind, payload.occurred_at):
                raise HTTPException(409, 'Event identifier already used with different content')
            response.status_code = 200
            return EventReceipt(event_id=event_id, run_id=run_id, sequence=payload.sequence, duplicate=True)
        run = connection.execute('SELECT * FROM codex_runs WHERE agent_id = ? AND run_id = ?',
                                 (agent_id, run_id)).fetchone()
        if run is None:
            if payload.kind != 'START' or payload.sequence != 1:
                raise HTTPException(409, 'A run must begin with START at sequence 1')
        elif run['last_reported_state'] != 'WORKING' or payload.kind == 'START' or payload.sequence != run['last_sequence'] + 1:
            raise HTTPException(409, 'Invalid run transition or event sequence')
        received_at = now()
        if connection.execute('SELECT COUNT(*) FROM codex_events WHERE agent_id = ? AND received_at > ?',
                              (agent_id, received_at - 60)).fetchone()[0] >= EVENTS_PER_MINUTE:
            raise HTTPException(429, 'Event rate limit reached', headers={'Retry-After': '60'})
        if connection.execute('SELECT COUNT(*) FROM codex_events WHERE agent_id = ?', (agent_id,)).fetchone()[0] >= MAX_EVENTS:
            raise HTTPException(429, 'Stored event limit reached; retention requires operator review')
        state = 'WORKING' if payload.kind == 'START' else payload.kind
        if run is None:
            if connection.execute('SELECT COUNT(*) FROM codex_runs WHERE agent_id = ?', (agent_id,)).fetchone()[0] >= MAX_RUNS:
                raise HTTPException(429, 'Stored run limit reached; retention requires operator review')
            connection.execute('''INSERT INTO codex_runs
                (agent_id, run_id, last_reported_state, last_sequence, started_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)''', (agent_id, run_id, state, payload.sequence, received_at, received_at))
        else:
            connection.execute('''UPDATE codex_runs SET last_reported_state = ?, last_sequence = ?, updated_at = ?
                WHERE agent_id = ? AND run_id = ?''', (state, payload.sequence, received_at, agent_id, run_id))
        connection.execute('''INSERT INTO codex_events
            (agent_id, event_id, run_id, sequence, kind, occurred_at, received_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)''',
            (agent_id, event_id, run_id, payload.sequence, payload.kind, payload.occurred_at, received_at))
    return EventReceipt(event_id=event_id, run_id=run_id, sequence=payload.sequence, duplicate=False)


@router.get('/{agent_id}/runs', response_model=list[RunView])
def list_runs(agent_id: str, request: Request, principal: Annotated[Principal, Depends(current_user)],
              limit: Annotated[int, Query(ge=1, le=100)] = 50,
              offset: Annotated[int, Query(ge=0)] = 0):
    with request.app.state.database.connection() as connection:
        owned_agent(connection, agent_id, principal)
        return [dict(row) for row in connection.execute('''SELECT * FROM codex_runs WHERE agent_id = ?
            ORDER BY updated_at DESC, run_id LIMIT ? OFFSET ?''', (agent_id, limit, offset))]


def find_run(connection, agent_id: str, run_id: str):
    row = connection.execute('SELECT * FROM codex_runs WHERE agent_id = ? AND run_id = ?', (agent_id, run_id)).fetchone()
    if row is None:
        raise HTTPException(404, 'Run not found')
    return dict(row)


@router.get('/{agent_id}/runs/{run_id}', response_model=RunView)
def run_details(agent_id: str, run_id: UUID, request: Request, principal: Annotated[Principal, Depends(current_user)]):
    with request.app.state.database.connection() as connection:
        owned_agent(connection, agent_id, principal)
        return find_run(connection, agent_id, str(run_id))


@router.get('/{agent_id}/runs/{run_id}/events', response_model=list[EventView])
def run_events(agent_id: str, run_id: UUID, request: Request, principal: Annotated[Principal, Depends(current_user)],
               after_sequence: Annotated[int, Query(ge=0)] = 0,
               limit: Annotated[int, Query(ge=1, le=100)] = 50):
    with request.app.state.database.connection() as connection:
        owned_agent(connection, agent_id, principal)
        find_run(connection, agent_id, str(run_id))
        return [dict(row) for row in connection.execute('''SELECT event_id, run_id, sequence, kind, occurred_at, received_at
            FROM codex_events WHERE agent_id = ? AND run_id = ? AND sequence > ? ORDER BY sequence LIMIT ?''',
            (agent_id, str(run_id), after_sequence, limit))]
