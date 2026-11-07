import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend.app.config import Settings
from backend.app.main import create_app


@pytest.fixture
def client():
    with TestClient(create_app(Settings(_env_file=None))) as instance:
        yield instance


def test_health_is_liveness_only_and_not_cached(client):
    response = client.get('/health')
    assert response.status_code == 200
    assert response.json() == {'status': 'ok'}
    assert response.headers['cache-control'] == 'no-store'


def test_simulator_and_assets_are_served(client):
    assert client.get('/').url.path == '/simulator/'
    response = client.get('/simulator/')
    assert response.status_code == 200
    assert 'text/html' in response.headers['content-type']
    assert 'lang="ru"' in response.text
    for asset in ('style.css', 'simulator.mjs', 'connection.mjs'):
        assert client.get(f'/simulator/{asset}').status_code == 200


def test_unknown_route_and_method(client):
    assert client.get('/missing').status_code == 404
    assert client.post('/health').status_code == 405


def test_environment_overrides(monkeypatch):
    monkeypatch.setenv('SMALLTV_HOST', '127.0.0.2')
    monkeypatch.setenv('SMALLTV_PORT', '8123')
    monkeypatch.setenv('SMALLTV_LOG_LEVEL', 'warning')
    settings = Settings(_env_file=None)
    assert (settings.host, settings.port, settings.log_level) == ('127.0.0.2', 8123, 'warning')


@pytest.mark.parametrize('name,value', [
    ('SMALLTV_PORT', '0'), ('SMALLTV_PORT', '65536'), ('SMALLTV_PORT', 'bad'),
    ('SMALLTV_LOG_LEVEL', 'invalid'), ('SMALLTV_HOST', ''),
])
def test_invalid_environment_is_rejected(monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_dotenv_and_environment_precedence(tmp_path, monkeypatch):
    dotenv = tmp_path / '.env'
    dotenv.write_text('SMALLTV_PORT=8124\nUNRELATED_VALUE=ignored\n', encoding='utf-8')
    monkeypatch.delenv('SMALLTV_PORT', raising=False)
    assert Settings(_env_file=dotenv).port == 8124
    monkeypatch.setenv('SMALLTV_PORT', '8125')
    assert Settings(_env_file=dotenv).port == 8125


def test_static_mount_does_not_expose_backend_sources(client):
    assert client.get('/simulator/%2e%2e/%2e%2e/backend/app/config.py').status_code == 404
