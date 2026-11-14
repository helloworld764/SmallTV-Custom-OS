"""Explicit local task/smoke bridge, protected credentials and durable outbox."""

import argparse
import getpass
import json
import secrets
from pathlib import Path
import sqlite3
import warnings

from delivery import Client, Outbox, Sender
from local_credentials import Credential, load_credential, save_credential, dpapi
from smoke_exec import run_smoke
from task_runner import run_task, exclusive_outbox, OutboxBusy, MAX_PROMPT_CHARS


ROOT = Path(__file__).resolve().parents[1]


def main(argv=None):
    parser = argparse.ArgumentParser(description='Storm Windows monitor bridge (verified HTTPS or loopback HTTP)')
    parser.add_argument('--key-file', type=Path, default=ROOT / 'secrets' / 'storm-agent.dpapi')
    parser.add_argument('--outbox', type=Path, default=ROOT / 'data' / 'codex-monitor' / 'outbox.sqlite3')
    commands = parser.add_subparsers(dest='command', required=True)
    setup = commands.add_parser('init-key', help='Store an already-issued agent key using current-user Windows DPAPI')
    setup.add_argument('--agent-id', required=True)
    setup.add_argument('--url', default='http://127.0.0.1:8000')
    setup.add_argument('--ca-file',type=Path,help='Optional trusted CA certificate for a private HTTPS server')
    commands.add_parser('status', help='Local queue count, no HTTP/model calls')
    commands.add_parser('check-key-store', help='DPAPI roundtrip with disposable random bytes; no real key needed')
    commands.add_parser('flush', help='Deliver saved events and a current heartbeat; no model call')
    commands.add_parser('run-smoke', help='One actual Codex smoke task, durable event queue and local delivery')
    task = commands.add_parser('run-task', help='Explicit read-only Codex task with hidden prompt via stdin')
    task.add_argument('--cwd', type=Path, required=True, help='Directory to analyze; not stored in telemetry')
    task.add_argument('--timeout-seconds', type=int, default=1800, metavar='10..7200')
    task.add_argument('--show-answer', action='store_true', help='Opt in to showing the final answer in this terminal')
    options = parser.parse_args(argv)
    if options.command == 'run-task' and not 10 <= options.timeout_seconds <= 7200:
        parser.error('Task timeout must be between 10 and 7200 seconds')
    try:
        if options.command == 'check-key-store':
            value = secrets.token_bytes(32)
            try:
                matched = dpapi(dpapi(value), decrypt=True) == value
                result = {'status': 'dpapi_available' if matched else 'dpapi_unavailable'}
            except OSError as error:
                result = {'status': 'dpapi_unavailable', 'os_code': error.errno}
        elif options.command == 'init-key':
            if options.key_file.exists():
                raise ValueError('Credential file already exists')
            # Fail rather than falling back to a visible terminal read.
            with warnings.catch_warnings():
                warnings.simplefilter('error', getpass.GetPassWarning)
                token = getpass.getpass('Agent key (hidden): ')
            credential = Credential(options.url, options.agent_id, token,str(options.ca_file) if options.ca_file else None)
            save_credential(options.key_file, credential)
            result = {'status': 'key_saved'}
        else:
            credential = load_credential(options.key_file)
            outbox = Outbox(options.outbox, credential)
            if options.command == 'status':
                result = {'status': 'local', 'pending': outbox.count()}
            else:
                with exclusive_outbox(options.outbox):
                    sender = Sender(Client(credential), outbox)
                    result = {}
                    if options.command in ('run-smoke', 'run-task'):
                        if outbox.count() > outbox.LIMIT - 2:
                            raise ValueError('Outbox needs two free slots')
                        if options.command == 'run-task':
                            if not options.cwd.is_dir():
                                raise ValueError('Task directory does not exist')
                            with warnings.catch_warnings():
                                warnings.simplefilter('error', getpass.GetPassWarning)
                                prompt = getpass.getpass('Task (hidden, single line): ')
                            if not prompt.strip() or len(prompt) > MAX_PROMPT_CHARS:
                                raise ValueError('Invalid prompt length')
                            result['exec'] = run_task(prompt, options.cwd, timeout=options.timeout_seconds,
                                                      event_sink=outbox.enqueue, tick=sender.tick,
                                                      show_answer=options.show_answer)
                        else:
                            result['exec'] = run_smoke(event_sink=outbox.enqueue, tick=sender.tick)
                    delivered = sender.flush()
                    result.update(delivered)
                    result['status'] = ('blocked' if delivered['blocked'] else 'pending' if delivered['pending']
                                        else 'delivered' if delivered['connected'] else 'unavailable')
                    execution = result.get('exec', {})
                    if execution.get('status', 'verified') != 'verified':
                        result['status'] = ('exec_interrupted' if execution.get('parser_outcome') == 'INTERRUPTED'
                                            else 'exec_failed' if execution.get('parser_outcome') == 'ERROR'
                                            else 'exec_not_verified')
    except OutboxBusy:
        result = {'status': 'already_running'}
    except (OSError, ValueError, TypeError, sqlite3.Error, getpass.GetPassWarning, EOFError):
        result = {'status': 'local_setup_or_storage_error'}
    except KeyboardInterrupt:
        result = {'status': 'cancelled'}
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    return 0 if result['status'] in ('key_saved', 'local', 'delivered', 'dpapi_available') else 1


if __name__ == '__main__':
    raise SystemExit(main())
