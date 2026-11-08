import json
import secrets
from pathlib import Path
import subprocess
import sys
from threading import Event

import pytest


@pytest.fixture
def task(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'codex-monitor'))
    import task_runner
    return task_runner


def script(*lines, prefix='', exit_code=0, suffix=''):
    text = '\n'.join(json.dumps(line) for line in lines) + '\n'
    return [sys.executable, '-c', f'import sys,time; {prefix}sys.stdout.write({text!r}); sys.stdout.flush(); {suffix}sys.exit({exit_code})']


def terminal(kind='turn.completed'):
    if kind == 'turn.failed':
        return {'type': kind, 'error': {'message': 'private failure details'}}
    return {'type': kind, 'usage': {'input_tokens': 1, 'cached_input_tokens': 0, 'output_tokens': 1}}


def test_task_accepts_arbitrary_answer_and_never_echoes_prompt(task, tmp_path):
    import smoke_exec
    reports = []
    command = script({'type': 'turn.started'},
                     {'type': 'item.completed', 'item': {'id': 'answer', 'type': 'agent_message', 'text': 'private answer'}},
                     terminal(), prefix='value=sys.stdin.buffer.read(); ')
    result = smoke_exec.observe(command, tmp_path, input_text='private prompt', require_answer=False, event_sink=reports.append)
    assert result['status'] == 'verified' and result['answer_matches'] is None
    assert [r['kind'] for r in reports] == ['START', 'DONE']
    assert 'private' not in json.dumps(result) + json.dumps(reports)


def test_confirmed_task_failure_is_delivered_as_error(task, tmp_path):
    import smoke_exec
    reports = []
    result = smoke_exec.observe(script({'type': 'turn.started'}, terminal('turn.failed'), exit_code=1),
                                tmp_path, require_answer=False, event_sink=reports.append)
    assert result['status'] == 'failed' and result['parser_outcome'] == 'ERROR'
    assert [r['kind'] for r in reports] == ['START', 'ERROR']
    assert 'private' not in json.dumps(result) + json.dumps(reports)


@pytest.mark.parametrize('code', [0, 1, 9])
def test_exit_without_terminal_does_not_invent_done_or_error(task, tmp_path, code):
    import smoke_exec
    reports = []
    result = smoke_exec.observe(script({'type': 'turn.started'}, exit_code=code), tmp_path,
                                require_answer=False, event_sink=reports.append)
    assert result['parser_outcome'] == 'UNKNOWN'
    assert [r['kind'] for r in reports] == ['START']


@pytest.mark.parametrize('keyboard', [False, True], ids=['cancellation-request', 'keyboard-interrupt'])
def test_explicit_cancellation_stops_child_and_persists_interrupted(task, tmp_path, keyboard):
    import smoke_exec
    reports, cancel = [], Event()
    def sink(value):
        reports.append(value)
        if value['kind'] == 'START' and not keyboard:
            cancel.set()
    def tick():
        if keyboard and reports:
            raise KeyboardInterrupt
    result = smoke_exec.observe(script({'type': 'turn.started'}, suffix='time.sleep(30); '), tmp_path,
                                require_answer=False, event_sink=sink, tick=tick, cancel_event=cancel)
    assert result['status'] == 'cancelled' and result['exit_code'] is not None
    assert result['parser_outcome'] == 'INTERRUPTED'
    assert [r['kind'] for r in reports] == ['START', 'INTERRUPTED']


def test_timeout_is_unknown_not_user_cancellation(task, tmp_path):
    import smoke_exec
    reports = []
    result = smoke_exec.observe(script({'type': 'turn.started'}, suffix='time.sleep(30); '), tmp_path,
                                timeout=0.3, require_answer=False, event_sink=reports.append)
    assert result['status'] == 'timeout' and result['parser_outcome'] == 'UNKNOWN'
    assert all(r['kind'] == 'START' for r in reports)


def test_prompt_is_stdin_and_safety_flags_are_fixed(task, monkeypatch, tmp_path):
    prompt = 'private prompt marker'
    monkeypatch.setattr(task, 'probe_cli', lambda: {'status': 'manual_verification_required', 'cli_version': '0.167.0'})
    monkeypatch.setattr(task.shutil, 'which', lambda name: 'fixture-cli')
    monkeypatch.setattr(task, 'check_arguments', lambda *args: None)
    def observe(command, cwd, **options):
        assert command[-1] == '-' and prompt not in str(command)
        assert command[1:6] == ['exec', '--ignore-user-config', '--json', '--sandbox', 'read-only']
        assert '--ephemeral' in command and not any('danger' in value for value in command)
        assert options['input_text'] == prompt and options['answer_sink'] is None
        return {'status': 'verified'}
    monkeypatch.setattr(task, 'observe', observe)
    assert task.run_task(prompt, tmp_path)['status'] == 'verified'


