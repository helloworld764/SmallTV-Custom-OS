"""Explicit single-turn read-only tasks; prompt goes through stdin, never argv."""

from contextlib import contextmanager
import errno
import os
from pathlib import Path
import shutil
import sys

from check_cli import probe_cli
from smoke_exec import check_arguments, observe


MAX_PROMPT_CHARS = 32768


class OutboxBusy(Exception):
    pass


@contextmanager
def exclusive_outbox(path):
    path = Path(str(path) + '.monitor.lock')
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as stream:
        if stream.seek(0, 2) == 0:
            stream.write(b'\0')
            stream.flush()
        stream.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            if error.errno in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                raise OutboxBusy from None
            raise
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == 'nt':
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


def display_answer(text):
    # Do not allow terminal escape sequences or C1 controls from model output.
    safe = ''.join(c for c in text if c in '\n\t' or (ord(c) >= 32 and not 127 <= ord(c) <= 159))
    output = '--- Codex answer ---\n' + safe + '\n--- End answer ---'
    encoding = getattr(sys.stdout, 'encoding', None)
    if encoding:
        # Redirected Windows stdout can use a legacy code page. Preserve readable
        # text and escape unsupported characters without altering console state.
        output = output.encode(encoding, errors='backslashreplace').decode(encoding)
    print(output, flush=True)


def run_task(prompt, cwd, *, timeout=1800, event_sink=None, tick=None, show_answer=False):
    root = Path(cwd).resolve()
    if not root.is_dir() or not isinstance(prompt, str) or not prompt.strip() or len(prompt) > MAX_PROMPT_CHARS:
        raise ValueError('Invalid task input')
    if type(timeout) is not int or not 10 <= timeout <= 7200:
        raise ValueError('Invalid task timeout')
    diagnostic = probe_cli()
    base = {'status': diagnostic['status'], 'cli_version': diagnostic['cli_version'], 'real_run_checked': False}
    if diagnostic['status'] != 'manual_verification_required':
        return base
    executable = shutil.which('codex')
    if executable is None:
        return base | {'status': 'not_found'}
    command = [executable, 'exec', '--ignore-user-config', '--json', '--sandbox', 'read-only',
               '--ephemeral', '--skip-git-repo-check']
    # Ignoring user config also removes windows.sandbox. Native Windows then
    # rejects even read commands before execution. Keep the OS sandbox enabled
    # explicitly; this does not broaden the fixed read-only permission profile.
    if sys.platform == 'win32':
        command.extend(['-c', 'windows.sandbox="elevated"'])
    failure = check_arguments(command, root)
    if failure:
        return base | failure
    result = observe([*command, '-'], root, timeout=timeout, event_sink=event_sink, tick=tick,
                     input_text=prompt, require_answer=False, answer_sink=display_answer if show_answer else None)
    return result | {'cli_version': base['cli_version']}
