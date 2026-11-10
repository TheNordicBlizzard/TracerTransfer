from pathlib import Path
ROOT = Path(SPECPATH).parent
analysis = Analysis([str(ROOT / 'launch_client.py')], pathex=[str(ROOT)], binaries=[], datas=[], hiddenimports=['pystray._win32', 'PIL._tkinter_finder'], hookspath=[], hooksconfig={}, runtime_hooks=[], excludes=[], noarchive=False)
pyz = PYZ(analysis.pure)
exe = EXE(pyz, analysis.scripts, analysis.binaries, analysis.datas, [], name='TracerCliente', debug=False, bootloader_ignore_signals=False, strip=False, upx=False, console=False, disable_windowed_traceback=False)
