import hashlib
import json
from pathlib import Path, PurePosixPath
import struct
import zlib

from fastapi.testclient import TestClient
import pytest

from backend.app.config import Settings
from backend.app.main import create_app


ROOT = Path(__file__).resolve().parents[1] / 'assets' / 'pets'
MANIFEST = json.loads((ROOT / 'manifest.json').read_text(encoding='utf-8')) if (ROOT/'manifest.json').is_file() else None
SIMULATOR_PETS = Path(__file__).resolve().parents[1] / 'web' / 'simulator' / 'pets'


def test_bot_thumbnails_are_complete_catalogue_without_metadata_or_private_characters():
    from backend.app.bot_pets import KINDS, STATES, photo_path
    manifest=json.loads((SIMULATOR_PETS/'bot'/'manifest.json').read_text(encoding='utf-8'))
    expected={f'{kind}-{state}.png' for kind in KINDS for state in STATES}
    assert set(manifest['files'])==expected
    assert {path.name for path in (SIMULATOR_PETS/'bot').glob('*.png')}==expected
    for kind in KINDS:
        assert hashlib.sha256((SIMULATOR_PETS/(kind+'-states.png')).read_bytes()).hexdigest()==manifest['sources'][kind]
    for name,checksum in manifest['files'].items():
        data=photo_path(name[:-4]).read_bytes()
        assert hashlib.sha256(data).hexdigest()==checksum
        assert len(data)<1048576 and struct.unpack('>II',data[16:24])==(960,960)
        offset=8
        while offset<len(data):
            length=int.from_bytes(data[offset:offset+4],'big')
            kind=data[offset+4:offset+8]
            assert kind in (b'IHDR',b'IDAT',b'IEND')
            offset+=length+12


def verify_png(record):
    relative = PurePosixPath(record['file'])
    assert not relative.is_absolute() and '..' not in relative.parts and '\\' not in record['file']
    path = ROOT / relative
    path.resolve().relative_to(ROOT.resolve())
    assert not path.is_symlink()
    data = path.read_bytes()
    assert len(data) < 16 * 1024 * 1024
    assert hashlib.sha256(data).hexdigest() == record['sha256']
    assert data[:8] == b'\x89PNG\r\n\x1a\n'
    offset, chunks = 8, []
    while offset < len(data):
        length = int.from_bytes(data[offset:offset+4], 'big')
        kind = data[offset+4:offset+8]
        payload = data[offset+8:offset+8+length]
        checksum = data[offset+8+length:offset+12+length]
        assert len(checksum) == 4
        assert zlib.crc32(kind + payload) & 0xffffffff == int.from_bytes(checksum, 'big')
        assert kind not in (b'tEXt', b'zTXt', b'iTXt', b'eXIf')  # No hidden textual/EXIF personal data.
        if kind == b'IHDR':
            assert not chunks
            width, height, depth, mode, _, _, interlace = struct.unpack('>IIBBBBB', payload)
            assert (width, height) == (record['width'], record['height'])
            assert depth == 8 and mode == 6 and interlace == 0
        chunks.append(kind)
        offset += length + 12
        if kind == b'IEND':
            assert payload == b'' and offset == len(data)
            break
    assert b'IDAT' in chunks and chunks[-1] == b'IEND'
    # Generated drafts can have almost-opaque 253/254 interiors, as the references do.
    assert record['alpha_min'] == 0 and 250 <= record['alpha_max'] <= 255
    assert record['fully_transparent_pixels'] > 0


@pytest.mark.parametrize('character', MANIFEST['characters'] if MANIFEST else [], ids=lambda item: item['id'])
def test_original_and_review_sheet_match_manifest_without_private_metadata(character):
    verify_png(character['reference'])
    verify_png(character['sheet'])
    assert character['approval'] == 'appearance_approved'
    assert character['columns'] == character['rows'] == 3
    assert character['access'] == ('owner_only' if character['id'] == 'Null' else 'catalog')


def test_review_is_not_enabled_and_generation_inputs_are_reproducible():
    if MANIFEST is None:pytest.skip('Development art archive is intentionally absent from server bundle')
    assert MANIFEST['stage'] == 'review_only' and MANIFEST['runtime_enabled'] is False
    assert {item['id'] for item in MANIFEST['characters']} == {'Byte', 'Glitch', 'Pixel', 'Null'}
    assert len(MANIFEST['states']) == len(set(MANIFEST['states'])) == 9
    assert set(MANIFEST['api_reactions']) == {'ADOPT', 'FEED', 'PLAY', 'SLEEP', 'WAKE', 'RETURN'}
    assert set(MANIFEST['api_reactions'].values()) <= set(MANIFEST['states'])
    for record in MANIFEST['superseded']:
        verify_png(record)
    prompts = json.loads((ROOT / 'prompts.json').read_text(encoding='utf-8'))
    assert prompts['tool'] == 'built-in image_gen' and prompts['review_only'] is True
    for job in prompts['prompts']:
        for file in [*job['reference_images'], job['output']]:
            assert not PurePosixPath(file).is_absolute() and '..' not in PurePosixPath(file).parts
            assert (ROOT / file).is_file()


def test_public_simulator_assets_match_approved_catalogue_and_exclude_null():
    runtime = json.loads((SIMULATOR_PETS / 'manifest.json').read_text(encoding='utf-8'))
    assert runtime['purpose'].startswith('authenticated simulator preview')
    assert runtime['grid'] == {'columns': 3, 'rows': 3, 'cell_geometry': 'equal thirds preview'}
    assert runtime['hardware_compatibility'] == 'unverified'
    assert [row['id'] for row in runtime['characters']] == ['Pixel', 'Byte', 'Glitch']
    assert runtime['null']['included'] is False
    sources = {row['id']: row for row in MANIFEST['characters']} if MANIFEST else None
    for character in runtime['characters']:
        assert character['access'] == 'catalog'
        assert character['approval'] == 'appearance_approved'
        path = SIMULATOR_PETS / character['file']
        data = path.read_bytes()
        assert hashlib.sha256(data).hexdigest() == character['sha256']
        if sources:assert data == (ROOT / sources[character['id']]['sheet']['file']).read_bytes()
        assert struct.unpack('>II', data[16:24]) == (character['width'], character['height'])


def test_private_review_and_null_assets_are_not_http_static_files(tmp_path):
    with TestClient(create_app(Settings(_env_file=None, db_path=tmp_path / 'art.sqlite3'))) as client:
        for path in ('/assets/pets/references/Null.png', '/assets/pets/review/index.html',
                     '/assets/pets/manifest.json', '/shared/%2e%2e/%2e%2e/assets/pets/references/Null.png'):
            assert client.get(path).status_code == 404
        assert client.get('/simulator/pets/manifest.json').status_code == 200
        for name in ('Pixel', 'Byte', 'Glitch'):
            response = client.get(f'/simulator/pets/{name}-states.png')
            assert response.status_code == 200
            assert response.headers['content-type'] == 'image/png'
            assert response.headers.get('etag')
        assert client.get('/simulator/pets/Null-states.png').status_code == 404
        assert client.get('/simulator/pets/../../assets/pets/references/Null.png').status_code == 404
