"""DPAPI CurrentUser identity and durable SQLite delivery journal (no plaintext fallback)."""
import base64
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import sqlite3


class Blob(ctypes.Structure):
    _fields_ = [('cbData', wintypes.DWORD), ('pbData', ctypes.POINTER(ctypes.c_ubyte))]


def _dpapi(data, decrypt=False):
    if os.name != 'nt':
        raise OSError('DPAPI CurrentUser requires Windows')
    buf = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    source, out = Blob(len(data), buf), Blob()
    crypt = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    fn = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    fn.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                   ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    fn.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    if not fn(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(out)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel.LocalFree(out.pbData)


def protect_token(value):
    return base64.b64encode(_dpapi(value.encode('utf-8'))).decode('ascii')


def unprotect_token(value):
    return _dpapi(base64.b64decode(value, validate=True), True).decode('utf-8')


def atomic_json(path, value):
    path = Path(path)
    temp = path.with_suffix('.tmp')
    with temp.open('w', encoding='utf-8') as stream:
        json.dump(value, stream)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, path)


class State:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        # An OS lock prevents two agents, including two EXEs, sharing publication state.
        self.lock = (self.directory / 'agent.lock').open('a+b')
        self.lock.seek(0)
        if os.name == 'nt':
            import msvcrt
            self.lock.write(b'0'); self.lock.flush(); self.lock.seek(0)
            try:
                msvcrt.locking(self.lock.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                self.lock.close()
                raise RuntimeError('Delivery agent already running') from None
        else:
            import fcntl
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.db = sqlite3.connect(self.directory / 'delivery.sqlite3')
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.execute('CREATE TABLE IF NOT EXISTS deliveries (\n            id TEXT PRIMARY KEY, name TEXT NOT NULL, sha256 TEXT NOT NULL,\n            size INTEGER NOT NULL, lease_token TEXT NOT NULL, lease_until TEXT NOT NULL,\n            phase TEXT NOT NULL, result TEXT, error TEXT, stage TEXT,\n            acked INTEGER NOT NULL DEFAULT 0)')
        self.db.commit()

    def get(self, receipt_id):
        row = self.db.execute('SELECT * FROM deliveries WHERE id=?', (receipt_id,)).fetchone()
        return dict(row) if row else None

    def remember(self, item):
        with self.db:
            self.db.execute('INSERT INTO deliveries(id,name,sha256,size,lease_token,lease_until,phase) VALUES (?,?,?,?,?,?,?)',
                            (*(item[k] for k in ('id','name','sha256','size','lease_token','lease_until')), 'leased'))

    def update(self, receipt_id, **fields):
        allowed = {'phase','result','error','stage','acked','lease_token','lease_until'}
        if not fields or not set(fields) <= allowed:
            raise ValueError('Invalid journal fields')
        with self.db:
            self.db.execute('UPDATE deliveries SET ' + ','.join(k+'=?' for k in fields) + ' WHERE id=?',
                            (*fields.values(), receipt_id))

    def pending(self):
        return [dict(r) for r in self.db.execute("SELECT * FROM deliveries WHERE phase='terminal' AND acked=0")]

    def interrupted(self):
        return [dict(r) for r in self.db.execute("SELECT * FROM deliveries WHERE phase!='terminal'")]

    def close(self):
        self.db.close()
        self.lock.close()
