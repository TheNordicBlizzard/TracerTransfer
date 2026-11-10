"""Per-data-directory Windows named mutex (released automatically on process death)."""
import ctypes
from ctypes import wintypes
import hashlib
import os
from pathlib import Path


class SingleInstance:
    def __init__(self, data_dir):
        self.handle = None
        if os.name != 'nt':
            raise OSError('TracerTransfer requer Windows.')
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        self.kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        self.kernel.CreateMutexW.restype = wintypes.HANDLE
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel.CloseHandle.restype = wintypes.BOOL
        identity = str(Path(data_dir).resolve()).casefold()
        # Global namespace prevents two sessions sharing one data directory concurrently.
        name = 'Global\\TracerTransfer-' + hashlib.sha256(identity.encode('utf-8')).hexdigest()
        self.handle = self.kernel.CreateMutexW(None, False, name)
        error = ctypes.get_last_error()
        if not self.handle:
            raise ctypes.WinError(error)
        if error == 183:
            self.close()
            raise RuntimeError('TracerTransfer já está em execução para esta pasta de dados.')

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
