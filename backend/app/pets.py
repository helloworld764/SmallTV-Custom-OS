"""Owner-only pet API and shared domain writes. No social access or hardware."""

from typing import Annotated, Literal
import unicodedata
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from . import auth, pet_engine as engine
from .auth import Principal, current_user, now, write_user


router = APIRouter(prefix='/api/v1/pets', tags=['pets'])
MAX_ACTIONS = 10000
Kind = Literal['Pixel', 'Byte', 'Glitch']
Action = Literal['feed', 'play', 'sleep', 'wake', 'return']
ReactionKind = Literal['ADOPT', 'FEED', 'PLAY', 'SLEEP', 'WAKE', 'RETURN']


class Adopt(BaseModel):
    model_config = ConfigDict(extra='forbid')
    kind: Kind
    name: str = Field(strict=True, min_length=1, max_length=96)

    @field_validator('name')
    @classmethod
    def valid_name(cls, value):
        if any(unicodedata.category(char).startswith('C') for char in value):
            raise ValueError('Pet name must not contain control characters')
        value = unicodedata.normalize('NFC', value).strip()
        if not 1 <= len(value) <= 24:
            raise ValueError('Pet name must contain 1 to 24 characters')
        return value


class PetAction(BaseModel):
    model_config = ConfigDict(extra='forbid')
    action_id: UUID
    action: Action
    confirm_restart: bool = Field(default=False, strict=True)

    @model_validator(mode='after')
    def confirmation(self):
        if (self.action == 'return') != self.confirm_restart:
            raise ValueError('Only return requires confirm_restart=true')
        return self


class Reaction(BaseModel):
    id: str
    kind: ReactionKind
    expires_at: int
    duration_ms: Literal[3000] = engine.REACTION_SECONDS * 1000


class PetView(BaseModel):
    id: str
    kind: Kind
    name: str
    satiety: float
    energy: float
    mood: float
    sleeping: bool
    lifecycle: Literal['ACTIVE', 'ABSENT', 'RETURN_AVAILABLE', 'UNKNOWN']
    clock_state: Literal['OK', 'UNKNOWN']
    emotion: Literal['content', 'hungry', 'tired', 'sad', 'sleepy', 'missing', 'unknown']
    round: int
    last_care_at: int
    disappeared_at: int
    return_available_at: int
    server_time: int
    reaction: Reaction | None


class ActionReceipt(BaseModel):
    action_id: str
    pet_id: str
    action: Action
    duplicate: bool
    applied_at: int
    round: int
    reaction: Reaction | None


def require_live_session(connection, principal):
    valid = connection.execute('''SELECT 1 FROM auth_sessions
        JOIN local_accounts ON local_accounts.user_id = auth_sessions.user_id
        WHERE auth_sessions.user_id = ? AND token_hash = ? AND expires_at > ? AND is_active = 1''',
        (principal.user.id, principal.session_hash, auth.now())).fetchone()
    if valid is None:
        raise HTTPException(401, 'Authentication required')


def find_pet(connection, owner_id, pet_id=None):
    row = connection.execute('''SELECT pets.*, pet_states.* FROM pets
        JOIN pet_states ON pet_states.pet_id = pets.id
        WHERE pets.owner_id = ? AND (? IS NULL OR pets.id = ?)''', (owner_id, pet_id, pet_id)).fetchone()
    if row is None:
        raise HTTPException(404, 'Pet not found')
    return dict(row)


def view(row, timestamp):
    state = engine.project(row, timestamp)
    reaction = None
    if state['lifecycle'] == 'ACTIVE' and 0 <= timestamp - row['reaction_at'] < engine.REACTION_TTL_SECONDS:
        reaction = Reaction(id=row['reaction_id'], kind=row['reaction_kind'],
                            expires_at=row['reaction_at'] + engine.REACTION_TTL_SECONDS)
    return PetView(id=row['id'], kind=row['kind'], name=row['name'],
                   **{key: round(state[key] / engine.UNIT, 2) for key in ('satiety', 'energy', 'mood')},
                   sleeping=bool(state['sleeping']) and state['lifecycle'] == 'ACTIVE',
                   lifecycle=state['lifecycle'], clock_state=state['clock_state'], emotion=engine.emotion(state),
                   round=row['round'], last_care_at=row['last_care_at'],
                   disappeared_at=state['disappeared_at'], return_available_at=state['return_available_at'],
                   server_time=timestamp, reaction=reaction)


@router.get('/catalog', response_model=list[Kind])
def catalog(principal: Annotated[Principal, Depends(current_user)]):
    # Null needs an explicit owner entitlement; never grant it to every admin.
    return ['Pixel', 'Byte', 'Glitch']