def test_bad_argument_probe_blocks_task(task, monkeypatch, tmp_path):
    monkeypatch.setattr(task, 'probe_cli', lambda: {'status': 'manual_verification_required', 'cli_version': '0.167.0'})
    monkeypatch.setattr(task.shutil, 'which', lambda name: 'fixture-cli')
    monkeypatch.setattr(task, 'check_arguments', lambda *args: {'status': 'argument_check_failed'})
    monkeypatch.setattr(task, 'observe', lambda *a, **k: pytest.fail('Task may not start'))
    assert task.run_task('private prompt', tmp_path)['status'] == 'argument_check_failed'


@pytest.mark.parametrize('platform', ['win32', 'linux'])
def test_windows_sandbox_is_explicit_without_broadening_permissions(task, monkeypatch, tmp_path, platform):
    from types import SimpleNamespace
    monkeypatch.setattr(task, 'sys', SimpleNamespace(platform=platform))
    monkeypatch.setattr(task, 'probe_cli', lambda: {'status': 'manual_verification_required', 'cli_version': '0.167.0'})
    monkeypatch.setattr(task.shutil, 'which', lambda name: 'fixture-cli')
    checked = []
    monkeypatch.setattr(task, 'check_arguments', lambda command, cwd: checked.append(command.copy()))
    def observe(command, cwd, **options):
        assert command == [*checked[0], '-']
        assert ('windows.sandbox="elevated"' in command) == (platform == 'win32')
        assert command[command.index('--sandbox') + 1] == 'read-only'
        assert '--ignore-user-config' in command
        assert '--ignore-rules' not in command
        assert not any('danger-full-access' in value or 'bypass' in value for value in command)
        return {'status': 'verified'}
    monkeypatch.setattr(task, 'observe', observe)
    assert task.run_task('Read a public fixture', tmp_path)['status'] == 'verified'


@pytest.mark.parametrize('prompt,timeout', [('', 100), (' ', 100), ('x'*32769, 100), ('x', 0), ('x', 7201)])
def test_invalid_task_inputs_do_not_probe_or_run_cli(task, monkeypatch, tmp_path, prompt, timeout):
    monkeypatch.setattr(task, 'probe_cli', lambda: pytest.fail('No CLI access allowed'))
    with pytest.raises(ValueError):
        task.run_task(prompt, tmp_path, timeout=timeout)


def test_outbox_lock_blocks_concurrent_process_and_releases(task, tmp_path):
    queue = tmp_path / 'queue.sqlite3'
    command = [sys.executable, '-c',
               'import sys; sys.path.insert(0,sys.argv[1]); from task_runner import exclusive_outbox,OutboxBusy; '
               '\ntry:\n with exclusive_outbox(sys.argv[2]): sys.exit(0)\nexcept OutboxBusy: sys.exit(3)',
               str(Path(task.__file__).parent), str(queue)]
    with task.exclusive_outbox(queue):
        assert subprocess.run(command, capture_output=True, timeout=5).returncode == 3
    assert subprocess.run(command, capture_output=True, timeout=5).returncode == 0


def test_opt_in_answer_strips_terminal_controls(task, capsys):
    task.display_answer('hello\x1b[31m\x9dworld\n')
    output = capsys.readouterr().out
    assert '\x1b' not in output and '\x9d' not in output and 'hello' in output


def test_answer_on_legacy_windows_stdout_escapes_unsupported_unicode(task, monkeypatch):
    import io
    from types import SimpleNamespace
    raw = io.BytesIO()
    stream = io.TextIOWrapper(raw, encoding='cp1251', errors='strict')
    monkeypatch.setattr(task, 'sys', SimpleNamespace(stdout=stream))
    monkeypatch.setattr(sys, 'stdout', stream)
    task.display_answer('\u041f\u0440\u0438\u0432\u0435\u0442 \u2705 \U0001f30a')
    output = raw.getvalue().decode('cp1251')
    assert '\u041f\u0440\u0438\u0432\u0435\u0442' in output
    assert r'\u2705' in output and r'\U0001f30a' in output
    assert '--- End answer ---' in output


def test_answer_sink_is_only_called_after_verified_completion(task, tmp_path):
    import smoke_exec
    answers = []
    message = {'type': 'item.completed', 'item': {'id': 'answer', 'type': 'agent_message', 'text': 'answer'}}
    for final in (False, True):
        lines = [{'type': 'turn.started'}, message] + ([terminal()] if final else [])
        result = smoke_exec.observe(script(*lines), tmp_path, require_answer=False, answer_sink=answers.append)
        assert len(answers) == int(final)
        assert (result['status'] == 'verified') == final


@pytest.mark.parametrize('outcome,expected_status,expected_code', [('DONE', 'delivered', 0),
                                                               ('ERROR', 'exec_failed', 1),
                                                               ('INTERRUPTED', 'exec_interrupted', 1)])
