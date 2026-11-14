import hashlib,importlib.util,io,json,struct,sys,zipfile
from pathlib import Path
from types import SimpleNamespace
import pytest
from PIL import Image
from backend.app.raster import encode,inspect,HEADER
from tools.build_release import build,ROOT
from tools.verify_release import verify

def test_release_is_reproducible_complete_and_excludes_private_files(tmp_path):
    root=tmp_path/'project';root.mkdir()
    for name in ('README.md','.env.example','backend/app/main.py','backend/migrations/001.sql','firmware/assets/x.spt','assets/fonts/OFL.txt',
                 '.env','backend/.env.production','data/a.sqlite3','backups/a.sqlite3','secrets/Null.png','backend/private.key','backend/__pycache__/x.pyc','tmp/report.txt'):
        path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('sentinel')
    first=tmp_path/'a.zip';second=tmp_path/'b.zip'
    manifest=build(first,root);build(second,root)
    assert first.read_bytes()==second.read_bytes()
    assert set(manifest['files'])=={'README.md','.env.example','backend/app/main.py','backend/migrations/001.sql','firmware/assets/x.spt','assets/fonts/OFL.txt'}
    assert verify(first)==manifest
    with pytest.raises(FileExistsError):build(first,root)

@pytest.mark.parametrize('name',['../escape','/absolute','C:/drive','bad\\path'])
def test_release_verifier_rejects_traversal(tmp_path,name):
    path=tmp_path/'bad.zip'
    with zipfile.ZipFile(path,'w') as archive:archive.writestr(name,'no')
    with pytest.raises(ValueError):verify(path)

def test_release_verifier_rejects_tampering(tmp_path):
    root=tmp_path/'project';root.mkdir();(root/'README.md').write_text('original')
    path=tmp_path/'x.zip';build(path,root)
    with zipfile.ZipFile(path) as archive:manifest=archive.read('RELEASE-MANIFEST.json')
    bad=tmp_path/'tampered.zip'
    with zipfile.ZipFile(bad,'w') as archive:
        archive.writestr('README.md','changed');archive.writestr('RELEASE-MANIFEST.json',manifest)
    with pytest.raises(ValueError,match='Checksum'):verify(bad)

def test_asset_catalog_hashes_budget_and_cyrillic():
    folder=ROOT/'firmware/assets/catalog-v1';manifest=json.loads((folder/'manifest.json').read_text())
    assert len(manifest['files'])==27 and manifest['total_raster_bytes']<512*1024
    for name,item in manifest['files'].items():
        raw=(folder/name).read_bytes();assert inspect(raw)['bytes']==item['bytes']<=17420
        assert hashlib.sha256(raw).hexdigest()==item['sha256'] and 'Null' not in name
    font=json.loads((folder/'cyrillic-16.json').read_text())
    codepoints=[glyph['codepoint'] for glyph in font['glyphs']]
    assert len(codepoints)==len(set(codepoints))
    assert all(ord(c) in codepoints for c in 'Казань Мурка Ёжик ёлка −'.replace('−',''))
    assert len((folder/'cyrillic-16.bin').read_bytes())==len(codepoints)*48
    assert hashlib.sha256((folder/'cyrillic-16.bin').read_bytes()).hexdigest()==manifest['font']['sha256']
    for glyph in font['glyphs']:
        if glyph['codepoint']!=32:assert any((folder/'cyrillic-16.bin').read_bytes()[glyph['offset']:glyph['offset']+48])
    assert 'SIL OPEN FONT LICENSE' in (ROOT/'assets/fonts/OFL.txt').read_text()

def test_raster_transparency_proportions_and_malformed_data():
    image=Image.new('RGBA',(20,40),(255,0,0,255));raw=encode(image,128)
    assert raw==encode(image,128);meta=inspect(raw);pixels=raw[meta['pixel_offset']:]
    assert len(raw)<=17420 and sum(index!=0 for index in pixels)==64*128
    for bad in (b'',raw[:-1],b'BAD!'+raw[4:],HEADER.pack(b'STP1',241,1,2,0)+bytes(249),raw[:-1]+bytes([255])):
        with pytest.raises(ValueError):inspect(bad)
    with pytest.raises(ValueError):encode(Image.new('RGBA',(2,2)))

