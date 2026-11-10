import mimetypes

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend.app.config import Settings
from backend.app.main import create_app


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(Settings(_env_file=None, db_path=tmp_path / 'backend.sqlite3'))) as instance:
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
    for asset in ('style.css', 'simulator.mjs', 'connection.mjs', 'preview.mjs', 'pet-display.mjs', 'pet-frame-bounds.mjs', 'pets/manifest.json'):
        assert client.get(f'/simulator/{asset}').status_code == 200
    assert client.get('/simulator/storm-logo.png').headers['content-type'] == 'image/png'
    assert 'id="weather-summary"' in response.text
    assert 'id="status"' not in response.text
    for name in ('Pixel', 'Byte', 'Glitch'):
        response = client.get(f'/simulator/pets/{name}-states.png')
        assert response.status_code == 200
        assert response.headers['content-type'] == 'image/png'
    assert client.get('/simulator/pets/Null-states.png').status_code == 404


def test_unknown_route_and_method(client):
    assert client.get('/missing').status_code == 404
    assert client.post('/health').status_code == 405


def test_modules_have_javascript_mime_even_with_windows_plaintext_mapping(client, monkeypatch):
    # Reproduce the MIME mapping observed in the running Windows server.
    mimetypes.init()
    monkeypatch.setitem(mimetypes.types_map, '.mjs', 'text/plain')
    for asset in ('simulator.mjs', 'connection.mjs'):
        response = client.get(f'/simulator/{asset}')
        assert response.status_code == 200
        assert response.headers['content-type'] == 'text/javascript; charset=utf-8'


def test_environment_overrides(monkeypatch):
    monkeypatch.setenv('SMALLTV_HOST', '127.0.0.2')
    monkeypatch.setenv('SMALLTV_PORT', '8123')
    monkeypatch.setenv('SMALLTV_LOG_LEVEL', 'warning')
    monkeypatch.setenv('SMALLTV_DB_PATH', 'tmp/custom.sqlite3')
    settings = Settings(_env_file=None)
    assert (settings.host, settings.port, settings.log_level) == ('127.0.0.2', 8123, 'warning')
    assert settings.db_path.as_posix() == 'tmp/custom.sqlite3'


@pytest.mark.parametrize('name,value', [
    ('SMALLTV_PORT', '0'), ('SMALLTV_PORT', '65536'), ('SMALLTV_PORT', 'bad'),
    ('SMALLTV_LOG_LEVEL', 'invalid'), ('SMALLTV_HOST', ''),
    ('SMALLTV_DB_PATH', ''), ('SMALLTV_DB_PATH', ':memory:'),
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


def test_panel_is_public_shell_with_no_private_data(client):
    response = client.get('/panel/')
    assert response.status_code == 200
    assert 'text/html' in response.headers['content-type']
    assert 'id="login-form"' in response.text
    assert 'id="settings-form"' in response.text
    assert 'id="empty"' in response.text
    assert 'type="password"' in response.text
    for asset in ('style.css', 'panel.mjs', 'api.mjs', 'enrollment.mjs', 'device-presence.mjs', 'help.html'):
        assert client.get(f'/panel/{asset}').status_code == 200
    assert client.get('/api/v1/devices').status_code == 401
    assert client.get('/panel/%2e%2e/%2e%2e/backend/app/config.py').status_code == 404
    assert client.get('/panel/.env').status_code == 404


def test_panel_modules_override_windows_plaintext_mime(client, monkeypatch):
    mimetypes.init()
    monkeypatch.setitem(mimetypes.types_map, '.mjs', 'text/plain')
    for asset in ('panel.mjs', 'api.mjs', 'enrollment.mjs', 'device-presence.mjs'):
        response = client.get(f'/panel/{asset}')
        assert response.headers['content-type'] == 'text/javascript; charset=utf-8'
        assert response.headers['x-content-type-options'] == 'nosniff'


def test_shared_site_theme_assets_and_page_controls_are_public_but_sources_are_private(client, monkeypatch):
    mimetypes.init()
    monkeypatch.setitem(mimetypes.types_map, '.mjs', 'text/plain')
    for page in ('/panel/', '/panel/help.html', '/simulator/'):
        response = client.get(page)
        assert response.status_code == 200
        assert 'data-site-theme-toggle' in response.text
        assert '/shared/site-theme.css' in response.text and '/shared/site-theme.mjs' in response.text
    module = client.get('/shared/site-theme.mjs')
    assert module.status_code == 200 and module.headers['content-type'] == 'text/javascript; charset=utf-8'
    assert client.get('/shared/site-theme.css').status_code == 200
    assert client.get('/shared/clock.mjs').headers['content-type'] == 'text/javascript; charset=utf-8'
    assert client.get('/shared/.env').status_code == 404
    assert client.get('/shared/%2e%2e/%2e%2e/backend/app/config.py').status_code == 404
