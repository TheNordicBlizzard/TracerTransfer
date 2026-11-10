"""Build and verify the secret-free Windows client test package."""
import hashlib
import json
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
NAMES = ['Configurar-Cliente.ps1', 'Iniciar-Teste.ps1', 'Exportar-Cadastro.ps1', 'Revogar-Cadastro.ps1', 'LEIA-ME.md']


def main():
    raise SystemExit('Pacote JSON legado descontinuado. Execute: uv run python scripts/package_release.py — gera somente o instalador piloto, guia e hashes.')
    files = {'TracerCliente.exe': ROOT / 'dist' / 'TracerCliente.exe'}
    files.update({name: ROOT / 'deploy' / 'client-test' / name for name in NAMES})
    content = {name: path.read_bytes() for name, path in files.items()}
    manifest = {name: hashlib.sha256(data).hexdigest() for name, data in content.items()}
    target = ROOT / 'release' / 'TracerCliente-Teste-Windows.zip'
    target.parent.mkdir(exist_ok=True)
    temporary = target.with_suffix('.zip.tmp')
    with zipfile.ZipFile(temporary, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in content.items():
            archive.writestr(name, data)
        archive.writestr('SHA256.json', json.dumps(manifest, indent=2))
    with zipfile.ZipFile(temporary) as archive:
        assert archive.testzip() is None, 'ZIP CRC failed'
        assert set(archive.namelist()) == set(content) | {'SHA256.json'}
        for name, digest in manifest.items():
            assert hashlib.sha256(archive.read(name)).hexdigest() == digest
        assert archive.read('TracerCliente.exe') == files['TracerCliente.exe'].read_bytes()
    temporary.replace(target)
    print(json.dumps({'path': str(target), 'size_bytes': target.stat().st_size,
                      'sha256': hashlib.sha256(target.read_bytes()).hexdigest(),
                      'files': list(content) + ['SHA256.json'], 'crc': 'PASS', 'hashes': 'PASS',
                      'credentials': 'not included; strict file whitelist'}, indent=2))


if __name__ == '__main__':
    main()
