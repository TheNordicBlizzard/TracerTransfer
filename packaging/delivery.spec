# Build from repo root: uv run pyinstaller --noconfirm packaging/delivery.spec
from pathlib import Path
root = Path(SPECPATH).parent
analysis = Analysis([str(root / 'launch_delivery.py')], pathex=[str(root)],
                    binaries=[], datas=[], hiddenimports=[], hookspath=[],
                    runtime_hooks=[], excludes=['tkinter', 'fastapi', 'uvicorn', 'client', 'server'],
                    noarchive=False)
pyz = PYZ(analysis.pure)
exe = EXE(pyz, analysis.scripts, analysis.binaries, analysis.datas, [],
          name='TracerEntregador', debug=False, bootloader_ignore_signals=False,
          strip=False, upx=False, console=True)
