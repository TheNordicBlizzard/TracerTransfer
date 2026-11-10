"""Build clean distribution zip packages for Cliente and Entregador (0.1.2)."""
from __future__ import annotations
import hashlib
import os
from pathlib import Path
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
RELEASE_DIR = ROOT / 'release'
RELEASE_DIR.mkdir(parents=True, exist_ok=True)


def calc_sha256(path: Path) -> str:
    with path.open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def build_client_zip():
    out_zip = RELEASE_DIR / 'TracerTransfer-Cliente-0.1.2-Pacote.zip'
    files_to_pack = [
        (ROOT / 'dist/installers/TracerTransfer-Cliente-0.1.2-windows-x64-pilot-unsigned.exe', 'Instalador/TracerTransfer-Cliente-0.1.2-windows-x64-pilot-unsigned.exe'),
        (ROOT / 'dist/TracerCliente.exe', 'Binarios-Portateis/TracerCliente.exe'),
        (ROOT / 'dist/TracerAtualizador.exe', 'Binarios-Portateis/TracerAtualizador.exe'),
        (ROOT / 'deployment.local.json', 'deployment.local.json'),
        (ROOT / 'deploy/deployment.example.json', 'deployment.example.json'),
        (ROOT / 'deploy/client-pilot/LEIA-ME.md', 'LEIA-ME.md'),
    ]
    sha_lines = []
    for src, arc in files_to_pack:
        if not src.is_file():
            raise FileNotFoundError(f'Missing client package file: {src}')
        h = calc_sha256(src)
        sha_lines.append(f'{h} *{arc}')
    with zipfile.ZipFile(out_zip, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for src, arc in files_to_pack:
            zf.write(src, arc)
        manifest = '\n'.join(sha_lines) + '\n'
        zf.writestr('SHA256.txt', manifest.encode('utf-8'))
    print(f'Cliente package built: {out_zip} ({out_zip.stat().st_size} bytes)')
    return out_zip


def build_delivery_zip():
    out_zip = RELEASE_DIR / 'TracerTransfer-Entregador-0.1.2-Pacote.zip'
    winsw_env = os.environ.get('WINSW_PATH')
    winsw_source = Path(winsw_env) if winsw_env else ROOT / 'dist' / 'WinSW.NET4.exe'
    if not winsw_source.is_file():
        # Tentar localizar no diretório temporário de ferramentas ou emitir aviso
        candidates = list(Path(tempfile.gettempdir()).glob('**/WinSW.NET4.exe'))
        if candidates:
            winsw_source = candidates[0]
        else:
            raise FileNotFoundError(f'WinSW executable not found at: {winsw_source}')
    files_to_pack = [
        (ROOT / 'dist/TracerEntregador.exe', 'TracerEntregador.exe'),
        (ROOT / 'dist/TracerAtualizador.exe', 'TracerAtualizador.exe'),
        (winsw_source, 'WinSW.NET4.exe'),
        (ROOT / 'deploy/windows-delivery/TracerTransferDelivery.xml', 'TracerTransferDelivery.xml'),
        (ROOT / 'deploy/windows-delivery/Install-Delivery.ps1', 'Install-Delivery.ps1'),
        (ROOT / 'deploy/windows-delivery/Update-Delivery.ps1', 'Update-Delivery.ps1'),
        (ROOT / 'deploy/windows-delivery/Enable-AutoUpdate.ps1', 'Enable-AutoUpdate.ps1'),
        (ROOT / 'deploy/windows-delivery/Uninstall-Delivery.ps1', 'Uninstall-Delivery.ps1'),
        (ROOT / 'deploy/windows-delivery/WinSW-LICENSE.txt', 'WinSW-LICENSE.txt'),
        (ROOT / 'deploy/windows-delivery/LEIA-ME.md', 'LEIA-ME.md'),
        (ROOT / 'deployment.local.json', 'deployment.local.json'),
        (ROOT / 'deploy/deployment.example.json', 'deployment.example.json'),
    ]
    sha_lines = []
    for src, arc in files_to_pack:
        if not src.is_file():
            raise FileNotFoundError(f'Missing delivery package file: {src}')
        h = calc_sha256(src)
        sha_lines.append(f'{h} *{arc}')
    with zipfile.ZipFile(out_zip, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for src, arc in files_to_pack:
            zf.write(src, arc)
        manifest = '\n'.join(sha_lines) + '\n'
        zf.writestr('SHA256.txt', manifest.encode('utf-8'))
    print(f'Entregador package built: {out_zip} ({out_zip.stat().st_size} bytes)')
    return out_zip


if __name__ == '__main__':
    c = build_client_zip()
    d = build_delivery_zip()
    print('Both packages built successfully!')
