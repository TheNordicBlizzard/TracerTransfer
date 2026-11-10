# Build from repo root: uv run pyinstaller --noconfirm packaging/delivery.spec
from pathlib import Path
root = Path(SPECPATH).parent
import runpy
BuildEvidence = runpy.run_path(str(root / 'packaging/build_metadata.py'))['BuildEvidence']
evidence = BuildEvidence(root, 'delivery')
analysis = Analysis([str(root / 'launch_delivery.py')], pathex=[str(root)],
                    binaries=[], datas=[(str(root / 'deploy/windows-delivery/Update-Delivery.ps1'), 'deploy/windows-delivery')], hiddenimports=[], hookspath=[],
                    runtime_hooks=[], excludes=['tkinter', 'fastapi', 'uvicorn', 'client', 'server'],
                    noarchive=False)
pyz = PYZ(analysis.pure)
exe = EXE(pyz, analysis.scripts, analysis.binaries, analysis.datas, [],
          name='TracerEntregador', debug=False, bootloader_ignore_signals=False,
          strip=False, upx=False, console=True)
evidence.finish(Path(exe.name))
