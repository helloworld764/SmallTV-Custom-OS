from contextlib import closing, contextmanager
import importlib.util
import json
import os
from pathlib import Path
import secrets
import socket
import sqlite3
import sys
from threading import Thread
import time
from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest
import uvicorn

from backend.app.config import Settings
from backend.app.database import Database
from backend.app.main import create_app
from backend.app.security import create_account


@pytest.fixture
def modules(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'codex-monitor'))
    import delivery
    import local_credentials
    import local_monitor
    import smoke_exec
    return SimpleNamespace(delivery=delivery, keys=local_credentials, cli=local_monitor, smoke=smoke_exec)


def credential(modules, **changes):
    return modules.keys.Credential(**({'base_url': 'http://127.0.0.1:8000', 'agent_id': str(uuid4()),
                                       'token': 'smalltv_agent_' + secrets.token_urlsafe(32)} | changes))


def event(run_id=None, sequence=1, kind='START'):
    return {'event_id': str(uuid4()), 'run_id': run_id or str(uuid4()), 'sequence': sequence,
            'kind': kind, 'occurred_at': 1000}


@pytest.mark.parametrize('url', ['http://example.com:8000', 'http://localhost:8000', 'https://127.0.0.1:8000',
                               'http://127.0.0.1:8000/path', 'http://user:pw@127.0.0.1:8000',
                               'http://127.0.0.1', 'http://127.0.0.1:0', 'http://127.0.0.1:8000?key=value'])
def test_only_explicit_loopback_origin_is_accepted(modules, url):
    with pytest.raises(ValueError):
        credential(modules, base_url=url)


def test_dpapi_roundtrip_or_fail_closed_on_nonwindows(modules, tmp_path):
    value = credential(modules)
    path = tmp_path / 'key.dpapi'
    if os.name != 'nt':
        with pytest.raises(OSError):
            modules.keys.save_credential(path, value)
        assert not path.exists()
        return
    try:
        modules.keys.save_credential(path, value)
    except OSError as error:
        assert not path.exists()
        if error.errno in (2, 5):
            pytest.skip(f'DPAPI unavailable in this Windows logon context (OS code {error.errno})')
        raise
    assert value.token.encode() not in path.read_bytes()
    assert modules.keys.load_credential(path) == value
    with pytest.raises(FileExistsError):
        modules.keys.save_credential(path, value)
    assert value.token not in repr(value)


def test_queue_is_durable_bound_and_contains_no_key(modules, tmp_path):
    key, path = credential(modules), tmp_path / 'outbox.sqlite3'
    queue = modules.delivery.Outbox(path, key)
    start = event()
    final = event(start['run_id'], 2, 'DONE')
    queue.enqueue(start)
    queue.enqueue(final)
    queue.enqueue(start)
    reopened = modules.delivery.Outbox(path, key)
    assert reopened.count() == 2 and reopened.head() == start
    reopened.acknowledge(start['event_id'])
    assert reopened.head() == final
    assert key.token.encode() not in path.read_bytes()
    with pytest.raises(ValueError):
        modules.delivery.Outbox(path, credential(modules))
    with pytest.raises(ValueError):
        reopened.enqueue(final | {'kind': 'ERROR'})


def test_queue_capacity_rejects_without_dropping_data(modules, tmp_path, monkeypatch):
    queue = modules.delivery.Outbox(tmp_path / 'queue.sqlite3', credential(modules))
    monkeypatch.setattr(queue, 'LIMIT', 1)
    first = event()
    queue.enqueue(first)
    with pytest.raises(ValueError):
        queue.enqueue(event())
    assert queue.head() == first and queue.count() == 1
    with pytest.raises(ValueError):
        queue.enqueue(event() | {'text': 'private input'})


def test_existing_nonqueue_database_is_not_modified(modules, tmp_path):
    path = tmp_path / 'other.sqlite3'
    with sqlite3.connect(path) as connection:
        connection.execute('CREATE TABLE keep (value TEXT)')
    original = path.read_bytes()
    with pytest.raises(ValueError):
        modules.delivery.Outbox(path, credential(modules))
    assert path.read_bytes() == original


class FakeClient:
    def __init__(self, key, events):
        self.credential, self.events, self.sent = key, list(events), []

    def request(self, route, payload=None):
        self.sent.append((route, payload))
        if route == 'identity':
            return 200, {'agent_id': self.credential.agent_id, 'kind': 'codex_windows'}, 0
        if route == 'heartbeat':
            return 204, None, 0
        return self.events.pop(0)


