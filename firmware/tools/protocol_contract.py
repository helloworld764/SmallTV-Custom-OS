"""Host-side contract for the existing wire protocol; not an embedded dependency."""
import json
from typing import Any,Annotated,Literal
from pydantic import BaseModel,ConfigDict,Field
from backend.app.display_options import DisplayOptions

Counter=Annotated[int,Field(strict=True,ge=0,le=9007199254740991)]
Identifier=Annotated[str,Field(strict=True,min_length=1,max_length=128)]
Screen=Literal['clock','weather','codex','tamagotchi','markets','monitor']
LegacyScreen=Literal['clock','weather','codex','tamagotchi','markets','monitor','null']

class WireModel(BaseModel):
    # Version 1 may gain additive fields. Consumers do not depend on unknown keys.
    model_config=ConfigDict(strict=True,extra='ignore')

class DisplaySettings(WireModel):
    brightness:Annotated[int,Field(ge=0,le=100)]
    theme:Literal['BlackArch Red','Dark Blue','Purple','Light']
    start_screen:LegacyScreen
    clock_format:Literal['12h','24h']
    clock_seconds:bool
    device_style:Literal['classic','minimal','terminal','orbit']
    clock_layout:Literal['regular','tall','stacked','analog']
    current_screen:LegacyScreen|None
    screen_changed_at:Counter
    advanced:DisplayOptions

class DisplayEnvelope(WireModel):
    schema_version:Annotated[int,Field(ge=1,le=1)]
    device_id:Identifier
    server_time:Counter
    poll_after_seconds:Annotated[int,Field(ge=5,le=5)]
    screen:Screen
    settings:DisplaySettings
    # Screen-specific data remains the existing weather/pet/Codex contract.
    data:dict[str,Any]

class CompletionEvent(WireModel):
    id:Annotated[str,Field(min_length=1,max_length=256)]
    cursor:Annotated[int,Field(ge=1,le=9007199254740991)]
    kind:Literal['DONE','ERROR','INTERRUPTED']
    ttl_seconds:Annotated[int,Field(ge=1,le=60)]

class EventsEnvelope(WireModel):
    schema_version:Annotated[int,Field(ge=1,le=1)]
    device_id:Identifier
    server_time:Counter
    next_cursor:Counter
    events:Annotated[list[CompletionEvent],Field(max_length=20)]

def schema(model):
    def canonical(value):
        if isinstance(value,dict):
            return {key:sorted(item,key=repr) if key=='enum' else canonical(item) for key,item in value.items()}
        if isinstance(value,list):return [canonical(item) for item in value]
        return value
    # typing.Literal caches equivalent sets across import orders; enum order has no semantics.
    return canonical({'$schema':'https://json-schema.org/draft/2020-12/schema',**model.model_json_schema()})

def decode(raw,model,expected_device_id):
    """Validate before accepting any state/cursor; no token or raw body logging."""
    if not isinstance(raw,bytes) or len(raw)>16384:raise ValueError('Protocol response exceeds budget')
    def unique(pairs):
        result={}
        for key,value in pairs:
            if key in result:raise ValueError('Duplicate JSON key')
            result[key]=value
        return result
    try:
        payload=json.loads(raw,object_pairs_hook=unique,parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Nonfinite number')))
        parsed=model.model_validate(payload)
    except (ValueError,TypeError,RecursionError) as error:
        raise ValueError('Invalid protocol response') from None
    if parsed.device_id!=expected_device_id:raise ValueError('Wrong device response')
    return parsed
