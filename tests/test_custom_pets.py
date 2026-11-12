import json,time
from io import BytesIO
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
from PIL import Image
import pytest
from test_auth import env,template,csrf,sign_in
from test_wallpapers import picture
from backend.app import custom_pets
from backend.app.image_processing import normalize_pet
from custom_pet_archive import archived_custom_pets
pytestmark=pytest.mark.usefixtures('archived_custom_pets')

BASE='/api/v1/pets'
def upload(client,headers,color='red'):
    result=client.post(BASE+'/image-drafts',headers={**headers,'Content-Type':'image/png'},content=picture(color,size=(100,200)))
    assert result.status_code==201,result.text
    return result.json()['id']
def confirm(client,headers,identifier,name='Свой кот'):
    return client.post(BASE+'/image-confirm',headers=headers,json={'draft_id':identifier,'name':name})

def test_custom_pet_preview_create_confirm_duplicate_and_private_image(env):
    client,app,_,_=env;headers=csrf(env);identifier=upload(client,headers)
    assert client.get(BASE+'/current').status_code==404
    draft=client.get(BASE+f'/image-drafts/{identifier}/image');assert draft.status_code==200
    image=Image.open(BytesIO(draft.content));assert image.height/image.width==2 and max(image.size)<=240 and not image.info
    assert draft.headers['cache-control']=='no-store' and draft.headers['x-content-type-options']=='nosniff'
    result=confirm(client,headers,identifier);assert result.status_code==200,result.text
    pet=result.json()['pet'];assert pet['name']=='Свой кот' and pet['image_id']==identifier
    assert client.get(BASE+'/current').json()==pet
    assert client.get(BASE+f'/current/image/{identifier}').content==draft.content
    assert client.get(BASE+f'/image-drafts/{identifier}/image').status_code==404
    assert confirm(client,headers,identifier).json()['duplicate'] is True
    assert confirm(client,headers,identifier,'Другое имя').status_code==409
    assert client.get(BASE+'/catalog').json()==['Pixel','Byte','Glitch']
    app.state.database.initialize();assert client.get(BASE+f'/current/image/{identifier}').status_code==200

def test_changing_appearance_preserves_absent_pet_progress_history_and_old_confirm(env):
    client,app,_,_=env;headers=csrf(env)
    original=client.post(BASE,headers=headers,json={'kind':'Byte','name':'Legacy'}).json()
    with app.state.database.transaction() as c:
        old=int(time.time())-73*3600
        c.execute('UPDATE pet_states SET last_care_at=?,updated_at=?,reaction_at=? WHERE pet_id=?',(old,old,old,original['id']))
        before=dict(c.execute('SELECT * FROM pet_states').fetchone())
    state=client.get(BASE+'/current').json();assert state['lifecycle']=='ABSENT'
    first=upload(client,headers);changed=confirm(client,headers,first).json()['pet']
    assert changed['id']==original['id'] and changed['kind']=='Byte' and changed['round']==original['round']
    assert changed['lifecycle']=='ABSENT' and changed['return_available_at']==state['return_available_at']
    assert changed['reaction'] is None
    with app.state.database.connection() as c:assert dict(c.execute('SELECT * FROM pet_states').fetchone())==before
    second=upload(client,headers,'blue');assert confirm(client,headers,second,'Новый образ').status_code==200
    assert client.get(BASE+f'/current/image/{first}').status_code==404
    repeated=confirm(client,headers,first).json();assert repeated['duplicate'] and repeated['pet']['image_id']==second
    assert repeated['pet']['name']=='Новый образ'

def test_custom_image_session_csrf_owner_scope_and_admin_no_bypass(env):
    client=env[0]
    assert client.post(BASE+'/image-drafts',content=b'image').status_code==401
    headers=csrf(env);identifier=upload(client,headers)
    assert client.post(BASE+'/image-confirm',json={'draft_id':identifier,'name':'Cat'}).status_code==403
    sign_in(env,'bob');other={'X-CSRF-Token':client.get('/api/v1/auth/session').json()['csrf_token']}
    assert client.get(BASE+f'/image-drafts/{identifier}/image').status_code==404
    assert confirm(client,other,identifier).status_code==404
    client.delete(BASE+f'/image-drafts/{identifier}',headers=other)
    sign_in(env,'admin');assert client.get(BASE+f'/image-drafts/{identifier}/image').status_code==404
    headers=csrf(env);assert confirm(client,headers,identifier).status_code==200
    sign_in(env,'bob');assert client.get(BASE+f'/current/image/{identifier}').status_code==404

