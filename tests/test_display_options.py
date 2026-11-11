import json
import pytest
from test_auth import env,template,csrf
from backend.app.display_options import DisplayOptions,screen_for

def test_display_options_save_owner_scope_and_controls_use_visible_order(env):
    client,app,users,_=env;headers=csrf(env)
    value={'custom_colors':True,'background':'#102030','text':'#ffffff','accent':'#55ccaa','screens':['markets','weather'],
           'palettes':[{'name':'Морская','background':'#102030','text':'#ffffff','accent':'#55ccaa'}], 'night_enabled':True,
           'night_start':'22:00','night_end':'08:00','timezone':'Europe/Moscow','backdrop':'stars'}
    assert client.patch('/api/v1/devices/bob-tv/settings',headers=headers,json={'advanced':value}).status_code==404
    saved=client.patch('/api/v1/devices/alice-tv/settings',headers=headers,json={'advanced':value})
    assert saved.status_code==200,saved.text
    options=saved.json()['advanced'];assert options['accent']=='#55ccaa' and options['night_brightness']==20
    assert saved.json()['screen_changed_at']>0
    client.patch('/api/v1/devices/alice-tv/settings',headers=headers,json={'brightness':55})
    assert client.get('/api/v1/devices/alice-tv/settings').json()['advanced']==options
    with app.state.database.connection() as c:
        row=dict(c.execute("SELECT * FROM device_settings WHERE device_id='alice-tv'").fetchone())
        assert json.loads(row['advanced'])['palettes'][0]['name']=='Морская'
        assert screen_for(row)=='markets'
    from uuid import uuid4
    hidden=client.post('/api/v1/devices/alice-tv/control',headers=headers,json={'request_id':str(uuid4()),'action':'screen','screen':'clock'})
    assert hidden.status_code==409
    result=client.post('/api/v1/devices/alice-tv/control',headers=headers,json={'request_id':str(uuid4()),'action':'next'})
    assert result.json()['settings']['current_screen']=='weather'
    assert client.get('/simulator/').headers['x-frame-options']=='SAMEORIGIN'
    assert client.get('/panel/').headers['x-frame-options']=='DENY'
    assert client.get('/simulator/').headers['content-security-policy']=="frame-ancestors 'self'"
    standard=client.patch('/api/v1/devices/alice-tv/settings',headers=headers,json={'theme':'Dark Blue'}).json()['advanced']
    assert standard['custom_colors'] is False and standard['night_enabled'] and standard['screens']==['markets','weather']

@pytest.mark.parametrize('value',[{'accent':'red; url(https://example.invalid)'},{'text':'#fff'},{'screens':[]},
 {'screens':['clock','clock']},{'screens':['monitor']},{'rotate_seconds':1},{'rotate_seconds':True},
 {'night_start':'25:00'},{'night_brightness':101},{'night_enabled':True,'night_start':'08:00','night_end':'08:00'},
 {'timezone':'Invalid/Zone'},{'notification_seconds':60},{'pet_motion':'violent'},{'wind_unit':'kmh'},
 {'palettes':[{'name':'same'},{'name':'SAME'}]},{'custom_colors':1}])
def test_invalid_advanced_settings_rejected(value):
    with pytest.raises(ValueError):DisplayOptions.model_validate(value)

def test_rotation_is_deterministic_and_default_does_not_change_legacy_screen():
    row={'current_screen':None,'start_screen':'clock','screen_changed_at':1000,'advanced':json.dumps({'screens':['clock','weather'],'auto_rotate':True,'rotate_seconds':30})}
    assert screen_for(row,1000)=='clock' and screen_for(row,1030)=='weather' and screen_for(row,1060)=='clock'
    row['advanced']='{}';row['start_screen']='monitor'
    assert screen_for(row,100000)=='monitor'
