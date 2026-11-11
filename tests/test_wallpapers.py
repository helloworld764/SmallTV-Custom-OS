from io import BytesIO
from uuid import uuid4
import json
import pytest
from fastapi import HTTPException
from PIL import Image,PngImagePlugin
from test_auth import env,template,csrf,sign_in
from backend.app.image_processing import normalize_wallpaper,MAX_UPLOAD

PATH='/api/v1/devices/alice-tv/wallpapers'

def picture(color='red',size=(320,480),fmt='PNG',**kwargs):
    stream=BytesIO();Image.new('RGB',size,color).save(stream,format=fmt,**kwargs);return stream.getvalue()

def upload(client,headers,color='red',path=PATH):
    return client.post(path,content=picture(color),headers={**headers,'Content-Type':'image/png'})

def test_private_catalogue_upload_draft_save_delete_and_restart(env):
    client,app,users,_=env;headers=csrf(env)
    assert client.get(PATH).json()==[]
    result=upload(client,headers);assert result.status_code==201,result.text
    info=result.json()['wallpaper'];identifier=info['id'];assert info['width']==info['height']==240
    assert set(info)=={'id','label','created_at','width','height','bytes'}
    assert client.get('/api/v1/devices/alice-tv/settings').json()['advanced']=={}
    duplicate=upload(client,headers);assert duplicate.status_code==200 and duplicate.json()['duplicate']
    assert duplicate.json()['wallpaper']['id']==identifier
    image=client.get(PATH+'/'+identifier);assert image.status_code==200
    assert image.headers['content-type']=='image/png' and image.headers['x-content-type-options']=='nosniff'
    assert 'no-store' in image.headers['cache-control']
    assert Image.open(BytesIO(image.content)).size==(240,240)
    saved=client.patch('/api/v1/devices/alice-tv/settings',headers=headers,json={'advanced':{'wallpaper_id':identifier,'backdrop':'wallpaper','pet_motion':'off'}})
    assert saved.status_code==200,saved.text
    app.state.database.initialize()
    assert client.get(PATH+'/'+identifier).content==image.content
    assert client.delete(PATH+'/'+identifier,headers=headers).status_code==409
    updated={**saved.json()['advanced'],'backdrop':'grid'}
    assert client.patch('/api/v1/devices/alice-tv/settings',headers=headers,json={'advanced':updated}).status_code==200
    assert client.delete(PATH+'/'+identifier,headers=headers).status_code==204
    advanced=client.get('/api/v1/devices/alice-tv/settings').json()['advanced']
    assert advanced['wallpaper_id'] is None and advanced['pet_motion']=='off' and advanced['backdrop']=='grid'
    assert client.get(PATH).json()==[] and client.get(PATH+'/'+identifier).status_code==404
    with app.state.database.connection() as c:
        assert c.execute("SELECT count(*) FROM audit_log WHERE action LIKE 'wallpaper.%'").fetchone()[0]==2

def test_wallpapers_session_csrf_ownership_and_cross_device_reference(env):
    client,app,users,_=env
    assert client.get(PATH).status_code==401
    assert upload(client,{}).status_code==401
    sign_in(env)
    assert upload(client,{}).status_code==403
    headers=csrf(env);identifier=upload(client,headers).json()['wallpaper']['id']
    assert client.get('/api/v1/devices/bob-tv/wallpapers').status_code==404
    assert upload(client,headers,path='/api/v1/devices/bob-tv/wallpapers').status_code==404
    assert client.delete(PATH+'/'+identifier).status_code==403
    sign_in(env,'bob')
    assert client.get(PATH+'/'+identifier).status_code==404
    bob={'X-CSRF-Token':client.get('/api/v1/auth/session').json()['csrf_token']}
    assert client.delete(PATH+'/'+identifier,headers=bob).status_code==404
    assert client.patch('/api/v1/devices/bob-tv/settings',headers=bob,json={'advanced':{'backdrop':'wallpaper','wallpaper_id':identifier}}).status_code==404
    sign_in(env,'admin');assert client.get(PATH+'/'+identifier).status_code==200

def test_catalogue_limit_deduplication_rate_and_unknown_reference(env):
    client=env[0];headers=csrf(env)
    for color in ('red','blue','green','white','black'):assert upload(client,headers,color).status_code==201
    assert upload(client,headers).status_code==200
    assert upload(client,headers,'purple').status_code==409
    assert len(client.get(PATH).json())==5
    for _ in range(3):assert upload(client,headers).status_code==200
    limited=upload(client,headers);assert limited.status_code==429 and limited.headers['retry-after']
    assert client.patch('/api/v1/devices/alice-tv/settings',headers=headers,json={'advanced':{'backdrop':'wallpaper'}}).status_code==422
    assert client.patch('/api/v1/devices/alice-tv/settings',headers=headers,json={'advanced':{'wallpaper_id':str(uuid4())}}).status_code==404
    assert client.get(PATH+'/not-a-uuid').status_code==422