@pytest.mark.parametrize('name',['', 'x'*25, '\u200bhidden', 'line\nbreak',123,None])
def test_custom_names_validated_without_changing_game(env,name):
    client=env[0];headers=csrf(env);identifier=upload(client,headers)
    assert confirm(client,headers,identifier,name).status_code==422
    assert client.get(BASE+'/current').status_code==404

def test_draft_cancel_expiry_replacement_and_rate_limit(env):
    client,app,_,_=env;headers=csrf(env);first=upload(client,headers)
    assert upload(client,headers)==first
    second=upload(client,headers,'blue');assert first!=second
    assert client.get(BASE+f'/image-drafts/{first}/image').status_code==404
    with app.state.database.transaction() as c:c.execute('UPDATE pet_image_drafts SET expires_at=0 WHERE id=?',(second,))
    assert confirm(client,headers,second).status_code==410
    third=upload(client,headers,'green')
    assert client.delete(BASE+f'/image-drafts/{third}',headers=headers).status_code==204
    assert confirm(client,headers,third).status_code==404
    upload(client,headers)
    assert client.post(BASE+'/image-drafts',content=picture(),headers={**headers,'Content-Type':'image/png'}).status_code==429

def test_confirmation_is_atomic_under_parallel_requests(env):
    client=env[0];headers=csrf(env);identifier=upload(client,headers)
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda _:confirm(client,headers,identifier),range(2)))
    assert all(r.status_code==200 for r in results)
    assert sorted(r.json()['duplicate'] for r in results)==[False,True]
    assert len({r.json()['pet']['id'] for r in results})==1

def test_revocation_during_decode_prevents_draft_write(env,monkeypatch):
    client,app,users,_=env;headers=csrf(env)
    def revoke(raw,mime):
        with app.state.database.transaction() as c:c.execute('DELETE FROM auth_sessions WHERE user_id=?',(users['alice'],))
        return normalize_pet(raw,mime)
    monkeypatch.setattr(custom_pets,'normalize_pet',revoke)
    result=client.post(BASE+'/image-drafts',headers={**headers,'Content-Type':'image/png'},content=picture())
    assert result.status_code==401
    with app.state.database.connection() as c:assert c.execute('SELECT count(*) FROM pet_image_drafts').fetchone()[0]==0

def test_transparent_padding_is_trimmed_without_distortion_and_telegram_preview_is_clean():
    source=Image.new('RGBA',(200,200));source.paste((20,200,100,255),(90,40,110,160));raw=BytesIO();source.save(raw,format='PNG')
    image,telegram=normalize_pet(raw.getvalue(),'image/png')
    sprite=Image.open(BytesIO(image));assert sprite.height/sprite.width==6 and max(sprite.size)<=240 and not sprite.info
    assert Image.open(BytesIO(telegram)).size==(960,960) and len(telegram)<=1048576
    empty=BytesIO();Image.new('RGBA',(20,20)).save(empty,format='PNG')
    from fastapi import HTTPException
    with pytest.raises(HTTPException):normalize_pet(empty.getvalue(),'image/png')

def test_backup_restore_keeps_active_image_and_drops_pending_private_drafts(env,tmp_path):
    from backend.app.backups import backup_database,restore_database
    from backend.app.database import Database
    client,app,_,_=env;headers=csrf(env);identifier=upload(client,headers);assert confirm(client,headers,identifier).status_code==200
    original=client.get(BASE+f'/current/image/{identifier}').content
    upload(client,headers,'blue');snapshot=tmp_path/'snapshot.sqlite3';target=tmp_path/'restored.sqlite3'
    backup_database(app.state.database.path,snapshot);restore_database(snapshot,target,active_database=app.state.database.path)
    with Database(target).connection() as c:
        assert c.execute('SELECT image FROM pet_appearances').fetchone()[0]==original
        assert c.execute('SELECT count(*) FROM pet_image_drafts WHERE confirmed_pet_id IS NULL').fetchone()[0]==0
        assert c.execute('SELECT count(*) FROM auth_sessions').fetchone()[0]==0
