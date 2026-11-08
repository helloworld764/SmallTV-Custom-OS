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


class DeliveryHookError(Exception):
    pass


def observe(command, cwd, timeout=120, *, event_sink=None, tick=None, input_text=None,
            require_answer=True, answer_sink=None, cancel_event=None):
    parser = ExecEvents()
    report = {'status': 'failed_start', 'real_run_checked': False, 'exit_code': None,
              'parser_outcome': 'UNKNOWN', 'parser_reason': None, 'answer_matches': False,
              'event_counts': dict.fromkeys(KNOWN_TYPES, 0), 'unknown_event_count': 0,
              'stderr_present': False, 'argument_hint': None}
    if not require_answer:
        report['answer_matches'] = None
    answer = None
    try:
        process = subprocess.Popen(command, cwd=cwd, stdin=subprocess.PIPE if input_text is not None else subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False,
                                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
                                   start_new_session=os.name != 'nt')
    except OSError:
        return report
    report['real_run_checked'] = True
    messages, stop, excessive, reader_error, input_error = queue.Queue(maxsize=8), Event(), Event(), Event(), Event()

    def input_writer():
        try:
            process.stdin.write(input_text.encode('utf-8'))
        except (OSError, ValueError):
            input_error.set()
        finally:
            try:
                process.stdin.close()
            except (OSError, ValueError):
                input_error.set()

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
    writer = Thread(target=input_writer, daemon=True) if input_text is not None else None
    if writer is not None:
        writer.start()
    deadline, size, pending = time.monotonic() + timeout, 0, b''

    def emit(events):
        if event_sink is not None:
            try:
                for event in events:
                    event_sink(event.to_dict())
            except Exception:
                raise DeliveryHookError from None

    def consume(raw):
        nonlocal answer
        try:
            line = raw.decode('utf-8')
            events = parser.feed(line)
            message = json.loads(line) if line.strip() else {}
        except (UnicodeError, ValueError, RecursionError):
            return False
        emit(events)
        kind = message.get('type') if isinstance(message, dict) else None
        if isinstance(kind, str) and kind in KNOWN_TYPES:
            report['event_counts'][kind] += 1
        elif line.strip():
            report['unknown_event_count'] += 1
        item = message.get('item') if isinstance(message, dict) else None
        if kind == 'item.completed' and isinstance(item, dict) and item.get('type') == 'agent_message':
            if require_answer:
                report['answer_matches'] = item.get('text') == 'STORM_OK'
            elif answer_sink is not None and isinstance(item.get('text'), str):
                answer = item['text'][:32768]
                if len(item['text']) > 32768:
                    answer += '\n[Storm: answer truncated at 32768 characters]'
        return len(line) <= MAX_LINE_CHARS and parser.reason is None

    try:
        while True:
            if cancel_event is not None and cancel_event.is_set():
                report['status'] = 'cancelled'
                break
            if input_error.is_set():
                report['status'] = 'stdin_write_failed'
                break
            if tick is not None:
                try:
                    tick()
                except Exception:
                    raise DeliveryHookError from None
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
                    report['status'] = 'output_limit' if parser.reason == 'line_limit' else 'parser_rejected'
                    break
            else:
                if chunk:
                    continue
                if pending and not consume(pending):
                    report['status'] = 'output_limit' if parser.reason == 'line_limit' else 'parser_rejected'
                    break
                process.wait(timeout=max(0.01, deadline - time.monotonic()))
                if writer is not None:
                    writer.join(timeout=1)
                    if writer.is_alive() or input_error.is_set():
                        report['status'] = 'stdin_write_failed'
                        break
                emit(parser.finish(process.returncode))
                if parser.outcome == 'DONE' and (not require_answer or report['answer_matches']):
                    report['status'] = 'verified'
                elif not require_answer and parser.outcome == 'ERROR':
                    report['status'] = 'failed'
                else:
                    report['status'] = 'unexpected_result'
            break
    except subprocess.TimeoutExpired:
        report['status'] = 'timeout'
    except KeyboardInterrupt:
        report['status'] = 'cancelled'
    except DeliveryHookError:
        report['status'] = 'delivery_error'
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
        if writer is not None:
            writer.join(timeout=1)
            if writer.is_alive():
                report['status'] = 'cleanup_failed'
        if any(reader.is_alive() for reader in readers):
            report['status'] = 'cleanup_failed'
        else:
            process.stdout.close()
            process.stderr.close()
        if report['status'] == 'verified' and (excessive.is_set() or reader_error.is_set()):
            report['status'] = 'output_limit' if excessive.is_set() else 'stream_read_failed'
    if report['status'] == 'cancelled' and process.returncode is not None:
        try:
            emit(parser.finish(process.returncode, cancelled_by_user=True))
        except DeliveryHookError:
            report['status'] = 'delivery_error'
    if report['status'] == 'verified' and answer_sink is not None and answer is not None:
        try:
            answer_sink(answer)
        except Exception:
            report['status'] = 'answer_output_failed'
    report.update(exit_code=process.returncode, parser_outcome=parser.outcome, parser_reason=parser.reason)
    return report


def run_smoke(*, event_sink=None, tick=None):
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
        if failure:
            report = base | failure
        else:
            options = {} if event_sink is None and tick is None else {'event_sink': event_sink, 'tick': tick}
            report = observe([*command, PROMPT], workspace, **options) | {'cli_version': base['cli_version']}
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
