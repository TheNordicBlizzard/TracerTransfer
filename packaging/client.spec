# Generic executable: no deployment.local.json or administrative build profile.
# Optional minimal HTTPS origin is installed separately by client.iss.
from pathlib import Path
ROOT = Path(SPECPATH).parent
import runpy
BuildEvidence = runpy.run_path(str(ROOT / 'packaging/build_metadata.py'))['BuildEvidence']
evidence = BuildEvidence(ROOT, 'client')
analysis = Analysis([str(ROOT / 'launch_client.py')], pathex=[str(ROOT)], binaries=[], datas=[], hiddenimports=['pystray._win32', 'PIL._tkinter_finder'], hookspath=[], hooksconfig={}, runtime_hooks=[], excludes=[], noarchive=False)
pyz = PYZ(analysis.pure)
exe = EXE(pyz, analysis.scripts, analysis.binaries, analysis.datas, [], name='TracerCliente', debug=False, bootloader_ignore_signals=False, strip=False, upx=False, console=False, disable_windowed_traceback=False)
evidence.finish(Path(exe.name))
