import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest


@pytest.fixture
def smoke(monkeypatch):
    folder = Path(__file__).resolve().parents[1] / 'codex-monitor'
    monkeypatch.syspath_prepend(str(folder))
    spec = importlib.util.spec_from_file_location('storm_smoke_exec', folder / 'smoke_exec.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fake_command(lines, code=0):
    # Runs only a local Python fixture, never Codex or a model.
    text = '\n'.join(json.dumps(line) if isinstance(line, dict) else line for line in lines) + '\n'
    return [sys.executable, '-c', f'import sys; sys.stdout.write({text!r}); sys.stderr.write("private stderr"); sys.exit({code})']


def success_lines():
    return [{'type': 'thread.started', 'thread_id': 'synthetic-thread'}, {'type': 'turn.started'},
            {'type': 'item.completed', 'item': {'id': 'synthetic-item', 'type': 'agent_message', 'text': 'STORM_OK'}},
            {'type': 'turn.completed', 'usage': {'input_tokens': 1, 'cached_input_tokens': 0, 'output_tokens': 1}}]


def test_local_fixture_process_integrates_with_parser_and_discards_streams(smoke, tmp_path):
    result = smoke.observe(fake_command(success_lines()), tmp_path, timeout=5)
    assert result['status'] == 'verified' and result['parser_outcome'] == 'DONE'
    assert result['answer_matches'] is True and result['stderr_present'] is True
    assert result['event_counts']['turn.completed'] == 1
    assert 'private' not in json.dumps(result) and 'synthetic-thread' not in json.dumps(result)


@pytest.mark.parametrize('lines,code,status', [
    ([{'type': 'turn.started'}], 0, 'unexpected_result'),
    (success_lines(), 1, 'unexpected_result'),
    (['not json private text'], 0, 'parser_rejected'),
    ([{'type': 'unrecognized-private-type'}], 0, 'parser_rejected'),
])
def test_bad_real_process_streams_are_not_verified(smoke, tmp_path, lines, code, status):
    result = smoke.observe(fake_command(lines, code), tmp_path, timeout=5)
    assert result['status'] == status and 'private' not in json.dumps(result)


def test_timeout_terminates_only_fixture_process(smoke, tmp_path):
    result = smoke.observe([sys.executable, '-c', 'import time; time.sleep(30)'], tmp_path, timeout=0.2)
    assert result['status'] == 'timeout' and result['exit_code'] is not None
    assert result['parser_outcome'] == 'UNKNOWN'


def test_output_limit_stops_fixture_without_printing_it(smoke, tmp_path, monkeypatch):
    monkeypatch.setattr(smoke, 'MAX_STREAM_BYTES', 100)
    result = smoke.observe([sys.executable, '-c', 'print("private" * 1000)'], tmp_path, timeout=5)
    assert result['status'] == 'output_limit' and 'private' not in json.dumps(result)


@pytest.mark.parametrize('character,count', [('x', 90000), ('\u044f', 90000), ('x', 400000)])
def test_large_command_output_event_can_complete_without_leaking_contents(smoke, tmp_path, character, count):
    # Generate in the child: Windows cannot accept a 90K command-line argument.
    code = (
        'import json; '
        'print(json.dumps({"type":"turn.started"})); '
        'print(json.dumps({"type":"item.completed","item":{"id":"read",'
        '"type":"command_execution","aggregated_output":'
        f'"private-marker"+{ascii(character)}*{count}' '}} , ensure_ascii=False)); '
        'print(json.dumps({"type":"turn.completed","usage":'
        '{"input_tokens":1,"cached_input_tokens":0,"output_tokens":1}}))'
    )
    # Force UTF-8 independently of the Windows console code page.
    command = [sys.executable, '-X', 'utf8', '-c', code]
    events = []
    result = smoke.observe(command, tmp_path, timeout=5, require_answer=False, event_sink=events.append)
    assert result['status'] == 'verified' and result['parser_outcome'] == 'DONE'
    assert result['event_counts']['item.completed'] == 1
    assert result['max_line_chars'] > count and result['stdout_bytes'] > count
    assert result['limit_scope'] is None
    assert [event['kind'] for event in events] == ['START', 'DONE']
    assert 'private-marker' not in json.dumps(result) + json.dumps(events)


@pytest.mark.parametrize('trailing_newline', [True, False])
def test_oversized_valid_event_has_explicit_limit_and_no_done(smoke, tmp_path, trailing_newline, monkeypatch):
    # Isolate the parser's character guard from the production total byte cap.
    monkeypatch.setattr(smoke, 'MAX_STREAM_BYTES', smoke.MAX_LINE_CHARS * 2)
    code = (
        'import json; print(json.dumps({"type":"turn.started"})); '
        'print(json.dumps({"type":"item.completed","item":{"id":"read",'
        f'"type":"command_execution","aggregated_output":"private-marker"+"x"*{smoke.MAX_LINE_CHARS}' '}}), '
        f'end={repr(chr(10) if trailing_newline else "")})'
    )
    events = []
    result = smoke.observe([sys.executable, '-c', code], tmp_path, timeout=5,
                           require_answer=False, event_sink=events.append)
    assert result['status'] == 'output_limit' and result['parser_reason'] == 'line_limit'
    assert result['limit_scope'] == 'event'
    assert result['parser_outcome'] == 'UNKNOWN'
    assert [event['kind'] for event in events] == ['START']
    assert 'private-marker' not in json.dumps(result) + json.dumps(events)


def test_total_stream_limit_still_applies_to_individually_valid_events(smoke, tmp_path):
    code = (
        'import json; print(json.dumps({"type":"turn.started"})); '
        'event=json.dumps({"type":"item.completed","item":{"id":"read",'
        '"type":"command_execution","aggregated_output":"x"*90000}}); '
        '[print(event) for _ in range(13)]'
    )
    events = []
    result = smoke.observe([sys.executable, '-c', code], tmp_path, timeout=5,
                           require_answer=False, event_sink=events.append)
    assert result['status'] == 'output_limit' and result['parser_outcome'] == 'UNKNOWN'
    assert result['limit_scope'] == 'stdout' and result['stdout_bytes'] > smoke.MAX_STREAM_BYTES
    assert [event['kind'] for event in events] == ['START']


def test_missing_cli_does_not_start_model(smoke, monkeypatch):
    monkeypatch.setattr(smoke, 'probe_cli', lambda: {'status': 'not_found', 'cli_version': None})
    monkeypatch.setattr(smoke, 'observe', lambda *a, **k: pytest.fail('No exec allowed'))
    assert smoke.run_smoke() == {'status': 'not_found', 'cli_version': None, 'real_run_checked': False}


def prepare_profile(smoke, monkeypatch, tmp_path, help_text):
    monkeypatch.setattr(smoke, 'probe_cli', lambda: {'status': 'manual_verification_required', 'cli_version': '0.0.0-test'})
    monkeypatch.setattr(smoke.shutil, 'which', lambda name: 'fixture-cli')
    monkeypatch.setattr(smoke.subprocess, 'run', lambda *a, **k: SimpleNamespace(returncode=0, stdout=help_text))
    fake_source = tmp_path / 'repo' / 'codex-monitor' / 'smoke_exec.py'
    fake_source.parent.mkdir(parents=True)
    monkeypatch.setattr(smoke, '__file__', str(fake_source))
    temporary = tmp_path / 'system-temp'
    temporary.mkdir()
    monkeypatch.setattr(smoke.tempfile, 'gettempdir', lambda: str(temporary))
    return fake_source.parents[1]


def test_unsupported_safety_flags_prevent_any_exec(smoke, monkeypatch, tmp_path):
    prepare_profile(smoke, monkeypatch, tmp_path, '--ephemeral --skip-git-repo-check')
    monkeypatch.setattr(smoke, 'observe', lambda *a, **k: pytest.fail('No exec allowed'))
    assert smoke.run_smoke()['status'] == 'unsupported_smoke_profile'


def test_single_exec_uses_empty_workspace_and_fixed_safe_arguments(smoke, monkeypatch, tmp_path):
    root = prepare_profile(smoke, monkeypatch, tmp_path, '--ignore-user-config --ephemeral --skip-git-repo-check')
    sentinel = root / 'keep.txt'
    sentinel.write_text('keep', encoding='utf-8')
    calls = []
    def observe(command, cwd):
        assert list(cwd.iterdir()) == []
        assert cwd.parent == tmp_path / 'system-temp'
        assert command == ['fixture-cli', 'exec', '--ignore-user-config', '--json', '--sandbox', 'read-only',
                           '--ephemeral', '--skip-git-repo-check', smoke.PROMPT]
        calls.append(cwd)
        return {'status': 'verified', 'real_run_checked': True}
    monkeypatch.setattr(smoke, 'observe', observe)
    assert smoke.run_smoke()['status'] == 'verified'
    assert len(calls) == 1 and not calls[0].exists()
    assert sentinel.read_text(encoding='utf-8') == 'keep'


def test_cli_requires_explicit_run_and_never_echoes_os_error(smoke, monkeypatch, capsys):
    def fail():
        raise PermissionError('private error')
    monkeypatch.setattr(smoke, 'run_smoke', fail)
    assert smoke.main([]) == 1
    assert json.loads(capsys.readouterr().out)['status'] == 'requires_run_flag'
    assert smoke.main(['--run']) == 1
    result = capsys.readouterr().out
    assert json.loads(result)['status'] == 'runner_failed' and 'private' not in result


def test_cleanup_failure_cannot_claim_verified_or_hide_actual_attempt(smoke, monkeypatch, tmp_path):
    prepare_profile(smoke, monkeypatch, tmp_path, '--ignore-user-config --ephemeral --skip-git-repo-check')
    monkeypatch.setattr(smoke, 'observe', lambda *a: {'status': 'verified', 'real_run_checked': True})
    def refuse_cleanup(*args, **kwargs):
        raise PermissionError('private path')
    monkeypatch.setattr(smoke.shutil, 'rmtree', refuse_cleanup)
    result = smoke.run_smoke()
    assert result['status'] == 'cleanup_failed' and result['real_run_checked'] is True
    assert 'private' not in json.dumps(result)


def test_rejected_arguments_block_model_and_do_not_echo_error(smoke, monkeypatch, tmp_path):
    prepare_profile(smoke, monkeypatch, tmp_path, '--ignore-user-config --ephemeral --skip-git-repo-check')
    def run(command, **options):
        if '--json' in command:
            assert command[-1] == '--help' and smoke.PROMPT not in command
            return SimpleNamespace(returncode=2, stdout='', stderr="unexpected argument '--ignore-user-config'; private details")
        return SimpleNamespace(returncode=0, stdout='--ignore-user-config --ephemeral --skip-git-repo-check', stderr='')
    monkeypatch.setattr(smoke.subprocess, 'run', run)
    monkeypatch.setattr(smoke, 'observe', lambda *a: pytest.fail('No model task allowed'))
    result = smoke.run_smoke()
    assert result['status'] == 'argument_check_failed' and result['real_run_checked'] is False
    assert result['argument_hint'] == '--ignore-user-config' and 'private' not in json.dumps(result)


def test_exact_arguments_help_probe_uses_no_prompt(smoke, monkeypatch, tmp_path):
    seen = []
    def run(command, **options):
        seen.append(command)
        assert options['cwd'] == tmp_path and options['shell'] is False
        return SimpleNamespace(returncode=0, stdout='help', stderr='private details')
    monkeypatch.setattr(smoke.subprocess, 'run', run)
    assert smoke.check_arguments(['fixture-cli', 'exec', '--json'], tmp_path) is None
    assert seen == [['fixture-cli', 'exec', '--json', '--help']]


def test_exit_two_retains_only_known_argument_hint(smoke, tmp_path):
    command = [sys.executable, '-c', 'import sys; sys.stderr.write("unexpected argument --json private details"); sys.exit(2)']
    result = smoke.observe(command, tmp_path, timeout=5)
    assert result['status'] == 'unexpected_result' and result['exit_code'] == 2
    assert result['argument_hint'] == '--json' and 'private' not in json.dumps(result)
