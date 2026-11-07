"""Real HTTP test on loopback only, never contacting the configured Debian backend."""

import json
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
from threading import Thread
import time

import pytest
import uvicorn

from backend.app.config import Settings
from backend.app.database import Database
from backend.app.main import create_app
from backend.app.security import create_account


def test_javascript_clients_integrate_with_real_local_api_and_sqlite(tmp_path):
    node = shutil.which('node')
    if node is None:
        pytest.skip('Node.js 20+ is required for the HTTP integration test')
    major = int(subprocess.check_output([node, '--version'], text=True).strip().lstrip('v').split('.')[0])
    if major < 20:
        pytest.skip('Node.js 20+ is required for the HTTP integration test')
    path = tmp_path / 'integration.sqlite3'
    database = Database(path)
    database.initialize()
    password = secrets.token_urlsafe(24)
    user = create_account(database, 'integration-user', password, 'Integration user')
    foreign = create_account(database, 'foreign-user', password, 'Foreign user')
    with database.transaction() as connection:
        for device_id, owner in [('integration-tv', user), ('foreign-tv', foreign)]:
            connection.execute('INSERT INTO devices (id, owner_id, name) VALUES (?, ?, ?)', (device_id, owner, device_id))
            connection.execute('INSERT INTO device_settings (device_id) VALUES (?)', (device_id,))
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    listener.listen()
    port = listener.getsockname()[1]
    settings = Settings(_env_file=None, db_path=path, cookie_secure=False)
    server = uvicorn.Server(uvicorn.Config(create_app(settings), log_level='error', lifespan='on'))
    thread = Thread(target=server.run, kwargs={'sockets': [listener]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server.started, 'Isolated test HTTP server did not start'
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run([node, 'tests/integration_http.mjs'], cwd=root,
                                input=json.dumps({'base': f'http://127.0.0.1:{port}', 'password': password}),
                                text=True, capture_output=True, timeout=30)
        # Test input is never put in command arguments, files or error output.
        assert result.returncode == 0, 'JS HTTP integration failed: ' + result.stdout + result.stderr
        with database.connection() as connection:
            row = connection.execute("SELECT brightness, theme FROM device_settings WHERE device_id = 'integration-tv'").fetchone()
            assert tuple(row) == (15, 'Dark Blue')
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        listener.close()
        assert not thread.is_alive(), 'Test HTTP server did not stop'
