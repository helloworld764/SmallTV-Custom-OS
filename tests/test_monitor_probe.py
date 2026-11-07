import importlib.util
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest


@pytest.fixture
def probe(monkeypatch):
    source = Path(__file__).resolve().parents[1] / 'codex-monitor' / 'check_cli.py'
    spec = importlib.util.spec_from_file_location('storm_monitor_check_cli', source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module.shutil, 'which', lambda name: 'test-cli.cmd')
    return module


def replies(probe, monkeypatch, version='codex-cli 0.0.0-test\n', help_text='Usage: codex exec\n --json\n --sandbox <MODE> [read-only, workspace-write]'):
    calls = []
    def run(arguments, **options):
        calls.append((arguments, options))
        return SimpleNamespace(returncode=0, stdout=version if '--version' in arguments else help_text, stderr='')
    monkeypatch.setattr(probe.subprocess, 'run', run)
    return calls


def test_probe_only_calls_version_and_exec_help_without_starting_tasks(probe, monkeypatch):
    calls = replies(probe, monkeypatch)
    report = probe.probe_cli()
    assert report['status'] == 'manual_verification_required'
    assert report['cli_version'] == '0.0.0-test' and report['real_run_checked'] is False
    assert all(report['checks'].values())
    assert [entry[0] for entry in calls] == [['test-cli.cmd', '--version'], ['test-cli.cmd', 'exec', '--help']]
    for _, options in calls:
        assert options['shell'] is False and options['timeout'] == 10
        assert options['stdin'] == subprocess.DEVNULL


def test_absent_cli_does_not_spawn_any_process(probe, monkeypatch):
    monkeypatch.setattr(probe.shutil, 'which', lambda name: None)
    monkeypatch.setattr(probe.subprocess, 'run', lambda *a, **k: pytest.fail('No process may start'))
    assert probe.probe_cli()['status'] == 'not_found'


@pytest.mark.parametrize('failure', [PermissionError('private-path'), FileNotFoundError('private-path'),
                                    subprocess.TimeoutExpired('private-command', 10, output='private-output')])
def test_failed_probe_does_not_expose_exception_details(probe, monkeypatch, failure):
    def run(*args, **kwargs):
        raise failure
    monkeypatch.setattr(probe.subprocess, 'run', run)
    report = probe.probe_cli()
    assert report['status'] == 'probe_failed' and 'private' not in json.dumps(report)


@pytest.mark.parametrize('stdout,stderr,code', [('secret-output', 'secret-error', 1),
                                               ('x' * 65537, '', 0), ('', 'x' * 65537, 0)])
def test_failed_or_excessive_output_is_not_reported(probe, monkeypatch, stdout, stderr, code):
    monkeypatch.setattr(probe.subprocess, 'run', lambda *a, **k: SimpleNamespace(returncode=code, stdout=stdout, stderr=stderr))
    report = probe.probe_cli()
    assert report['status'] == 'probe_failed'
    assert 'secret' not in json.dumps(report)


@pytest.mark.parametrize('help_text', ['', '--json-file --sandboxed read-only-extra', '--json --sandbox', '--json read-only'])
def test_missing_or_similarly_named_flags_do_not_claim_compatibility(probe, monkeypatch, help_text):
    replies(probe, monkeypatch, help_text=help_text)
    assert probe.probe_cli()['status'] == 'incompatible_or_unknown'


def test_unrecognized_version_is_not_echoed(probe, monkeypatch):
    replies(probe, monkeypatch, version='unknown private output')
    report = probe.probe_cli()
    assert report['status'] == 'incompatible_or_unknown' and report['cli_version'] is None
    assert 'private' not in json.dumps(report)


def test_help_and_executable_path_are_not_copied_to_report(probe, monkeypatch):
    replies(probe, monkeypatch, version='codex-cli 0.0.0-test\nprivate text',
            help_text='--json --sandbox read-only\nprivate details')
    report = json.dumps(probe.probe_cli())
    assert 'private' not in report and 'test-cli.cmd' not in report


@pytest.mark.parametrize('available,expected_code', [(True, 0), (False, 1)])
def test_cli_outputs_only_safe_json_and_explicit_unverified_status(probe, monkeypatch, capsys, available, expected_code):
    replies(probe, monkeypatch)
    if not available:
        monkeypatch.setattr(probe.shutil, 'which', lambda name: None)
    assert probe.main() == expected_code
    report = json.loads(capsys.readouterr().out)
    assert report['real_run_checked'] is False
