"""Windows current-user DPAPI credentials. No plaintext fallback."""

import ctypes
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
import ipaddress
from urllib.parse import urlsplit
from uuid import UUID


def local_url(value):
    """Legacy function name retained; remote origins require verified HTTPS."""
    if not isinstance(value,str) or any(ord(ch)<=32 for ch in value) or '\\' in value:raise ValueError('Invalid server origin')
    parsed = urlsplit(value)
    if (parsed.scheme not in ('http','https') or not parsed.hostname
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.path not in ('', '/')):raise ValueError('Only a server origin is supported')
    hostname=parsed.hostname.encode('idna').decode('ascii').lower()
    if '%' in hostname:raise ValueError('Invalid server host')
    try:
        address=ipaddress.ip_address(hostname)
    except ValueError:
        if ':' in hostname or not re.fullmatch(r'(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)*[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?',hostname):raise ValueError('Invalid server host') from None
        host=hostname
    else:
        if address.is_unspecified or address.is_multicast:raise ValueError('Invalid server host')
        host='['+address.compressed+']' if address.version==6 else address.compressed
    port=parsed.port or (443 if parsed.scheme=='https' else None)
    if port is None or not 1<=port<=65535 or parsed.port==0:raise ValueError('Invalid server port')
    if parsed.scheme=='http' and hostname not in ('127.0.0.1','::1'):raise ValueError('Remote servers require HTTPS')
    return f'{parsed.scheme}://{host}:{port}'


@dataclass(frozen=True)
class Credential:
    base_url: str
    agent_id: str
    token: str = field(repr=False)
    ca_file: str | None = None

    def __post_init__(self):
        object.__setattr__(self, 'base_url', local_url(self.base_url))
        object.__setattr__(self, 'agent_id', str(UUID(self.agent_id)))
        if not re.fullmatch(r'smalltv_agent_[A-Za-z0-9_-]{40,80}', self.token):
            raise ValueError('Invalid agent credential')
        if self.ca_file is not None:
            if not self.base_url.startswith('https://'):raise ValueError('A CA file requires HTTPS')
            path=Path(self.ca_file).resolve(strict=True)
            if not path.is_file():raise ValueError('Invalid CA file')
            object.__setattr__(self,'ca_file',str(path))


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
    value={'version':1,'base_url':credential.base_url,'agent_id':credential.agent_id,'token':credential.token}
    if credential.ca_file is not None:value['ca_file']=credential.ca_file
    protected = dpapi(json.dumps(value).encode('utf-8'))
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
        if not {'version','base_url','agent_id','token'}<=set(value) or set(value)-{'version','base_url','agent_id','token','ca_file'} or value['version'] != 1:
            raise ValueError
        return Credential(value['base_url'], value['agent_id'], value['token'],value.get('ca_file'))
    except (ValueError, TypeError, KeyError, RecursionError):
        raise ValueError('Invalid credential file') from None
