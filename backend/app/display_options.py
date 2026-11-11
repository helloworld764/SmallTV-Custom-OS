"""Bounded, declarative display options. No executable CSS or user URLs."""
import json
import time
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

Color=Annotated[str,Field(pattern=r'^#[0-9a-fA-F]{6}$')]
ClockTime=Annotated[str,Field(pattern=r'^(?:[01][0-9]|2[0-3]):[0-5][0-9]$')]
ScreenName=Literal['clock','weather','tamagotchi','markets']
ZONES=('local','UTC','Europe/Moscow','Europe/London','Europe/Berlin','Asia/Dubai','Asia/Tokyo','America/New_York')

class Palette(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    name:str=Field(min_length=1,max_length=24)
    background:Color='#080809'
    text:Color='#f4f4f6'
    accent:Color='#ff5264'

class DisplayOptions(BaseModel):
    model_config=ConfigDict(extra='forbid')
    custom_colors:bool=Field(default=False,strict=True)
    background:Color='#080809'
    text:Color='#f4f4f6'
    accent:Color='#ff5264'
    palettes:list[Palette]=Field(default_factory=list,max_length=8)
    backdrop:Literal['solid','gradient','grid','stars']='solid'
    screens:list[ScreenName]=Field(default_factory=lambda:['clock','weather','tamagotchi','markets'],min_length=1,max_length=4)
    auto_rotate:bool=Field(default=False,strict=True)
    rotate_seconds:int=Field(default=30,ge=10,le=600,strict=True)
    night_enabled:bool=Field(default=False,strict=True)
    night_start:ClockTime='22:00'
    night_end:ClockTime='08:00'
    night_brightness:int=Field(default=20,ge=0,le=100,strict=True)
    timezone:Literal['local','UTC','Europe/Moscow','Europe/London','Europe/Berlin','Asia/Dubai','Asia/Tokyo','America/New_York']='local'
    date_format:Literal['long','short','iso']='long'
    weekday:bool=Field(default=True,strict=True)
    day_progress:bool=Field(default=True,strict=True)
    notification_seconds:int=Field(default=4,ge=2,le=10,strict=True)
    quiet_enabled:bool=Field(default=False,strict=True)
    quiet_start:ClockTime='23:00'
    quiet_end:ClockTime='08:00'
    pet_phrases:bool=Field(default=True,strict=True)
    pet_motion:Literal['normal','reduced','off']='normal'

    @model_validator(mode='after')
    def no_duplicates(self):
        if len(set(self.screens))!=len(self.screens):raise ValueError('Duplicate screens')
        if len({p.name.casefold() for p in self.palettes})!=len(self.palettes):raise ValueError('Duplicate palette names')
        if self.night_enabled and self.night_start==self.night_end:raise ValueError('Night schedule must have different endpoints')
        if self.quiet_enabled and self.quiet_start==self.quiet_end:raise ValueError('Quiet schedule must have different endpoints')
        return self

def options(value):
    if isinstance(value,str):value=json.loads(value)
    return DisplayOptions.model_validate(value or {})

def screen_for(settings,now=None):
    now=int(time.time()) if now is None else now
    config=options(settings.get('advanced',{}))
    value=settings['current_screen'] or settings['start_screen']
    if value=='null':value='tamagotchi'
    if value not in config.screens and value not in ('monitor','codex'):value=config.screens[0]
    if config.auto_rotate:
        index=config.screens.index(value) if value in config.screens else 0
        elapsed=max(0,now-settings.get('screen_changed_at',now))
        value=config.screens[(index+elapsed//config.rotate_seconds)%len(config.screens)]
    return value