def ack(value, duplicate=False):
    return {name: value[name] for name in ('event_id', 'run_id', 'sequence')} | {'duplicate': duplicate}


def test_network_retry_preserves_event_id_and_heartbeat_is_not_queued(modules, tmp_path):
    key, first = credential(modules), event()
    queue = modules.delivery.Outbox(tmp_path / 'queue.sqlite3', key)
    queue.enqueue(first)
    client = FakeClient(key, [(-1, None, 0), (200, ack(first, True), 0)])
    clock = [100]
    sender = modules.delivery.Sender(client, queue, clock=lambda: clock[0])
    sender.tick()
    assert queue.count() == 1
    sender.tick()
    assert len([r for r, _ in client.sent if r == 'events']) == 1
    clock[0] = 101
    sender.tick()
    assert queue.count() == 0
    assert [body for r, body in client.sent if r == 'events'] == [first, first]
    assert [r for r, _ in client.sent].count('heartbeat') == 1
    clock[0] = 110
    sender.tick()
    assert [r for r, _ in client.sent].count('heartbeat') == 2 and queue.count() == 0


@pytest.mark.parametrize('status', [401, 403, 404, 409, 422, 302, 429])
def test_permanent_errors_preserve_queue_and_stop_retry(modules, tmp_path, status):
    key = credential(modules)
    queue = modules.delivery.Outbox(tmp_path / 'q.sqlite3', key)
    queue.enqueue(event())
    client = FakeClient(key, [(status, None, 0)])
    sender = modules.delivery.Sender(client, queue)
    sender.tick()
    assert sender.blocked is not None and queue.count() == 1
    count = len(client.sent)
    sender.tick()
    assert len(client.sent) == count


def test_rate_limit_respects_server_retry_after(modules, tmp_path):
    key, first = credential(modules), event()
    queue = modules.delivery.Outbox(tmp_path / 'q.sqlite3', key)
    queue.enqueue(first)
    client = FakeClient(key, [(429, None, 60), (201, ack(first), 0)])
    clock = [100]
    sender = modules.delivery.Sender(client, queue, clock=lambda: clock[0])
    sender.tick()
    clock[0] = 159
    sender.tick()
    assert queue.count() == 1
    clock[0] = 160
    sender.tick()
    assert queue.count() == 0


@pytest.mark.parametrize('change', [{'event_id': str(uuid4())}, {'sequence': True}, {'duplicate': 0}, {'extra': 'private'}])
def test_malformed_ack_cannot_delete_event(modules, tmp_path, change):
    key, first = credential(modules), event()
    queue = modules.delivery.Outbox(tmp_path / 'q.sqlite3', key)
    queue.enqueue(first)
    sender = modules.delivery.Sender(FakeClient(key, [(201, ack(first) | change, 0)]), queue)
    sender.tick()
    assert sender.blocked and queue.head() == first


def test_identity_mismatch_cannot_deliver_queue(modules, tmp_path, monkeypatch):
    key = credential(modules)
    queue = modules.delivery.Outbox(tmp_path / 'q.sqlite3', key)
    queue.enqueue(event())
    client = FakeClient(key, [])
    monkeypatch.setattr(client, 'request', lambda *a: (200, {'agent_id': str(uuid4()), 'kind': 'codex_windows'}, 0))
    sender = modules.delivery.Sender(client, queue)
    sender.tick()
    assert sender.blocked == 'identity_mismatch' and queue.count() == 1


def test_empty_queue_does_not_claim_connection_when_api_is_down(modules, tmp_path, monkeypatch):
    key = credential(modules)
    queue = modules.delivery.Outbox(tmp_path / 'q.sqlite3', key)
    client = FakeClient(key, [])
    monkeypatch.setattr(client, 'request', lambda *a: (-1, None, 0))
    result = modules.delivery.Sender(client, queue).flush(timeout=0)
    assert result == {'pending': 0, 'blocked': None, 'connected': False}