def test_task_cli_status_and_queue_integrate_with_backend(task, monkeypatch, tmp_path, capsys,
                                                        outcome, expected_status, expected_code):
    from fastapi.testclient import TestClient
    from backend.app.config import Settings
    from backend.app.database import Database
    from backend.app.main import create_app
    from backend.app.security import create_account
    import local_monitor
    import local_credentials
    import smoke_exec
    database = Database(tmp_path / 'server.sqlite3')
    database.initialize()
    password = secrets.token_urlsafe(24)
    create_account(database, 'task-test', password, 'Isolated task test')
    with TestClient(create_app(Settings(_env_file=None, db_path=database.path))) as api:
        login = api.post('/api/v1/auth/login', headers={'X-SmallTV-Client': 'web'},
                         json={'username': 'task-test', 'password': password}).json()
        issued = api.post('/api/v1/agents/codex', headers={'X-CSRF-Token': login['csrf_token']},
                          json={'name': 'Isolated task client'}).json()
        key = local_credentials.Credential('http://127.0.0.1:8000', issued['agent_id'], issued['token'])
        class Client:
            def __init__(self, credential):
                self.credential = credential
            def request(self, route, payload=None):
                path = '/api/v1/agents/codex/' + route
                headers = {'Authorization': 'Bearer ' + self.credential.token}
                response = api.get(path, headers=headers) if payload is None else api.post(path, headers=headers, json=payload)
                return response.status_code, response.json() if response.content else None, 0
        monkeypatch.setattr(local_monitor, 'Client', Client)
        monkeypatch.setattr(local_monitor, 'load_credential', lambda path: key)
        monkeypatch.setattr(local_monitor.getpass, 'getpass', lambda prompt: 'private task text')
        cancel = Event()
        def run(prompt, cwd, **options):
            assert prompt == 'private task text'
            sink = options.pop('event_sink')
            options.pop('show_answer')
            def enqueue(value):
                sink(value)
                if outcome == 'INTERRUPTED':
                    cancel.set()
            lines = [{'type': 'turn.started'}]
            if outcome != 'INTERRUPTED':
                lines += [terminal('turn.failed' if outcome == 'ERROR' else 'turn.completed')]
            command = script(*lines, prefix='value=sys.stdin.read(); ', exit_code=int(outcome == 'ERROR'),
                             suffix='time.sleep(30); ' if outcome == 'INTERRUPTED' else '')
            return smoke_exec.observe(command, cwd, input_text=prompt, require_answer=False,
                                      cancel_event=cancel, event_sink=enqueue, **options)
        monkeypatch.setattr(local_monitor, 'run_task', run)
        queue_path = tmp_path / 'queue.sqlite3'
        code = local_monitor.main(['--outbox', str(queue_path), 'run-task', '--cwd', str(tmp_path)])
        report = json.loads(capsys.readouterr().out)
        assert code == expected_code and report['status'] == expected_status
        assert report['pending'] == 0 and report['exec']['parser_outcome'] == outcome
        assert 'private' not in json.dumps(report) and issued['token'] not in json.dumps(report)
        with database.connection() as connection:
            assert connection.execute('SELECT last_reported_state FROM codex_runs').fetchone()[0] == outcome
            assert [r[0] for r in connection.execute('SELECT kind FROM codex_events ORDER BY sequence')] == ['START', outcome]
        assert b'private task text' not in queue_path.read_bytes()


def test_cli_busy_queue_does_not_read_prompt_or_start_task(task, monkeypatch, tmp_path, capsys):
    import local_monitor
    import local_credentials
    key = local_credentials.Credential('http://127.0.0.1:8000', '11111111-1111-4111-8111-111111111111',
                                       'smalltv_agent_' + secrets.token_urlsafe(32))
    monkeypatch.setattr(local_monitor, 'load_credential', lambda path: key)
    monkeypatch.setattr(local_monitor.getpass, 'getpass', lambda *a: pytest.fail('Prompt must not be read'))
    queue = tmp_path / 'queue.sqlite3'
    with task.exclusive_outbox(queue):
        assert local_monitor.main(['--outbox', str(queue), 'run-task', '--cwd', str(tmp_path)]) == 1
    assert json.loads(capsys.readouterr().out)['status'] == 'already_running'


def test_large_utf8_prompt_is_delivered_completely_through_pipe(task, tmp_path):
    import smoke_exec
    prompt = '\u044f' * 32768
    command = script({'type': 'turn.started'}, terminal(),
                     prefix='value=sys.stdin.buffer.read(); assert len(value)==65536; ')
    result = smoke_exec.observe(command, tmp_path, timeout=5, input_text=prompt, require_answer=False)
    assert result['status'] == 'verified' and result['parser_outcome'] == 'DONE'


def test_child_closing_stdin_cannot_confirm_done_for_unwritten_prompt(task, tmp_path):
    import smoke_exec
    reports = []
    command = script({'type': 'turn.started'}, terminal())
    result = smoke_exec.observe(command, tmp_path, timeout=5, input_text='\U0001f600' * 32768,
                                require_answer=False, event_sink=reports.append)
    assert result['status'] == 'stdin_write_failed'
    assert not any(report['kind'] == 'DONE' for report in reports)
