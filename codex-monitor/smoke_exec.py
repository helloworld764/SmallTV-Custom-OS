"""Opt-in, single read-only exec smoke test; never prints or saves raw streams."""

import argparse
import json
import os
from pathlib import Path
import queue
import re
import shutil
import signal
import subprocess
import tempfile
from threading import Event, Thread
import time

from check_cli import probe_cli
from exec_events import ExecEvents, MAX_LINE_CHARS


KNOWN_TYPES = ('thread.started', 'turn.started', 'turn.completed', 'turn.failed',
               'item.started', 'item.updated', 'item.completed', 'error')
MAX_STREAM_BYTES = 1048576
PROMPT = 'Reply exactly STORM_OK. Do not use tools, read files, run commands or make changes.'


def argument_hint(stderr):
    # Return only a known flag name, never any part of an arbitrary error message.
    for flag in ('--ignore-user-config', '--ephemeral', '--skip-git-repo-check', '--sandbox', '--json'):
        if flag in stderr:
            return flag
    return None


def check_arguments(command, cwd):
    """Validate the exact exec option placement through help, without a prompt."""
    try:
        result = subprocess.run([*command, '--help'], cwd=cwd, stdin=subprocess.DEVNULL,
                                capture_output=True, text=True, encoding='utf-8', errors='replace',
                                timeout=10, shell=False, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except (OSError, subprocess.TimeoutExpired):
        return {'status': 'argument_check_failed', 'argument_hint': None}
    if result.returncode != 0:
        return {'status': 'argument_check_failed', 'argument_hint': argument_hint(result.stderr)}
    return None


def stop_process(process):
    """Stop only the process tree created by this test, never other Codex sessions."""
    if os.name == 'nt':
        subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=10, creationflags=subprocess.CREATE_NO_WINDOW, check=False)
        # taskkill can return access denied without raising. Always terminate
        # our direct child as a fallback; inherited pipes detect surviving descendants.
        if process.poll() is None:
            process.kill()
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def observe(command, cwd, timeout=120):
    parser = ExecEvents()
    report = {'status': 'failed_start', 'real_run_checked': False, 'exit_code': None,
              'parser_outcome': 'UNKNOWN', 'parser_reason': None, 'answer_matches': False,
              'event_counts': dict.fromkeys(KNOWN_TYPES, 0), 'unknown_event_count': 0,
              'stderr_present': False, 'argument_hint': None}
    try:
        process = subprocess.Popen(command, cwd=cwd, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False,
                                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
                                   start_new_session=os.name != 'nt')
    except OSError:
        return report
    report['real_run_checked'] = True
    messages, stop, excessive, reader_error = queue.Queue(maxsize=8), Event(), Event(), Event()

    def stdout_reader():
        while not stop.is_set():
            try:
                chunk = process.stdout.read1(4096)
            except (OSError, ValueError):
                reader_error.set()
                break
            while not stop.is_set():
                try:
                    messages.put(chunk, timeout=0.1)
                    break
                except queue.Full:
                    pass
            if not chunk:
                break

    def stderr_reader():
        size = 0
        tail = b''
        while not stop.is_set():
            try:
                chunk = process.stderr.read1(4096)
            except (OSError, ValueError):
                reader_error.set()
                break
            if not chunk:
                break
            report['stderr_present'] = True
            if report['argument_hint'] is None:
                report['argument_hint'] = argument_hint((tail + chunk).decode('utf-8', errors='replace'))
            tail = chunk[-128:]
            size += len(chunk)
            if size > MAX_STREAM_BYTES:
                excessive.set()
                break

    readers = [Thread(target=stdout_reader, daemon=True), Thread(target=stderr_reader, daemon=True)]
    for reader in readers:
        reader.start()
    deadline, size, pending = time.monotonic() + timeout, 0, b''

    def consume(raw):
        try:
            line = raw.decode('utf-8')
            parser.feed(line)
            message = json.loads(line) if line.strip() else {}
        except (UnicodeError, ValueError, RecursionError):
            return False
        kind = message.get('type') if isinstance(message, dict) else None
        if isinstance(kind, str) and kind in KNOWN_TYPES:
            report['event_counts'][kind] += 1
        elif line.strip():
            report['unknown_event_count'] += 1
        item = message.get('item') if isinstance(message, dict) else None
        if kind == 'item.completed' and isinstance(item, dict) and item.get('type') == 'agent_message':
            report['answer_matches'] = item.get('text') == 'STORM_OK'
        return len(line) <= MAX_LINE_CHARS and parser.reason is None

    try:
        while True:
            if reader_error.is_set():
                report['status'] = 'stream_read_failed'
                break
            if excessive.is_set():
                report['status'] = 'output_limit'
                break
            if time.monotonic() >= deadline:
                report['status'] = 'timeout'
                break
            try:
                chunk = messages.get(timeout=0.1)
            except queue.Empty:
                continue
            size += len(chunk)
            if size > MAX_STREAM_BYTES:
                report['status'] = 'output_limit'
                break
            pending += chunk
            if len(pending) > MAX_LINE_CHARS * 4 and b'\n' not in pending:
                report['status'] = 'output_limit'
                break
            while b'\n' in pending:
                raw, pending = pending.split(b'\n', 1)
                if not consume(raw):
                    report['status'] = 'parser_rejected'
                    break
            else:
                if chunk:
                    continue
                if pending and not consume(pending):
                    report['status'] = 'parser_rejected'
                    break
                process.wait(timeout=max(0.01, deadline - time.monotonic()))
                parser.finish(process.returncode)
                report['status'] = 'verified' if parser.outcome == 'DONE' and report['answer_matches'] else 'unexpected_result'
            break
    except subprocess.TimeoutExpired:
        report['status'] = 'timeout'
    except KeyboardInterrupt:
        report['status'] = 'cancelled'
    finally:
        if process.poll() is None:
            try:
                stop_process(process)
            except (OSError, subprocess.TimeoutExpired):
                process.kill()
        if report['status'] in ('verified', 'unexpected_result'):
            readers[1].join(timeout=1)
        stop.set()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            report['status'] = 'cleanup_failed'
        for reader in readers:
            reader.join(timeout=1)
        if any(reader.is_alive() for reader in readers):
            report['status'] = 'cleanup_failed'
        else:
            process.stdout.close()
            process.stderr.close()
        if report['status'] == 'verified' and (excessive.is_set() or reader_error.is_set()):
            report['status'] = 'output_limit' if excessive.is_set() else 'stream_read_failed'
    report.update(exit_code=process.returncode, parser_outcome=parser.outcome, parser_reason=parser.reason)
    return report


