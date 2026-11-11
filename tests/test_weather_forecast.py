from datetime import datetime
from urllib.parse import parse_qs, urlparse
import pytest
from backend.app import weather
from backend.app.weather_forecast import parse_forecast
from test_weather import env

def sample():
    return {'current':{'time':'2026-11-11T12:15','temperature_2m':-3,'apparent_temperature':-7,'wind_speed_10m':4.2,'weather_code':71},
            'daily':{'time':['2026-11-11','2026-11-12'],'weather_code':[71,63], 'temperature_2m_min':[-5,-4],
                     'temperature_2m_max':[0,2],'apparent_temperature_min':[-10,-8], 'apparent_temperature_max':[-3,-1],
                     'sunrise':['2026-11-11T07:00','2026-11-12T07:02'],'sunset':['2026-11-11T16:00','2026-11-12T15:58'],
                     'wind_speed_10m_max':[7,9]}}

def test_two_day_forecast_contract_cache_and_stale_fallback(env,monkeypatch):
    client,_,_,_,_=env
    calls=[]; now=[1000]
    monkeypatch.setattr(weather.time,'time',lambda:now[0])
    def fetch(url,timeout): calls.append(parse_qs(urlparse(url).query));return sample()
    monkeypatch.setattr(weather,'_json_get',fetch)
    first=client.get('/api/v1/weather/forecast').json()
    assert first['available'] and first['wind_ms']==4.2 and first['feels_c']==-7
    assert first['today']['sunrise']=='07:00' and first['tomorrow']['max_c']==2
    assert calls[0]['forecast_days']==['2'] and calls[0]['wind_speed_unit']==['ms']
    assert calls[0]['timezone']==['Europe/Moscow']
    assert client.get('/api/v1/weather/forecast').json()==first and len(calls)==1
    now[0]+=901
    monkeypatch.setattr(weather,'_json_get',lambda *args: (_ for _ in ()).throw(OSError('offline')))
    assert client.get('/api/v1/weather/forecast').json()['stale']
    now[0]+=86400
    assert client.get('/api/v1/weather/forecast').json()['available'] is False
    client.cookies.clear()
    assert client.get('/api/v1/weather/forecast').status_code==401

@pytest.mark.parametrize('kind',['nan','negative_wind','missing','wrong_day','reversed','extra_days'])
def test_malformed_forecasts_rejected(kind):
    data=sample()
    if kind=='nan': data['current']['apparent_temperature']=float('nan')
    if kind=='negative_wind': data['current']['wind_speed_10m']=-1
    if kind=='missing': data['daily']['temperature_2m_max']=[]
    if kind=='wrong_day': data['daily']['time'][1]='2026-11-13'
    if kind=='reversed': data['daily']['temperature_2m_min'][0]=30
    if kind=='extra_days': data['daily']['time'].append('2026-11-13')
    with pytest.raises((ValueError,IndexError)):parse_forecast(data)

def test_polar_sunrise_and_sunset_can_be_unavailable():
    data=sample();data['daily']['sunrise'][0]=None;data['daily']['sunset'][0]=None
    assert parse_forecast(data)['today']['sunrise'] is None