@pytest.mark.parametrize('raw,mime,status',[(b'not an image','image/png',422),(b'<svg/>','image/svg+xml',415),(b'','image/png',413),(b'x'*(MAX_UPLOAD+1),'image/png',413),(picture(),'image/jpeg',422),(picture()[:60],'image/png',422),(picture(size=(4097,1)),'image/png',422),(picture(size=(2100,2000)),'image/png',422)])
def test_reject_unsafe_images(raw,mime,status):
    with pytest.raises(HTTPException) as error:normalize_wallpaper(raw,mime)
    assert error.value.status_code==status

def test_metadata_removed_jpeg_orientation_and_animation_rejected():
    metadata=PngImagePlugin.PngInfo();metadata.add_text('private','not retained')
    out=normalize_wallpaper(picture(pnginfo=metadata),'image/png');image=Image.open(BytesIO(out))
    assert image.size==(240,240) and not image.info and not image.getexif()
    # Orientation 6 rotates red/blue halves from left/right to top/bottom.
    original=Image.new('RGB',(100,100),'blue');original.paste('red',(0,0,50,100))
    exif=Image.Exif();exif[274]=6;stream=BytesIO();original.save(stream,format='JPEG',exif=exif)
    rotated=Image.open(BytesIO(normalize_wallpaper(stream.getvalue(),'image/jpeg')))
    assert rotated.getpixel((120,30))[0]>200 and rotated.getpixel((120,210))[2]>200
    assert not rotated.info and not rotated.getexif()
    animation=BytesIO();original.save(animation,format='PNG',save_all=True,append_images=[Image.new('RGB',(100,100),'green')],duration=100,loop=0)
    with pytest.raises(HTTPException):normalize_wallpaper(animation.getvalue(),'image/png')

def test_upload_api_checks_type_label_length_and_chunked_limit(env):
    client=env[0];headers=csrf(env)
    assert client.post(PATH,headers=headers,content=picture()).status_code==415
    assert client.post(PATH+'?label=%20%20',headers={**headers,'Content-Type':'image/png'},content=picture()).status_code==422
    assert client.post(PATH,headers={**headers,'Content-Type':'image/png'},content=iter([b'x'*1048576]*3)).status_code==413
    assert client.post(PATH,headers={**headers,'Content-Type':'image/png'},content=picture()[:50]).status_code==422

def test_decode_busy_returns_retry(monkeypatch):
    from backend.app import image_processing
    class Busy:
        def acquire(self,**kwargs):return False
    monkeypatch.setattr(image_processing,'_slots',Busy())
    with pytest.raises(HTTPException) as error:normalize_wallpaper(picture(),'image/png')
    assert error.value.status_code==429 and error.value.headers['Retry-After']=='2'

def test_wallpaper_and_reference_survive_backup_restore(env,tmp_path):
    from backend.app.backups import backup_database,verify_backup,restore_database
    from backend.app.database import Database,read_migrations
    client,app,_,_=env;headers=csrf(env)
    identifier=upload(client,headers).json()['wallpaper']['id']
    assert client.patch('/api/v1/devices/alice-tv/settings',headers=headers,json={'advanced':{'backdrop':'wallpaper','wallpaper_id':identifier}}).status_code==200
    original=client.get(PATH+'/'+identifier).content
    snapshot=tmp_path/'copy.sqlite3';restored=tmp_path/'restored.sqlite3'
    backup_database(app.state.database.path,snapshot);assert verify_backup(snapshot).schema_version==len(read_migrations())
    restore_database(snapshot,restored,active_database=app.state.database.path)
    with Database(restored).connection() as c:
        assert bytes(c.execute('SELECT image FROM device_wallpapers WHERE id=?',(identifier,)).fetchone()[0])==original
        assert json.loads(c.execute("SELECT advanced FROM device_settings WHERE device_id='alice-tv'").fetchone()[0])['wallpaper_id']==identifier
        assert c.execute('SELECT count(*) FROM auth_sessions').fetchone()[0]==0

def test_upgrade_from_0023_preserves_existing_settings(tmp_path):
    from backend.app.database import Database,MIGRATIONS
    from backend.app.security import create_account
    import secrets
    old=tmp_path/'old-migrations';old.mkdir()
    for path in sorted(MIGRATIONS.glob('*.sql'))[:23]:(old/path.name).write_bytes(path.read_bytes())
    path=tmp_path/'upgrade.sqlite3';database=Database(path,migrations=old);database.initialize()
    owner=create_account(database,'upgrade-user',secrets.token_urlsafe(24),'Upgrade')
    advanced=json.dumps({'backdrop':'stars','screens':['weather','clock'],'accent':'#55ccaa'})
    with database.transaction() as c:
        c.execute("INSERT INTO devices(id,owner_id,name) VALUES ('old-tv',?,'Old TV')",(owner,))
        c.execute("INSERT INTO device_settings(device_id,brightness,advanced) VALUES ('old-tv',33,?)",(advanced,))
    upgraded=Database(path);upgraded.initialize();upgraded.initialize()
    with upgraded.connection() as c:
        row=c.execute("SELECT brightness,advanced FROM device_settings WHERE device_id='old-tv'").fetchone()
        assert row['brightness']==33 and row['advanced']==advanced
        assert c.execute('SELECT count(*) FROM device_wallpapers').fetchone()[0]==0
        assert not c.execute('PRAGMA foreign_key_check').fetchall()
