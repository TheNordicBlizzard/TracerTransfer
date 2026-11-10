"""Smoke visual dos EXEs, sem digitar credenciais ou alterar início automático."""
import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import subprocess
import tempfile
import time
from PIL import ImageGrab
from tests.support import SCRATCH, stop_process

ROOT=Path(__file__).resolve().parents[1]
user32=ctypes.WinDLL('user32',use_last_error=True)
kernel32=ctypes.WinDLL('kernel32',use_last_error=True)
user32.IsWindowVisible.argtypes=[wintypes.HWND]
user32.GetWindowTextW.argtypes=[wintypes.HWND,wintypes.LPWSTR,ctypes.c_int]
user32.GetWindowThreadProcessId.argtypes=[wintypes.HWND,ctypes.POINTER(wintypes.DWORD)]
kernel32.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
kernel32.OpenProcess.restype=wintypes.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes=[wintypes.HANDLE,wintypes.DWORD,wintypes.LPWSTR,ctypes.POINTER(wintypes.DWORD)]
kernel32.CloseHandle.argtypes=[wintypes.HANDLE]
CALLBACK=ctypes.WINFUNCTYPE(wintypes.BOOL,wintypes.HWND,wintypes.LPARAM)
user32.EnumWindows.argtypes=[CALLBACK,wintypes.LPARAM]


def windows_for(exe,title):
    matches=[]
    @CALLBACK
    def visit(hwnd,_):
        text=ctypes.create_unicode_buffer(512)
        user32.GetWindowTextW(hwnd,text,512)
        if text.value != title:
            return True
        pid=wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd,ctypes.byref(pid))
        process=kernel32.OpenProcess(0x1000,False,pid.value)
        if process:
            try:
                path=ctypes.create_unicode_buffer(32768)
                length=wintypes.DWORD(len(path))
                if kernel32.QueryFullProcessImageNameW(process,0,path,ctypes.byref(length)) and Path(path.value).resolve()==exe.resolve():
                    matches.append(hwnd)
            finally:
                kernel32.CloseHandle(process)
        return True
    user32.EnumWindows(visit,0)
    return matches


def probe(name,title,background=False):
    exe=ROOT/'dist'/name
    with tempfile.TemporaryDirectory(prefix='tracer-gui-',dir=SCRATCH) as tmp:
        command=[str(exe),'--data-dir',tmp]
        if background:
            command.append('--background')
        process=subprocess.Popen(command,cwd=ROOT)
        try:
            deadline=time.monotonic()+30
            hwnd=None
            while time.monotonic()<deadline:
                if process.poll() is not None:
                    raise RuntimeError(f'{name} terminou: {process.returncode}')
                matches=windows_for(exe,title)
                if matches:
                    hwnd=matches[0]
                    break
                time.sleep(0.2)
            assert hwnd is not None, f'Janela {title} não apareceu'
            time.sleep(1.5)
            if background:
                assert not user32.IsWindowVisible(hwnd),'Cliente nao ocultou na bandeja'
                print('PASS: EXE cliente --background oculto, processo ativo.')
            else:
                assert user32.IsWindowVisible(hwnd),'Janela nao visivel'
                output=ROOT/'docs'/('client-window.png' if 'Cliente' in name else 'server-window.png')
                ImageGrab.grab(window=hwnd).save(output)
                print(f'PASS: janela EXE {name}; screenshot {output}')
        finally:
            stop_process(process)


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser()
    parser.add_argument('--client-only',action='store_true')
    args=parser.parse_args()
    probe('TracerCliente.exe','TracerTransfer')
    probe('TracerCliente.exe','TracerTransfer',background=True)
    if not args.client_only:
        probe('TracerServidor.exe','TracerTransfer — Servidor')
