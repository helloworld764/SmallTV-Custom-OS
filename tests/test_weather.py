from contextlib import closing
import json
import secrets
import time as clock
import sqlite3
from urllib.parse import urlparse, parse_qs

from fastapi.testclient import TestClient
import pytest

from backend.app.config import Settings
from backend.app.database import Database
from backend.app.main import create_app
from backend.app.security import create_account
from backend.app import weather


@pytest.fixture
def env(tmp_path):
    path = tmp_path / 'weather.sqlite3'
    database = Database(path)
    database.initialize()
    password = secrets.token_urlsafe(24)
    owner = create_account(database, 'weather-user', password, 'Weather user', role='user')
    other = create_account(database, 'other-weather', password, 'Other user', role='user')
    with TestClient(create_app(Settings(_env_file=None, db_path=path))) as client:
        response = client.post('/api/v1/auth/login', headers={'X-SmallTV-Client': 'web'},
                               json={'username': 'weather-user', 'password': password})
        assert response.status_code == 200
        yield client, response.json()['csrf_token'], owner, other, database


class FakeResponse:
    status = 200
    def __init__(self, value): self.value = value
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def read(self, limit): return json.dumps(self.value).encode()


def test_default_city_is_kazan_and_private(env):
    client, _, _, _, _ = env
    assert client.get('/api/v1/preferences/weather').json() == {
        'city': '\u041a\u0430\u0437\u0430\u043d\u044c', 'timezone': 'Europe/Moscow'}
    unauthenticated = TestClient(create_app(Settings(_env_file=None, db_path=env[4].path)))
    unauthenticated.cookies.clear()
    assert unauthenticated.get('/api/v1/preferences/weather').status_code == 401


def test_city_update_requires_csrf_and_persists_only_for_owner(env, monkeypatch):
    client, csrf, owner, other, database = env
    geocodes = []
    def fake_geocode(city, timeout):
        geocodes.append((city, timeout))
        return {'name': 'London', 'latitude': 51.5, 'longitude': -0.12,
                'timezone': 'Europe/London', 'country': 'United Kingdom'}
    monkeypatch.setattr(weather, 'geocode', fake_geocode)
    assert client.patch('/api/v1/preferences/weather', json={'city': 'London'}).status_code == 403
    assert not geocodes
    response = client.patch('/api/v1/preferences/weather', headers={'X-CSRF-Token': csrf},
                            json={'city': '  London  '})
    assert response.status_code == 200 and response.json()['city'] == 'London'
    assert geocodes[0][0] == 'London'
    with database.connection() as connection:
        rows = connection.execute('SELECT user_id, weather_city FROM user_preferences').fetchall()
    assert [tuple(row) for row in rows] == [(owner, 'London')]
    assert other not in [row['user_id'] for row in rows]


def test_city_lookup_failure_does_not_overwrite_preference(env, monkeypatch):
    client, csrf, owner, _, database = env
    monkeypatch.setattr(weather, 'urlopen', lambda *args, **kwargs: FakeResponse({'results': []}))
    response = client.patch('/api/v1/preferences/weather', headers={'X-CSRF-Token': csrf},
                            json={'city': 'NotARealPlace'})
    assert response.status_code == 422
    with database.connection() as connection:
        assert connection.execute('SELECT 1 FROM user_preferences WHERE user_id=?', (owner,)).fetchone() is None


def test_weather_uses_cache_maps_conditions_and_returns_stale_fallback(env, monkeypatch):
    client, _, _, _, database = env
    now = [int(clock.time())]
    monkeypatch.setattr(weather.time, 'time', lambda: now[0])
    calls = []
    def fake_open(request, timeout):
        url = request.full_url
        calls.append(url)
        params = parse_qs(urlparse(url).query)
        if 'forecast' in url:
            return FakeResponse({'current': {'temperature_2m': -3.6, 'weather_code': 71,
                                             'time': '2026-11-08T12:00'}})
        return FakeResponse({'results': []})
    monkeypatch.setattr(weather, 'urlopen', fake_open)
    one = client.get('/api/v1/weather').json()
    two = client.get('/api/v1/weather').json()
    assert 'available' in one, (one, calls)
    assert one['available'] and one['city'] == '\u041a\u0430\u0437\u0430\u043d\u044c'
    assert one['temperature_c'] == -4 and one['weather_code'] == 71
    assert one['condition'] == '\u041d\u0435\u0431\u043e\u043b\u044c\u0448\u043e\u0439 \u0441\u043d\u0435\u0433'
    assert one['attribution'] == 'Open-Meteo \u00b7 GeoNames'
    assert two == one and len(calls) == 1
    now[0] += weather.CACHE_SECONDS + 1
    def offline(*args, **kwargs): raise OSError('offline')
    monkeypatch.setattr(weather, 'urlopen', offline)
    stale = client.get('/api/v1/weather').json()
    assert stale['available'] and stale['stale'] is True
    assert stale['city'] == '\u041a\u0430\u0437\u0430\u043d\u044c'


def test_weather_unavailable_without_cache_is_not_fabricated(env, monkeypatch):
    client, _, _, _, _ = env
    monkeypatch.setattr(weather, 'urlopen', lambda *args, **kwargs: (_ for _ in ()).throw(OSError('offline')))
    response = client.get('/api/v1/weather')
    assert response.status_code == 200
    assert response.json() == {'available': False, 'city': '\u041a\u0430\u0437\u0430\u043d\u044c',
                               'attribution': 'Open-Meteo \u00b7 GeoNames'}
