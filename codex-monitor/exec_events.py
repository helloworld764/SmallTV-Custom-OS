"""Offline, single-turn JSONL normalizer. No process, file or network access."""

from dataclasses import asdict, dataclass
import json
import time
from uuid import uuid4


# Command output is embedded in one JSONL event, including multi-file reads.
# The process observer separately caps the entire byte stream at 1 MiB.
MAX_LINE_CHARS = 262144


@dataclass(frozen=True)
class StormEvent:
    event_id: str
    run_id: str
    sequence: int
    kind: str
    occurred_at: int

    def to_dict(self):
        return asdict(self)


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON key')
        result[key] = value
    return result


def reject_constant(value):
    raise ValueError('Non-finite JSON number')


class ExecEvents:
    """Conservative prototype for one owned exec process and one turn.

    feed() emits only START. finish() may emit one terminal report after both
    a terminal JSON event and a consistent process exit have been observed.
    Input contents are discarded; only fixed reason codes are retained.
    """

    def __init__(self, clock=None):
        self.run_id = str(uuid4())
        self.outcome = 'UNKNOWN'
        self.reason = None
        self._clock = clock if clock is not None else lambda: int(time.time())
        self._sequence = 0
        self._thread_seen = False
        self._started = False
        self._pending = None
        self._invalid = False
        self._finished = False

    def _invalidate(self, reason):
        self._invalid = True
        self.reason = self.reason or reason

    def _event(self, kind):
        timestamp = self._clock()
        if type(timestamp) is not int or not 0 <= timestamp <= 253402300799:
            self._invalidate('invalid_clock')
            return []
        self._sequence += 1
        return [StormEvent(str(uuid4()), self.run_id, self._sequence, kind, timestamp)]

    def feed(self, line):
        if self._finished:
            raise RuntimeError('Parser already finalized')
        if self._invalid:
            return []
        if not isinstance(line, str):
            self._invalidate('invalid_line')
            return []
        if len(line) > MAX_LINE_CHARS:
            self._invalidate('line_limit')
            return []
        if not line.strip():
            return []
        try:
            message = json.loads(line, object_pairs_hook=unique_object, parse_constant=reject_constant)
        except (ValueError, RecursionError):
            self._invalidate('invalid_json')
            return []
        if not isinstance(message, dict) or not isinstance(message.get('type'), str):
            self._invalidate('invalid_envelope')
            return []
        kind = message['type']
        if kind == 'thread.started' and not self._thread_seen and not self._started:
            thread_id = message.get('thread_id')
            if isinstance(thread_id, str) and 0 < len(thread_id) <= 128:
                self._thread_seen = True
                return []
        elif kind == 'turn.started' and not self._started and self._pending is None:
            self._started = True
            return self._event('START')
        elif kind in ('item.started', 'item.updated', 'item.completed') and self._started and self._pending is None:
            item = message.get('item')
            if isinstance(item, dict) and isinstance(item.get('type'), str) and isinstance(item.get('id'), str):
                return []  # Never copy text, commands, outputs, paths or usage into reports.
        elif kind == 'turn.completed' and self._started and self._pending is None:
            usage = message.get('usage')
            if isinstance(usage, dict) and all(type(usage.get(key)) is int and usage[key] >= 0
                                              for key in ('input_tokens', 'cached_input_tokens', 'output_tokens')):
                self._pending = 'DONE'
                return []
        elif kind == 'turn.failed' and self._started and self._pending is None:
            error = message.get('error')
            if isinstance(error, dict) and isinstance(error.get('message'), str):
                self._pending = 'ERROR'
                return []
        elif kind == 'error':
            self._invalidate('stream_error')
            return []  # Generic errors may be transient; do not fabricate a run result.
        self._invalidate('unsupported_or_unexpected_event')
        return []

    def finish(self, exit_code, *, cancelled_by_user=False):
        if self._finished:
            return []
        self._finished = True
        if self._invalid or not self._started:
            return []
        if type(cancelled_by_user) is not bool or (exit_code is not None and type(exit_code) is not int):
            self._invalidate('invalid_exit_observation')
            return []
        if cancelled_by_user and self._pending is None:
            terminal = 'INTERRUPTED'  # Caller must have positively observed user cancellation.
        elif not cancelled_by_user and self._pending == 'DONE' and exit_code == 0:
            terminal = 'DONE'
        elif not cancelled_by_user and self._pending == 'ERROR' and exit_code is not None and exit_code != 0:
            terminal = 'ERROR'
        else:
            self._invalidate('missing_or_inconsistent_completion')
            return []
        reports = self._event(terminal)
        if reports:
            self.outcome = terminal
        return reports
