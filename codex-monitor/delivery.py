"""Loopback-only HTTP delivery with a durable, identity-bound SQLite outbox."""

from contextlib import closing
from http.client import HTTPException
import json
from pathlib import Path
import sqlite3
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener, ProxyHandler, HTTPRedirectHandler
from uuid import UUID


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Client:
    def __init__(self, credential):
        self.credential = credential
        self.opener = build_opener(ProxyHandler({}), NoRedirect())

    def request(self, route, payload=None):
        # Only fixed routes; never allow caller-provided hosts or arbitrary paths.
        if route not in ('identity', 'heartbeat', 'events'):
            raise ValueError('Unsupported agent route')
        data = None if payload is None else json.dumps(payload).encode('utf-8')
        request = Request(self.credential.base_url + '/api/v1/agents/codex/' + route, data=data,
                          headers={'Authorization': 'Bearer ' + self.credential.token,
                                   'Content-Type': 'application/json'}, method='GET' if payload is None else 'POST')
        try:
            with self.opener.open(request, timeout=2.5) as response:
                raw = response.read(8193)
                if len(raw) > 8192:
                    return 0, None, 0
                return response.status, json.loads(raw) if raw else None, 0
        except HTTPError as error:
            try:
                retry = int(error.headers.get('Retry-After', '0'))
            except (ValueError, TypeError):
                retry = 0
            status = error.code
            error.close()  # Do not read/log server error bodies or echo credentials.
            return status, None, max(0, min(retry, 300))
        except (URLError, OSError, TimeoutError, HTTPException):
            return -1, None, 0
        except (ValueError, UnicodeError, RecursionError):
            return 0, None, 0


class Outbox:
    LIMIT = 1000

    def __init__(self, path, credential):
        self.path = Path(path)
        if self.path.suffix not in ('.sqlite3', '.sqlite', '.db'):
            raise ValueError('Outbox must use an ignored SQLite file extension')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as connection, connection:
            connection.execute('BEGIN IMMEDIATE')
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if tables and tables != {'binding', 'outbox'}:
                raise ValueError('Not a monitor outbox')
            connection.execute('CREATE TABLE IF NOT EXISTS binding (version INTEGER, base_url TEXT, agent_id TEXT)')
            connection.execute('''CREATE TABLE IF NOT EXISTS outbox (
                ordinal INTEGER PRIMARY KEY, event_id TEXT NOT NULL UNIQUE, payload TEXT NOT NULL)''')
            expected = (1, credential.base_url, credential.agent_id)
            binding = connection.execute('SELECT * FROM binding').fetchall()
            if tables and not binding:
                raise ValueError('Outbox has no identity binding')
            if binding and binding != [expected]:
                raise ValueError('Outbox belongs to a different server or agent')
            if not binding:
                connection.execute('INSERT INTO binding VALUES (?, ?, ?)', expected)

    def connect(self):
        connection = sqlite3.connect(self.path, timeout=2.5)
        connection.execute('PRAGMA synchronous=FULL')
        return connection

    def enqueue(self, event):
        if (set(event) != {'event_id', 'run_id', 'sequence', 'kind', 'occurred_at'}
                or event['kind'] not in ('START', 'WORKING', 'DONE', 'ERROR', 'INTERRUPTED')
                or type(event['sequence']) is not int or not 1 <= event['sequence'] <= 10000
                or type(event['occurred_at']) is not int or not 0 <= event['occurred_at'] <= 253402300799):
            raise ValueError('Invalid event')
        value = dict(event, event_id=str(UUID(event['event_id'])), run_id=str(UUID(event['run_id'])))
        encoded = json.dumps(value, sort_keys=True)
        with closing(self.connect()) as connection, connection:
            connection.execute('BEGIN IMMEDIATE')
            old = connection.execute('SELECT payload FROM outbox WHERE event_id=?', (value['event_id'],)).fetchone()
            if old:
                if old[0] != encoded:
                    raise ValueError('Event ID conflicts with queued content')
                return
            if connection.execute('SELECT COUNT(*) FROM outbox').fetchone()[0] >= self.LIMIT:
                raise ValueError('Outbox capacity reached')
            connection.execute('INSERT INTO outbox(event_id,payload) VALUES (?,?)', (value['event_id'], encoded))

    def head(self):
        with closing(self.connect()) as connection:
            row = connection.execute('SELECT payload FROM outbox ORDER BY ordinal LIMIT 1').fetchone()
            return json.loads(row[0]) if row else None

    def acknowledge(self, event_id):
        with closing(self.connect()) as connection, connection:
            connection.execute('DELETE FROM outbox WHERE event_id=?', (event_id,))

    def count(self):
        with closing(self.connect()) as connection:
            return connection.execute('SELECT COUNT(*) FROM outbox').fetchone()[0]


class Sender:
    def __init__(self, client, outbox, clock=time.monotonic):
        self.client, self.outbox, self.clock = client, outbox, clock
        self.authenticated = False
        self.heartbeat_confirmed = False
        self.blocked = None
        self.due = dict.fromkeys(('identity', 'heartbeat', 'events'), 0)
        self.failures = dict.fromkeys(self.due, 0)

    def defer(self, route, status, retry):
        if status == 429 and retry <= 0:
            self.blocked = 'quota'
            return
        if status == -1 or status == 408 or status == 429 or status >= 500:
            self.failures[route] = min(self.failures[route] + 1, 6)
            self.due[route] = self.clock() + max(retry, min(2 ** (self.failures[route] - 1), 30))
        else:
            self.blocked = 'authorization' if status in (401, 403) else 'response_or_conflict'

    def tick(self):
        if self.blocked:
            return
        now = self.clock()
        if not self.authenticated:
            if now < self.due['identity']:
                return
            status, body, retry = self.client.request('identity')
            if status != 200:
                self.defer('identity', status, retry)
                return
            if body != {'agent_id': self.client.credential.agent_id, 'kind': 'codex_windows'}:
                self.blocked = 'identity_mismatch'
                return
            self.authenticated = True
        if now >= self.due['heartbeat']:
            status, body, retry = self.client.request('heartbeat', {})
            if status != 204 or body is not None:
                self.heartbeat_confirmed = False
                self.defer('heartbeat', status if status != 204 else 0, retry)
                if self.blocked:
                    return
            else:
                self.heartbeat_confirmed = True
                self.failures['heartbeat'] = 0
                self.due['heartbeat'] = self.clock() + 10
        if now < self.due['events']:
            return
        event = self.outbox.head()
        if event is None:
            return
        status, body, retry = self.client.request('events', event)
        expected = {key: event[key] for key in ('event_id', 'run_id', 'sequence')}
        expected['duplicate'] = status == 200
        if (status in (200, 201) and body == expected and type(body.get('duplicate')) is bool
                and type(body.get('sequence')) is int):
            self.outbox.acknowledge(event['event_id'])
            self.failures['events'] = 0
            self.due['events'] = 0
        else:
            self.defer('events', 0 if status in (200, 201) else status, retry)

    def flush(self, timeout=15):
        deadline = self.clock() + timeout
        self.tick()
        while (self.outbox.count() or not self.heartbeat_confirmed) and not self.blocked and self.clock() < deadline:
            self.tick()
            time.sleep(0.1)
        return {'pending': self.outbox.count(), 'blocked': self.blocked,
                'connected': self.authenticated and self.heartbeat_confirmed and not bool(self.blocked)}