@router.post('', response_model=PetView, status_code=201)
def adopt(payload: Adopt, request: Request, principal: Annotated[Principal, Depends(write_user)]):
    with request.app.state.database.transaction() as connection:
        require_live_session(connection, principal)
        return adopt_owned(connection, principal.user.id, payload, now())


def adopt_owned(connection, owner_id, payload, timestamp):
    """Domain write shared by authorized adapters within their transaction."""
    identifier = str(uuid4())
    if connection.execute('SELECT 1 FROM pets WHERE owner_id = ?', (owner_id,)).fetchone():
        raise HTTPException(409, 'A pet already exists; adoption cannot reset its lifecycle')
    connection.execute('INSERT INTO pets VALUES (?, ?, ?, ?, ?)',
                       (identifier, owner_id, payload.kind, payload.name, timestamp))
    connection.execute('''INSERT INTO pet_states
        (pet_id, satiety, energy, mood, sleeping, updated_at, last_care_at, round,
         reaction_id, reaction_kind, reaction_at) VALUES (?, ?, ?, ?, 0, ?, ?, 1, ?, 'ADOPT', ?)''',
        (identifier, engine.INITIAL, engine.INITIAL, engine.INITIAL, timestamp, timestamp, 'adopt:' + identifier, timestamp))
    return view(find_pet(connection, owner_id), timestamp)


@router.get('/current', response_model=PetView)
def current(request: Request, principal: Annotated[Principal, Depends(current_user)]):
    with request.app.state.database.connection() as connection:
        row = find_pet(connection, principal.user.id)
    return view(row, now())  # Reads do not save time, refresh care or emit new reactions.


@router.post('/{pet_id}/actions', response_model=ActionReceipt, status_code=201)
def act(pet_id: UUID, payload: PetAction, request: Request, response: Response,
        principal: Annotated[Principal, Depends(write_user)]):
    with request.app.state.database.transaction() as connection:
        require_live_session(connection, principal)
        receipt = act_owned(connection, principal.user.id, str(pet_id), payload, now())
        if receipt.duplicate:
            response.status_code = 200
        return receipt


def act_owned(connection, owner_id, pet_id, payload, timestamp):
    """Apply an authorized owner's action without coupling to cookie sessions."""
    identifier = str(payload.action_id)
    row = find_pet(connection, owner_id, pet_id)
    previous = connection.execute('SELECT * FROM pet_actions WHERE pet_id = ? AND action_id = ?',
                                  (pet_id, identifier)).fetchone()
    if previous:
        if (previous['action'], bool(previous['confirmed'])) != (payload.action, payload.confirm_restart):
            raise HTTPException(409, 'Action identifier already used with different content')
        return ActionReceipt(action_id=identifier, pet_id=pet_id, action=payload.action, duplicate=True,
                             applied_at=previous['applied_at'], round=previous['round'], reaction=None)
    if connection.execute('SELECT COUNT(*) FROM pet_actions WHERE pet_id = ?', (pet_id,)).fetchone()[0] >= MAX_ACTIONS:
        raise HTTPException(429, 'Pet action history limit reached; operator review required')
    try:
        state = engine.apply(row, payload.action, timestamp)
    except engine.GameError as error:
        if error.retry_after is not None:
            raise HTTPException(429, str(error), headers={'Retry-After': str(error.retry_after)}) from None
        raise HTTPException(409, str(error)) from None
    connection.execute('''UPDATE pet_states SET satiety=?, energy=?, mood=?, sleeping=?, updated_at=?,
        last_care_at=?, round=?, feed_at=?, play_at=?, sleep_at=?, reaction_id=?, reaction_kind=?, reaction_at=?
        WHERE pet_id=?''', tuple(state[key] for key in
            ('satiety', 'energy', 'mood', 'sleeping', 'updated_at', 'last_care_at', 'round', 'feed_at', 'play_at', 'sleep_at'))
            + (identifier, payload.action.upper(), timestamp, pet_id))
    connection.execute('INSERT INTO pet_actions VALUES (?, ?, ?, ?, ?, ?)',
                       (pet_id, identifier, payload.action, int(payload.confirm_restart), timestamp, state['round']))
    return ActionReceipt(action_id=identifier, pet_id=pet_id, action=payload.action, duplicate=False,
                         applied_at=timestamp, round=state['round'],
                         reaction=Reaction(id=identifier, kind=payload.action.upper(), expires_at=timestamp + engine.REACTION_TTL_SECONDS))
