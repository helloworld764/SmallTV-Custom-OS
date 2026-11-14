import json,shutil,ssl,subprocess,threading
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import pytest
from test_monitor_delivery import modules,credential

@pytest.fixture
def tls_server(tmp_path):
    openssl=shutil.which('openssl')
    if not openssl and Path('C:/Program Files/Git/usr/bin/openssl.exe').is_file():openssl='C:/Program Files/Git/usr/bin/openssl.exe'
    if not openssl:pytest.fail('OpenSSL is required for verified HTTPS transport tests')
    def run(*args):subprocess.run([openssl,*args],cwd=tmp_path,check=True,capture_output=True,timeout=30)
    run('req','-x509','-newkey','rsa:2048','-nodes','-keyout','ca-key.pem','-out','ca.pem','-days','1','-subj','/CN=Storm Test Root','-addext','basicConstraints=critical,CA:TRUE','-addext','keyUsage=critical,keyCertSign,cRLSign')
    run('req','-newkey','rsa:2048','-nodes','-keyout','server-key.pem','-out','server.csr','-subj','/CN=Storm test server')
    (tmp_path/'extensions.cnf').write_text('[server]\nbasicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth\nsubjectAltName=IP:127.0.0.1\nsubjectKeyIdentifier=hash\nauthorityKeyIdentifier=keyid,issuer\n')
    run('x509','-req','-in','server.csr','-CA','ca.pem','-CAkey','ca-key.pem','-CAcreateserial','-out','server.pem','-days','1','-extfile','extensions.cnf','-extensions','server')
    requests=[]
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.headers.get('Authorization'))
            if self.server.redirect:
                self.send_response(302);self.send_header('Location','https://example.invalid/must-not-follow');self.end_headers();return
            self.send_response(200);self.send_header('Content-Type','application/json');self.end_headers();self.wfile.write(json.dumps({'agent_id':self.server.agent_id}).encode())
        def log_message(self,*args):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);server.redirect=False;server.agent_id=None
    context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.load_cert_chain(tmp_path/'server.pem',tmp_path/'server-key.pem');server.socket=context.wrap_socket(server.socket,server_side=True)
    worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
    try:yield server,tmp_path/'ca.pem',requests
    finally:server.shutdown();server.server_close();worker.join(5)

def test_verified_https_custom_ca_and_legacy_dpapi_roundtrip(modules,tls_server,tmp_path,monkeypatch):
    server,ca,requests=tls_server
    value=credential(modules,base_url=f'https://127.0.0.1:{server.server_port}',ca_file=str(ca));server.agent_id=value.agent_id
    assert modules.delivery.Client(value).request('identity')[:2]==(200,{'agent_id':value.agent_id})
    assert requests==['Bearer '+value.token]
    monkeypatch.setattr(modules.keys,'dpapi',lambda data,**options:bytes(b^165 for b in data))
    target=tmp_path/'remote.dpapi';modules.keys.save_credential(target,value);assert modules.keys.load_credential(target)==value

def test_untrusted_certificate_hostname_mismatch_and_redirect_fail_closed(modules,tls_server):
    server,ca,requests=tls_server
    untrusted=credential(modules,base_url=f'https://127.0.0.1:{server.server_port}')
    assert modules.delivery.Client(untrusted).request('identity')[0]==-1 and not requests
    mismatch=credential(modules,base_url=f'https://localhost:{server.server_port}',ca_file=str(ca))
    assert modules.delivery.Client(mismatch).request('identity')[0]==-1 and not requests
    server.redirect=True
    good=credential(modules,base_url=f'https://127.0.0.1:{server.server_port}',ca_file=str(ca))
    assert modules.delivery.Client(good).request('identity')[0]==302 and len(requests)==1

@pytest.mark.parametrize('url',['http://192.168.1.5:8000','https://user:pass@server.test','https://server.test/path','https://server.test?token=private','https://0.0.0.0','https://[::]','https://server.test:0','https://server.test\n'])
def test_remote_origin_policy_rejects_unsafe_values(modules,url):
    with pytest.raises(ValueError):credential(modules,base_url=url)

def test_https_default_port_and_outbox_never_silently_retargets(modules,tmp_path):
    local=credential(modules);queue=modules.delivery.Outbox(tmp_path/'outbox.sqlite3',local)
    remote=credential(modules,base_url='https://storm.example',agent_id=local.agent_id)
    assert remote.base_url=='https://storm.example:443'
    with pytest.raises(ValueError):modules.delivery.Outbox(tmp_path/'outbox.sqlite3',remote)