def run_smoke():
    diagnostic = probe_cli()
    base = {'status': diagnostic['status'], 'cli_version': diagnostic['cli_version'], 'real_run_checked': False}
    if diagnostic['status'] != 'manual_verification_required':
        return base
    executable = shutil.which('codex')
    if executable is None:
        return base | {'status': 'not_found'}
    help_text = ''
    try:
        for arguments in (['--help'], ['exec', '--help']):
            result = subprocess.run([executable, *arguments], stdin=subprocess.DEVNULL, capture_output=True,
                                    text=True, encoding='utf-8', errors='replace', timeout=10,
                                    shell=False, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            if result.returncode != 0 or len(result.stdout) > 65536:
                return base | {'status': 'probe_failed'}
            help_text += result.stdout
    except (OSError, subprocess.TimeoutExpired):
        return base | {'status': 'probe_failed'}
    for flag in ('--ignore-user-config', '--ephemeral', '--skip-git-repo-check'):
        if not re.search(r'(?<![\w-])' + re.escape(flag) + r'(?![\w-])', help_text):
            return base | {'status': 'unsupported_smoke_profile'}
    # Outside the repository: do not inherit its Git context or AGENTS.md.
    parent = Path(tempfile.gettempdir()).resolve()
    workspace = Path(tempfile.mkdtemp(prefix='storm-exec-smoke-', dir=parent)).resolve()
    report = base
    try:
        command = [executable, 'exec', '--ignore-user-config', '--json', '--sandbox', 'read-only',
                   '--ephemeral', '--skip-git-repo-check']
        failure = check_arguments(command, workspace)
        report = (base | failure) if failure else (observe([*command, PROMPT], workspace) | {'cli_version': base['cli_version']})
    except OSError:
        report = base | {'status': 'runner_failed', 'real_run_checked': None}
    finally:
        # Resolve and validate the exact newly-created target before recursive cleanup.
        if workspace.resolve().parent == parent and workspace.name.startswith('storm-exec-smoke-'):
            try:
                shutil.rmtree(workspace)
            except OSError:
                report = report | {'status': 'cleanup_failed'}
        else:
            report = report | {'status': 'cleanup_failed'}
    return report


def main(argv=None):
    arguments = argparse.ArgumentParser(description='Single opt-in read-only Codex smoke test')
    arguments.add_argument('--run', action='store_true', help='Run one actual Codex task using existing authentication/quota')
    options = arguments.parse_args(argv)
    try:
        report = run_smoke() if options.run else {'status': 'requires_run_flag', 'real_run_checked': False}
    except OSError:
        report = {'status': 'runner_failed', 'real_run_checked': False}
    except KeyboardInterrupt:
        report = {'status': 'cancelled', 'real_run_checked': False}
    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 0 if report['status'] == 'verified' else 1


if __name__ == '__main__':
    raise SystemExit(main())