def test_compact_wallpaper_preserves_transparent_margins_and_accepts_empty_background():
    from backend.app.device_display import compact_wallpaper
    image=Image.new('RGBA',(240,240));image.putpixel((200,120),(255,0,0,255))
    for background in (image,Image.new('RGBA',(240,240))):
        stream=io.BytesIO();background.save(stream,format='PNG');raw=compact_wallpaper(stream.getvalue());meta=inspect(raw)
        pixels=raw[meta['pixel_offset']:]
        assert meta['width']==meta['height']==240 and len(raw)<=58636
        assert pixels[0]==0 and sum(index!=0 for index in pixels)==(1 if background is image else 0)
        if background is image:assert pixels[120*240+200]!=0

def load_probe():
    spec=importlib.util.spec_from_file_location('storm_device_probe',ROOT/'firmware/tools/device_probe.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module

def test_software_device_outage_recovery_cursor_reset_and_limits():
    module=load_probe();identity='3e87fc22-3be3-4857-b8fc-bdfd9d5ec5be';probe=module.Probe('http://127.0.0.1:8001',identity,'test-only')
    def unavailable(*args):raise OSError('offline')
    probe.read=unavailable;assert probe.poll()['status']=='UNKNOWN'
    probe.read=lambda suffix:{'schema_version':1,'device_id':identity,'screen':'clock'} if not suffix else {'schema_version':1,'device_id':identity,'next_cursor':8}
    assert probe.poll()=={'status':'ONLINE','screen':'clock','cursor':8}
    probe.read=unavailable;assert probe.poll()['status']=='OFFLINE' and probe.cursor==8
    probe.read=lambda suffix:{'schema_version':1,'device_id':identity,'screen':'weather'} if not suffix else {'schema_version':1,'device_id':identity,'next_cursor':0}
    assert probe.poll()=={'status':'ONLINE','screen':'weather','cursor':0}
    probe.read=lambda suffix:{'schema_version':2};assert probe.poll()['status']=='OFFLINE'
    for origin in ('http://example.com','https://user:pass@example.com','https://example.com/path','https://example.com/#token'):
        with pytest.raises(ValueError):module.Probe(origin,identity,'test-only')

def test_probe_rejects_malformed_foreign_pages_and_clears_private_state_on_denial():
    from urllib.error import HTTPError
    from http.client import IncompleteRead
    identity='3e87fc22-3be3-4857-b8fc-bdfd9d5ec5be'
    probe=load_probe().Probe('http://127.0.0.1:8001',identity,'test-only')
    good={'schema_version':1,'device_id':identity,'screen':'tamagotchi'}
    for bad in ([],{}, {**good,'screen':'invalid'}, {**good,'schema_version':True}):
        probe.read=lambda suffix:bad
        assert probe.poll()['status']=='UNKNOWN' and probe.snapshot is None
    for page in ({'schema_version':1,'device_id':'foreign','next_cursor':0},
                 {'schema_version':1,'device_id':identity,'next_cursor':True}):
        probe.read=lambda suffix:good if not suffix else page
        assert probe.poll()['status']=='UNKNOWN' and probe.cursor==0
    probe.read=lambda suffix:good if not suffix else {'schema_version':1,'device_id':identity,'next_cursor':1}
    assert probe.poll()['status']=='ONLINE'
    def denied(*args):raise HTTPError('https://example.invalid',401,'Unauthorized',{},None)
    probe.read=denied
    assert probe.poll()=={'status':'OFFLINE','screen':None,'cursor':0} and probe.snapshot is None
    def truncated(*args):raise IncompleteRead(b'not a complete reply')
    probe.read=truncated;assert probe.poll()['status']=='OFFLINE'

def test_synthetic_transfer_drill_preserves_progress_and_revokes_access(tmp_path):
    from tools.transfer_smoke import create,check
    directory=tmp_path/'windows-source';create(directory)
    assert check(directory,tmp_path/'new-restored.sqlite3')>=26
