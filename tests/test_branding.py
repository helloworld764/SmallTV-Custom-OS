from pathlib import Path
import re

from fastapi.testclient import TestClient
import pytest

from backend.app.auth import COOKIE
from backend.app.config import Settings
from backend.app.main import create_app


@pytest.fixture
def brand_client(tmp_path):
    app = create_app(Settings(_env_file=None, db_path=tmp_path / 'brand.sqlite3'))
    with TestClient(app) as client:
        yield client


@pytest.mark.parametrize('path,title', [
    ('/panel/', 'Storm Control Panel'),
    ('/panel/help.html', 'Storm Control Panel — первый вход'),
    ('/simulator/', 'Storm Simulator'),
])
def test_user_pages_use_official_branding(brand_client, path, title):
    response = brand_client.get(path)
    assert response.status_code == 200
    assert f'<title>{title}</title>' in response.text
    assert 'Storm OS' in response.text
    assert 'SmallTV Custom OS' not in response.text
    assert 'SMALLTV CUSTOM OS' not in response.text


def test_openapi_uses_product_name_without_changing_api_contract(brand_client):
    schema = brand_client.get('/openapi.json').json()
    assert schema['info']['title'] == 'Storm OS'
    assert schema['info']['version'] == '0.1.0'
    assert '/health' in schema['paths']
    assert '/api/v1/auth/login' in schema['paths']
    assert '/api/v1/devices/{device_id}/settings' in schema['paths']
    assert brand_client.get('/health').json() == {'status': 'ok'}


def test_spec_records_product_names_and_compatibility():
    root = Path(__file__).resolve().parents[1]
    spec = (root / 'TZ.md').read_text(encoding='utf-8')
    version = re.search(r'^\*\*Версия:\*\* (\d+)\.(\d+)\b', spec, re.MULTILINE)
    assert version is not None and tuple(map(int, version.groups())) >= (1, 2)
    for name in ('Storm OS', 'Storm Control Panel', 'Storm Simulator', 'Storm Bot'):
        assert name in spec
        assert name in (root / 'AGENTS.md').read_text(encoding='utf-8')
        assert name in (root / 'README.md').read_text(encoding='utf-8')
    assert 'SmallTV-Custom-OS' in spec  # Repository name remains unchanged.


def test_technical_configuration_and_session_names_stay_compatible():
    assert Settings.model_config['env_prefix'] == 'SMALLTV_'
    assert Settings.model_fields['db_path'].default == Path('data/smalltv.sqlite3')
    assert COOKIE == 'smalltv_session'
