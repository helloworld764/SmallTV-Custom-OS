"""Two local calendar days; current observations and daily ranges stay distinct."""
from datetime import datetime, timedelta
import json
import math
import time
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request
from .auth import Principal, current_user
from . import weather

router = APIRouter(prefix='/api/v1/weather', tags=['weather'])
DAILY = 'weather_code,temperature_2m_max,temperature_2m_min,apparent_temperature_max,apparent_temperature_min,sunrise,sunset,wind_speed_10m_max'

def number(value, low=-100, high=100):
    if isinstance(value, bool) or not isinstance(value, (float,int)) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError('Invalid weather measurement')
    return round(value,1)

def condition(value):
    if type(value) is not int or value not in weather.CONDITIONS: raise ValueError('Invalid weather code')
    icon, label = weather.CONDITIONS[value]
    return {'weather_code':value, 'condition':label, 'icon':icon}

def parse_forecast(data):
    current, daily = data['current'], data['daily']
    observed = datetime.fromisoformat(current['time'])
    dates = [observed.date(), observed.date()+timedelta(days=1)]
    if daily['time'] != [day.isoformat() for day in dates]: raise ValueError('Unexpected forecast dates')
    days=[]
    for index, day in enumerate(dates):
        row={'date':day.isoformat(), **condition(daily['weather_code'][index])}
        for key, source in [('min_c','temperature_2m_min'),('max_c','temperature_2m_max'),
                            ('feels_min_c','apparent_temperature_min'),('feels_max_c','apparent_temperature_max')]:
            row[key]=number(daily[source][index])
        if row['min_c'] > row['max_c'] or row['feels_min_c'] > row['feels_max_c']: raise ValueError('Invalid daily range')
        row['wind_max_ms']=number(daily['wind_speed_10m_max'][index],0,150)
        for key in ('sunrise','sunset'):
            value=daily[key][index]
            if value is not None:
                stamp=datetime.fromisoformat(value)
                if stamp.date()!=day: raise ValueError('Invalid solar date')
                value=stamp.strftime('%H:%M')
            row[key]=value
        days.append(row)
    return {'observed_at':current['time'], 'temperature_c':number(current['temperature_2m']),
            'feels_c':number(current['apparent_temperature']), 'wind_ms':number(current['wind_speed_10m'],0,150),
            **condition(current['weather_code']), 'today':days[0], 'tomorrow':days[1]}

@router.get('/forecast')
def forecast(request: Request, principal: Annotated[Principal,Depends(current_user)]):
    return forecast_for_owner(request,principal.user.id)

def forecast_for_owner(request,owner_id):
    settings=request.app.state.settings; db=request.app.state.database; now=int(time.time())
    with db.connection() as c:
        place=weather._preference(c,owner_id)
        key=weather._cache_key(place['latitude'],place['longitude'])+':'+place['timezone']
        row=c.execute('SELECT * FROM weather_forecast_cache WHERE cache_key=?',(key,)).fetchone()
    payload=json.loads(row['payload']) if row else None
    stale=False
    if row is None or not 0 <= now-row['fetched_at'] < settings.weather_cache_seconds:
        query=urlencode({'latitude':place['latitude'],'longitude':place['longitude'],'timezone':place['timezone'],
                         'current':'temperature_2m,apparent_temperature,weather_code,wind_speed_10m',
                         'daily':DAILY,'forecast_days':2,'wind_speed_unit':'ms'})
        try:
            payload=parse_forecast(weather._json_get('https://api.open-meteo.com/v1/forecast?'+query,settings.weather_timeout_seconds))
            with db.transaction() as c:
                c.execute('INSERT INTO weather_forecast_cache VALUES (?,?,?) ON CONFLICT(cache_key) DO UPDATE SET payload=excluded.payload,fetched_at=excluded.fetched_at',
                          (key,json.dumps(payload),now))
            updated=now
        except (OSError,ValueError,KeyError,TypeError,IndexError,OverflowError):
            if row is None or not 0 <= now-row['fetched_at'] <= weather.STALE_SECONDS:
                return {'available':False,'city':place['city'],'timezone':place['timezone'],'attribution':'Open-Meteo · GeoNames'}
            stale=True; updated=row['fetched_at']
    else: updated=row['fetched_at']
    return {'available':True,**payload,'city':place['city'],'timezone':place['timezone'],
            'stale':stale,'updated_at':updated,'attribution':'Open-Meteo · GeoNames'}
