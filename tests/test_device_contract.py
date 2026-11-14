import json
from pathlib import Path
import pytest
from test_device_display import display_env,env,template
from firmware.tools.protocol_contract import DisplayEnvelope,EventsEnvelope,decode
from tools.export_device_contract import documents

ROOT=Path(__file__).resolve().parents[1]

@pytest.mark.parametrize('screen',['clock','weather','tamagotchi','codex','markets','monitor'])
def test_real_api_envelopes_conform_without_cookies(display_env,screen):
    base,keys=display_env;client=base[0];client.cookies.clear()
    response=client.get('/api/v1/devices/alice-tv/display',headers=keys['alice'],params={'screen':screen})
    assert response.status_code==200
    parsed=decode(response.content,DisplayEnvelope,'alice-tv');assert parsed.screen==screen
    page=client.get('/api/v1/devices/alice-tv/display/events',headers=keys['alice'])
    assert decode(page.content,EventsEnvelope,'alice-tv').next_cursor==0

def test_exported_schemas_are_current():
    for name,content in documents().items():assert (ROOT/'firmware/protocol'/name).read_text(encoding='utf-8')==content

@pytest.mark.parametrize('change',[{'schema_version':True},{'schema_version':2},{'next_cursor':True},{'next_cursor':-1},{'next_cursor':9007199254740992},
                                 {'events':[{'id':'synthetic-event','cursor':1,'kind':'DONE','ttl_seconds':0}]},
                                 {'events':[{'id':'synthetic-event','cursor':1,'kind':'WORKING','ttl_seconds':60}]}])
def test_invalid_cursor_version_and_expired_event_are_rejected(change):
    page={'schema_version':1,'device_id':'example-tv','server_time':1000,'next_cursor':0,'events':[]}|change
    with pytest.raises(ValueError):decode(json.dumps(page).encode(),EventsEnvelope,'example-tv')

def test_budget_duplicate_keys_nonfinite_foreign_and_additive_fields():
    page={'schema_version':1,'device_id':'example-tv','server_time':1000,'next_cursor':0,'events':[]}
    good=json.dumps(page).encode()
    for invalid in [b'x'*16385,b'[]',b'{"schema_version":1,"schema_version":2}',good.replace(b'1000',b'NaN'),b'{'+b'"nested":['*1100]:
        with pytest.raises(ValueError):decode(invalid,EventsEnvelope,'example-tv')
    with pytest.raises(ValueError):decode(good,EventsEnvelope,'other-tv')
    assert decode(json.dumps(page|{'future_additive_field':'ignored'}).encode(),EventsEnvelope,'example-tv').events==[]
    event={'id':'synthetic-event','cursor':2,'kind':'DONE','ttl_seconds':60}
    assert decode(json.dumps(page|{'next_cursor':2,'events':[event]}).encode(),EventsEnvelope,'example-tv').events[0].kind=='DONE'
