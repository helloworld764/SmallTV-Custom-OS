"""Windows current-user DPAPI credentials. No plaintext fallback."""

import ctypes
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
from urllib.parse import urlsplit
from uuid import UUID


def local_url(value):
    parsed = urlsplit(value)
    if (parsed.scheme != 'http' or parsed.hostname not in ('127.0.0.1', '::1')
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.path not in ('', '/') or not parsed.port or not 1 <= parsed.port <= 65535):
        raise ValueError('Only an explicit loopback HTTP address and port are supported')
    host = '[::1]' if parsed.hostname == '::1' else '127.0.0.1'
    return f'http://{host}:{parsed.port}'


@dataclass(frozen=True)
class Credential:
    base_url: str
    agent_id: str
    token: str = field(repr=False)

    def __post_init__(self):
        object.__setattr__(self, 'base_url', local_url(self.base_url))
        object.__setattr__(self, 'agent_id', str(UUID(self.agent_id)))
        if not re.fullmatch(r'smalltv_agent_[A-Za-z0-9_-]{40,80}', self.token):
            raise ValueError('Invalid agent credential')


def dpapi(data, *, decrypt=False):
    if os.name != 'nt':
        raise OSError('Windows DPAPI is required')
    from ctypes import wintypes
    class Blob(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]
    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    output = Blob()
    library = ctypes.WinDLL('crypt32', use_last_error=True)
    operation = library.CryptUnprotectData if decrypt else library.CryptProtectData
    operation.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                          ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    operation.restype = wintypes.BOOL
    free = ctypes.WinDLL('kernel32', use_last_error=True).LocalFree
    free.argtypes, free.restype = [ctypes.c_void_p], ctypes.c_void_p
    # CRYPTPROTECT_UI_FORBIDDEN; never LOCAL_MACHINE (other local users).
    if not operation(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(output)):
        raise OSError(ctypes.get_last_error(), 'DPAPI operation failed')
    try:
        return ctypes.string_at(output.data, output.size)
    finally:
        free(ctypes.cast(output.data, ctypes.c_void_p))


def save_credential(path, credential):
    path = Path(path)
    if path.suffix != '.dpapi':
        raise ValueError('Credential file must end in .dpapi')
    protected = dpapi(json.dumps({'version': 1, 'base_url': credential.base_url,
                                 'agent_id': credential.agent_id, 'token': credential.token}).encode('utf-8'))
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(protected)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        path.unlink(missing_ok=True)  # Only the new file created above.
        raise


def load_credential(path):
    with Path(path).open('rb') as stream:
        protected = stream.read(16385)
    if not protected or len(protected) > 16384:
        raise ValueError('Invalid credential file')
    try:
        value = json.loads(dpapi(protected, decrypt=True))
        if set(value) != {'version', 'base_url', 'agent_id', 'token'} or value['version'] != 1:
            raise ValueError
        return Credential(value['base_url'], value['agent_id'], value['token'])
    except (ValueError, TypeError, KeyError, RecursionError):
        raise ValueError('Invalid credential file') from None
