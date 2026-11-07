import importlib.util
import json
from pathlib import Path
from uuid import UUID

from fastapi.testclient import TestClient
import pytest

from backend.app.codex_events import EventRequest
from backend.app.config import Settings
from backend.app.database import Database
from backend.app.main import create_app
from backend.app.security import create_account
import secrets


@pytest.fixture
def parser_module():
    source = Path(__file__).resolve().parents[1] / 'codex-monitor' / 'exec_events.py'
    spec = importlib.util.spec_from_file_location('storm_exec_events', source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def feed(parser, kind, **fields):
    return parser.feed(json.dumps({'type': kind, **fields}))


def completed(parser):
    return feed(parser, 'turn.completed', usage={'input_tokens': 4, 'cached_input_tokens': 0, 'output_tokens': 2})


def test_success_waits_for_matching_exit_and_reports_only_safe_fields(parser_module):
    parser = parser_module.ExecEvents(clock=lambda: 1000)
    assert feed(parser, 'thread.started', thread_id='synthetic-thread') == []
    start = feed(parser, 'turn.started')
    assert [value.kind for value in start] == ['START']
    assert feed(parser, 'item.completed', item={'id': 'item_1', 'type': 'agent_message', 'text': 'private example content'}) == []
    assert completed(parser) == [] and parser.outcome == 'UNKNOWN'
    final = parser.finish(0)
    assert [value.kind for value in final] == ['DONE'] and parser.outcome == 'DONE'
    assert parser.finish(0) == []
    for report, sequence in zip(start + final, (1, 2)):
        data = report.to_dict()
        assert set(data) == {'event_id', 'run_id', 'sequence', 'kind', 'occurred_at'}
        assert data['run_id'] == parser.run_id and data['sequence'] == sequence
        assert UUID(data['event_id']).version == 4
        EventRequest.model_validate(data)
        assert 'private' not in json.dumps(data)
    with pytest.raises(RuntimeError, match='already finalized'):
        feed(parser, 'turn.started')


@pytest.mark.parametrize('exit_code', [None, 0, 1, -9])
def test_eof_or_exit_alone_never_claims_completion(parser_module, exit_code):
    parser = parser_module.ExecEvents()
    feed(parser, 'turn.started')
    assert parser.finish(exit_code) == [] and parser.outcome == 'UNKNOWN'


@pytest.mark.parametrize('exit_code', [None, 1, -9, True])
def test_completed_event_with_bad_or_missing_exit_cannot_be_done(parser_module, exit_code):
    parser = parser_module.ExecEvents()
    feed(parser, 'turn.started')
    completed(parser)
    assert parser.finish(exit_code) == [] and parser.outcome == 'UNKNOWN'


def test_confirmed_failure_and_user_cancellation_are_distinct(parser_module):
    parser = parser_module.ExecEvents()
    feed(parser, 'turn.started')
    feed(parser, 'turn.failed', error={'message': 'private error details'})
    reports = parser.finish(1)
    assert reports[0].kind == 'ERROR' and parser.outcome == 'ERROR'
    assert 'private' not in repr(vars(parser))
    cancelled = parser_module.ExecEvents()
    feed(cancelled, 'turn.started')
    assert cancelled.finish(None, cancelled_by_user=True)[0].kind == 'INTERRUPTED'


@pytest.mark.parametrize('terminal,exit_code,cancelled', [('turn.failed', 0, False), ('turn.failed', None, False),
                                                       ('turn.completed', 0, True)])
def test_conflicting_observations_remain_unknown(parser_module, terminal, exit_code, cancelled):
    parser = parser_module.ExecEvents()
    feed(parser, 'turn.started')
    if terminal == 'turn.failed':
        feed(parser, terminal, error={'message': 'synthetic failure'})
    else:
        completed(parser)
    assert parser.finish(exit_code, cancelled_by_user=cancelled) == []
    assert parser.outcome == 'UNKNOWN'


@pytest.mark.parametrize('bad_line', ['not json', '[]', '{}', 'null', '{"type":3}',
                                      '{"type":"turn.started","type":"turn.completed"}',
                                      '{"type":"ignored","n":NaN}', 'x' * 65537,
                                      '[' * 2000 + '0' + ']' * 2000, b'bytes'])
def test_corrupt_or_oversized_input_permanently_blocks_success(parser_module, bad_line):
    parser = parser_module.ExecEvents()
    feed(parser, 'turn.started')
    assert parser.feed(bad_line) == []
    assert completed(parser) == [] and parser.finish(0) == []
    assert parser.outcome == 'UNKNOWN'


@pytest.mark.parametrize('kind,fields', [('future.event', {}), ('error', {'message': 'private details'}),
                                       ('item.completed', {}), ('turn.completed', {}),
                                       ('turn.failed', {'error': 'private text'}),
                                       ('turn.completed', {'usage': {'input_tokens': True, 'cached_input_tokens': 0, 'output_tokens': 1}})])
def test_unknown_or_malformed_events_cannot_become_success(parser_module, kind, fields):
    parser = parser_module.ExecEvents()
    feed(parser, 'turn.started')
    assert feed(parser, kind, **fields) == []
    completed(parser)
    assert parser.finish(0) == [] and parser.outcome == 'UNKNOWN'
    assert 'private' not in repr(vars(parser))


def test_multiple_turns_or_repeated_terminal_are_rejected(parser_module):
    for extra in ('turn.started', 'turn.completed'):
        parser = parser_module.ExecEvents()
        feed(parser, 'turn.started')
        completed(parser)
        feed(parser, extra)
        assert parser.finish(0) == [] and parser.outcome == 'UNKNOWN'


def test_item_completion_is_not_turn_completion_and_output_is_discarded(parser_module):
    parser = parser_module.ExecEvents()
    feed(parser, 'turn.started')
    for kind in ('item.started', 'item.updated', 'item.completed'):
        assert feed(parser, kind, item={'id': 'item', 'type': 'command_execution',
                                     'command': 'private command', 'status': 'completed'}) == []
    assert parser.finish(0) == []
    assert 'private' not in repr(vars(parser))


@pytest.mark.parametrize('timestamp', [-1, True, 1.5, 253402300800])
def test_invalid_clock_cannot_produce_backend_events(parser_module, timestamp):
    parser = parser_module.ExecEvents(clock=lambda: timestamp)
    assert feed(parser, 'turn.started') == []
    assert completed(parser) == [] and parser.finish(0) == []
    assert parser.reason == 'invalid_clock'


def test_parsers_do_not_mix_runs_and_blank_lines_are_ignored(parser_module):
    first, second = parser_module.ExecEvents(), parser_module.ExecEvents()
    assert first.run_id != second.run_id
    assert first.feed('\n') == []
    feed(first, 'turn.started')
    assert first.finish(None) == []
    assert second.finish(0) == []


def test_synthetic_parser_events_integrate_with_backend_and_sqlite(parser_module, tmp_path):
    database = Database(tmp_path / 'parser.sqlite3')
    database.initialize()
    password = secrets.token_urlsafe(24)
    create_account(database, 'parser-test', password, 'Software parser test')
    parser = parser_module.ExecEvents(clock=lambda: 1000)
    reports = feed(parser, 'turn.started')
    completed(parser)
    reports += parser.finish(0)
    with TestClient(create_app(Settings(_env_file=None, db_path=database.path))) as client:
        login = client.post('/api/v1/auth/login', headers={'X-SmallTV-Client': 'web'}, json={'username': 'parser-test', 'password': password})
        credential = client.post('/api/v1/agents/codex', headers={'X-CSRF-Token': login.json()['csrf_token']}, json={'name': 'Offline parser test'}).json()
        auth = {'Authorization': 'Bearer ' + credential['token']}
        for report in reports:
            assert client.post('/api/v1/agents/codex/events', headers=auth, json=report.to_dict()).status_code == 201
            assert client.post('/api/v1/agents/codex/events', headers=auth, json=report.to_dict()).status_code == 200
        run = client.get(f"/api/v1/agents/codex/{credential['agent_id']}/runs/{parser.run_id}").json()
        assert run['last_reported_state'] == 'DONE' and run['last_sequence'] == 2
        assert client.get(f"/api/v1/agents/codex/{credential['agent_id']}/presence").json()['state'] == 'UNKNOWN'
