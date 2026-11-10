# Build from repository root: uv run python -m PyInstaller packaging/updater.spec
from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules
root = Path(SPECPATH).parent
analysis = Analysis([str(root / 'launch_updater.py')], pathex=[str(root)],
    binaries=[], datas=[(str(root / 'deploy' / 'windows-delivery' / 'Update-Delivery.ps1'), 'deploy/windows-delivery')],
    hiddenimports=collect_submodules('cryptography') + collect_submodules('httpx'),
    hookspath=[], runtime_hooks=[], excludes=['tkinter', 'fastapi', 'uvicorn', 'client', 'server'], noarchive=False)
pyz = PYZ(analysis.pure)
exe = EXE(pyz, analysis.scripts, analysis.binaries, analysis.datas, [],
    name='TracerAtualizador', debug=False, bootloader_ignore_signals=False,
    strip=False, upx=False, console=True)
