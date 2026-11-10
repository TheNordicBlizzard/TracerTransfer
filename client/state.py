"""Public configuration API: save_config(data_dir, Config(...)); read_config(data_dir).

protected_token is DPAPI CurrentUser base64, produced by protect_token(token).
No password or session is persisted. Configuration is not an OS security boundary.
"""
import base64
import ctypes
from ctypes import wintypes
from dataclasses import dataclass, asdict, field
import json
import math
import os
import tempfile
from pathlib import Path
from urllib.parse import urlsplit
from configuration import get_default_server_url

COMPANY_SERVER = get_default_server_url('https://tracer.example.com')


def file_signature(path):
    st = path.stat()
    return f'{st.st_size}:{st.st_mtime_ns}:{st.st_ctime_ns}:{st.st_ino}'


def baseline_folder(folder):
    if not folder:
        return {}
    root = Path(folder)
    if not root.is_dir() or root.is_symlink() or root.is_junction():
        raise ValueError('Escolha uma pasta local existente, sem redirecionamento.')
    return {str(p.resolve()): file_signature(p) for p in root.iterdir()
            if p.suffix.lower() in ('.oma', '.vca') and not p.is_symlink()
            and not p.is_junction() and p.is_file()}


@dataclass
class Config:
    server_url: str = ''
    source_dir: str = ''
    device_id: str = ''
    protected_token: str = ''
    ca_file: str | None = None
    poll_seconds: float = 5.0
    stable_seconds: float = 5.0
    paused: bool = False
    delivery_mode: str = 'queued'
    installation_id: str = ''
    activation_id: str = ''
    role: str = ''
    activation_baseline: dict = field(default_factory=dict)
    startup_enabled: bool = False

    @property
    def activated(self):
        return bool(self.activation_id and self.device_id and self.installation_id
                    and self.protected_token and self.role == 'sender')

    def validate(self):
        if self.delivery_mode not in ('queued', 'direct'):
            raise ValueError('Modo de entrega inválido.')
        if self.server_url:
            u = urlsplit(self.server_url)
            if u.scheme != 'https' or not u.hostname or u.username or u.password or u.query or u.fragment or u.path not in ('', '/'):
                raise ValueError('O servidor deve ser uma origem HTTPS sem credenciais, caminho ou query.')
        if not math.isfinite(self.poll_seconds) or not math.isfinite(self.stable_seconds) or self.poll_seconds < 0.1 or self.stable_seconds < 0:
            raise ValueError('Intervalos inválidos.')
        if not isinstance(self.paused, bool):
            raise ValueError('paused deve ser booleano.')
        return self


def default_data_dir():
    return Path(os.environ.get('LOCALAPPDATA', Path.home())) / 'TracerTransfer' / 'client'


def read_config(data_dir):
    p = Path(data_dir) / 'config.json'
    if not p.exists():
        return Config()
    return Config(**json.loads(p.read_text(encoding='utf-8'))).validate()


def save_config(data_dir, config):
    config.validate()
    p = Path(data_dir)
    p.mkdir(parents=True, exist_ok=True)
    target = p / 'config.json'
    fd, filename = tempfile.mkstemp(prefix='config-', suffix='.tmp', dir=p)
    tmp = Path(filename)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            stream.write(json.dumps(asdict(config), indent=2))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, target)
    finally:
        tmp.unlink(missing_ok=True)


class Blob(ctypes.Structure):
    _fields_ = [('cbData', wintypes.DWORD), ('pbData', ctypes.POINTER(ctypes.c_ubyte))]


def _dpapi(data, decrypt=False):
    if os.name != 'nt':
        raise OSError('DPAPI CurrentUser requer Windows; não há fallback plaintext.')
    buf = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    source = Blob(len(data), buf)
    out = Blob()
    crypt = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    fn = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    fn.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    fn.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    # CRYPTPROTECT_UI_FORBIDDEN, deliberately not LOCAL_MACHINE.
    if not fn(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(out)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel.LocalFree(out.pbData)


def protect_token(token):
    return base64.b64encode(_dpapi(token.encode('utf-8'))).decode('ascii')


def unprotect_token(value):
    return _dpapi(base64.b64decode(value, validate=True), decrypt=True).decode('utf-8')