def test_real_loopback_http_backend_delivery_and_revocation(modules, tmp_path, monkeypatch, capsys):
    database = Database(tmp_path / 'server.sqlite3')
    database.initialize()
    password = secrets.token_urlsafe(24)
    create_account(database, 'delivery-test', password, 'Software delivery test')
    settings = Settings(_env_file=None, db_path=database.path, cookie_secure=False)
    with TestClient(create_app(settings)) as session:
        login = session.post('/api/v1/auth/login', headers={'X-SmallTV-Client': 'web'}, json={'username': 'delivery-test', 'password': password}).json()
        csrf = {'X-CSRF-Token': login['csrf_token']}
        issued = session.post('/api/v1/agents/codex', headers=csrf, json={'name': 'Loopback test agent'}).json()
        listener = socket.socket()
        listener.bind(('127.0.0.1', 0))
        listener.listen()
        server = uvicorn.Server(uvicorn.Config(create_app(settings), log_level='error', lifespan='on'))
        thread = Thread(target=server.run, kwargs={'sockets': [listener]}, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 10
            while not server.started and time.monotonic() < deadline:
                time.sleep(0.01)
            assert server.started
            key = credential(modules, agent_id=issued['agent_id'], token=issued['token'],
                             base_url=f'http://127.0.0.1:{listener.getsockname()[1]}')
            queue = modules.delivery.Outbox(tmp_path / 'outbox.sqlite3', key)
            start = event()
            final = event(start['run_id'], 2, 'DONE')
            queue.enqueue(start)
            queue.enqueue(final)
            assert modules.delivery.Sender(modules.delivery.Client(key), queue).flush(timeout=3)['pending'] == 0
            run_path = f"/api/v1/agents/codex/{key.agent_id}/runs/{start['run_id']}"
            assert session.get(run_path).json()['last_reported_state'] == 'DONE'
            assert session.get(f'/api/v1/agents/codex/{key.agent_id}/presence').json()['state'] == 'ONLINE'
            # Lost acknowledgement: enqueue the identical event again after restart.
            queue.enqueue(final)
            sender = modules.delivery.Sender(modules.delivery.Client(key), queue)
            sender.tick()
            assert queue.count() == 0
            with database.connection() as connection:
                assert connection.execute('SELECT COUNT(*) FROM codex_events').fetchone()[0] == 2
            # Full bridge with a local Python process instead of a model.
            lines = [{'type': 'turn.started'},
                     {'type': 'item.completed', 'item': {'id': 'fixture', 'type': 'agent_message', 'text': 'STORM_OK'}},
                     {'type': 'turn.completed', 'usage': {'input_tokens': 1, 'cached_input_tokens': 0, 'output_tokens': 1}}]
            fixture_text = '\n'.join(json.dumps(line) for line in lines) + '\n'
            command = [sys.executable, '-c', f'import sys; sys.stdout.write({fixture_text!r})']
            monkeypatch.setattr(modules.cli, 'load_credential', lambda path: key)
            monkeypatch.setattr(modules.cli, 'run_smoke', lambda **hooks: modules.smoke.observe(command, tmp_path, **hooks))
            assert modules.cli.main(['--outbox', str(tmp_path / 'bridge.sqlite3'), 'run-smoke']) == 0
            report = json.loads(capsys.readouterr().out)
            assert report['status'] == 'delivered' and report['pending'] == 0
            assert report['exec']['status'] == 'verified'
            assert issued['token'] not in json.dumps(report)
            with database.connection() as connection:
                assert connection.execute('SELECT COUNT(*) FROM codex_events').fetchone()[0] == 4
            session.post(f'/api/v1/agents/codex/{key.agent_id}/credentials/revoke', headers=csrf)
            queue.enqueue(event())
            sender = modules.delivery.Sender(modules.delivery.Client(key), queue)
            sender.tick()
            assert sender.blocked == 'authorization' and queue.count() == 1
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            listener.close()
            assert not thread.is_alive()


def test_credential_file_storage_guards_with_injected_cipher(modules, monkeypatch, tmp_path):
    # Tests file handling only, not DPAPI cryptography. Temporary runtime-only key.
    monkeypatch.setattr(modules.keys, 'dpapi', lambda data, **options: bytes(b ^ 165 for b in data))
    value, path = credential(modules), tmp_path / 'vault.dpapi'
    modules.keys.save_credential(path, value)
    assert modules.keys.load_credential(path) == value
    assert value.token.encode() not in path.read_bytes()
    with pytest.raises(FileExistsError):
        modules.keys.save_credential(path, value)
    assert modules.keys.load_credential(path) == value


def test_unavailable_protection_never_creates_plaintext_file(modules, monkeypatch, tmp_path):
    def unavailable(*args, **kwargs):
        raise OSError(2, 'DPAPI unavailable')
    monkeypatch.setattr(modules.keys, 'dpapi', unavailable)
    path = tmp_path / 'vault.dpapi'
    with pytest.raises(OSError):
        modules.keys.save_credential(path, credential(modules))
    assert not path.exists()


def test_runtime_file_extensions_must_be_ignored_by_git(modules, tmp_path):
    value = credential(modules)
    with pytest.raises(ValueError):
        modules.keys.save_credential(tmp_path / 'key.json', value)
    with pytest.raises(ValueError):
        modules.delivery.Outbox(tmp_path / 'outbox.json', value)
    assert list(tmp_path.iterdir()) == []


def test_smoke_hooks_queue_events_and_fail_closed_on_storage_error(modules, tmp_path):
    fixture = [sys.executable, '-c', 'print(\'{"type":"turn.started"}\')']
    values, ticks = [], []
    result = modules.smoke.observe(fixture, tmp_path, event_sink=values.append, tick=lambda: ticks.append(1))
    assert values[0]['kind'] == 'START' and ticks
    assert result['parser_outcome'] == 'UNKNOWN'  # EOF is not success.
    def reject(value):
        raise ValueError('private storage details')
    result = modules.smoke.observe(fixture, tmp_path, event_sink=reject)
    assert result['status'] == 'delivery_error' and 'private' not in json.dumps(result)


def test_store_probe_uses_no_actual_key_or_network(modules, monkeypatch, capsys):
    monkeypatch.setattr(modules.cli, 'dpapi', lambda data, **options: bytes(b ^ 165 for b in data))
    assert modules.cli.main(['check-key-store']) == 0
    assert json.loads(capsys.readouterr().out) == {'status': 'dpapi_available'}


def test_local_setup_error_never_echoes_secret_or_os_error(modules, monkeypatch, capsys):
    value = credential(modules)
    def fail(*args):
        raise OSError('private ' + value.token)
    monkeypatch.setattr(modules.cli, 'load_credential', fail)
    assert modules.cli.main(['flush']) == 1
    output = capsys.readouterr().out
    assert value.token not in output and 'private' not in output


def test_http_redirects_are_not_followed_even_on_loopback(modules):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    paths = []
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            paths.append(self.path)
            self.send_response(302)
            self.send_header('Location', '/capture')
            self.end_headers()
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        key = credential(modules, base_url=f'http://127.0.0.1:{server.server_port}')
        assert modules.delivery.Client(key).request('identity')[0] == 302
        assert paths == ['/api/v1/agents/codex/identity']
    finally:
        server.shutdown()
        thread.join(timeout=3)
        server.server_close()


def test_incomplete_http_response_is_retryable_without_raw_error(modules, monkeypatch):
    from http.client import IncompleteRead
    client = modules.delivery.Client(credential(modules))
    def broken(*args, **kwargs):
        raise IncompleteRead(b'private truncated response')
    monkeypatch.setattr(client.opener, 'open', broken)
    assert client.request('heartbeat', {}) == (-1, None, 0)


def reserved_listener(port=0):
    listener = socket.socket()
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(('127.0.0.1', port))
    # Bound but not listening: reserve our test port while the API is offline.
    return listener


@contextmanager
def running_api(listener, settings):
    listener.listen()
    server = uvicorn.Server(uvicorn.Config(create_app(settings), log_level='error', lifespan='on'))
    thread = Thread(target=server.run, kwargs={'sockets': [listener]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server.started, 'Isolated API did not start'
        yield
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        listener.close()
        assert not thread.is_alive(), 'Isolated API did not stop'


@pytest.mark.parametrize('completed', [True, False], ids=['confirmed-done', 'unconfirmed-eof'])
@pytest.mark.parametrize('lost_ack', [False, True], ids=['normal-recovery', 'lost-ack-and-second-outage'])
def test_real_api_outage_restart_and_durable_recovery_without_duplicates(modules, tmp_path, completed, lost_ack):
    database = Database(tmp_path / 'outage-server.sqlite3')
    database.initialize()
    password = secrets.token_urlsafe(24)
    create_account(database, 'outage-test', password, 'Isolated outage test')
    settings = Settings(_env_file=None, db_path=database.path, cookie_secure=False)
    with TestClient(create_app(settings)) as setup:
        login = setup.post('/api/v1/auth/login', headers={'X-SmallTV-Client': 'web'},
                           json={'username': 'outage-test', 'password': password}).json()
        issued = setup.post('/api/v1/agents/codex', headers={'X-CSRF-Token': login['csrf_token']},
                            json={'name': 'Isolated outage client'}).json()

    listener = reserved_listener()
    port = listener.getsockname()[1]
    key = credential(modules, base_url=f'http://127.0.0.1:{port}',
                     agent_id=issued['agent_id'], token=issued['token'])
    queue_path = tmp_path / 'outage-client.sqlite3'
    queue = modules.delivery.Outbox(queue_path, key)
    sender = modules.delivery.Sender(modules.delivery.Client(key), queue)
    lines = [{'type': 'turn.started'}]
    if completed:
        lines += [{'type': 'item.completed', 'item': {'id': 'fixture', 'type': 'agent_message', 'text': 'STORM_OK'}},
                  {'type': 'turn.completed', 'usage': {'input_tokens': 1, 'cached_input_tokens': 0, 'output_tokens': 1}}]
    text = '\n'.join(json.dumps(line) for line in lines) + '\n'
    command = [sys.executable, '-c', f'import sys; sys.stdout.write({text!r})']
    try:
        report = modules.smoke.observe(command, tmp_path, timeout=15,
                                       event_sink=queue.enqueue, tick=sender.tick)
        expected_count = 2 if completed else 1
        assert report['parser_outcome'] == ('DONE' if completed else 'UNKNOWN')
        assert queue.count() == expected_count
        assert sender.authenticated is False and sender.heartbeat_confirmed is False
        with database.connection() as connection:
            assert connection.execute('SELECT COUNT(*) FROM codex_events').fetchone()[0] == 0
            assert connection.execute('SELECT COUNT(*) FROM codex_runs').fetchone()[0] == 0
            assert connection.execute('SELECT COUNT(*) FROM agent_presence').fetchone()[0] == 0

        with closing(queue.connect()) as connection:
            queued = [json.loads(row[0]) for row in connection.execute('SELECT payload FROM outbox ORDER BY ordinal')]
        # New objects reconstruct the client after an exit; only SQLite carries state.
        reopened = modules.delivery.Outbox(queue_path, key)
        assert reopened.count() == expected_count and reopened.head() == queued[0]

        class DropFirstAcknowledgement(modules.delivery.Client):
            dropped = False
            def request(self, route, payload=None):
                response = super().request(route, payload)
                if route == 'events' and response[0] == 201 and not self.dropped:
                    self.dropped = True
                    return -1, None, 0  # Server committed, but the client received no ACK.
                return response

        with running_api(listener, settings):
            client = DropFirstAcknowledgement(key) if lost_ack else modules.delivery.Client(key)
            recovered = modules.delivery.Sender(client, reopened)
            if lost_ack:
                recovered.tick()
                assert client.dropped and reopened.count() == expected_count
                with database.connection() as connection:
                    assert connection.execute('SELECT COUNT(*) FROM codex_events').fetchone()[0] == 1
                    assert connection.execute('SELECT last_reported_state FROM codex_runs').fetchone()[0] == 'WORKING'
            else:
                result = recovered.flush(timeout=5)
                assert result == {'pending': 0, 'blocked': None, 'connected': True}

        if lost_ack:
            # The API is really stopped now. Another network failure must retain the same payloads.
            listener = reserved_listener(port)
            offline_queue = modules.delivery.Outbox(queue_path, key)
            offline = modules.delivery.Sender(modules.delivery.Client(key), offline_queue)
            offline.tick()
            assert not offline.authenticated and offline_queue.count() == expected_count
            assert offline_queue.head() == queued[0]
            with running_api(listener, settings):
                final_queue = modules.delivery.Outbox(queue_path, key)
                final_sender = modules.delivery.Sender(modules.delivery.Client(key), final_queue)
                result = final_sender.flush(timeout=8)
                assert result == {'pending': 0, 'blocked': None, 'connected': True}

        with database.connection() as connection:
            saved = connection.execute('''SELECT event_id, run_id, sequence, kind, occurred_at
                FROM codex_events ORDER BY sequence''').fetchall()
            assert [dict(row) for row in saved] == queued
            assert connection.execute('SELECT COUNT(*) FROM codex_runs').fetchone()[0] == 1
            assert connection.execute('SELECT last_reported_state FROM codex_runs').fetchone()[0] == ('DONE' if completed else 'WORKING')
        assert modules.delivery.Outbox(queue_path, key).count() == 0
    finally:
        listener.close()
