"""Build allowlisted distributions without implicit tools or local profiles."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import re
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
WINSW_SHA256 = '923111c7142b3dc783a3c722b19b8a21bcb78222d7a136ac33f0ca8a29f4cb66'
WINSW_VERSION = '2.12.0'
DELIVERY_FILES = ('TracerTransferDelivery.xml', 'Install-Delivery.ps1', 'Update-Delivery.ps1',
                  'Enable-AutoUpdate.ps1', 'Uninstall-Delivery.ps1', 'WinSW-LICENSE.txt', 'LEIA-ME.md')


def calc_sha256(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def validate_version(version):
    if not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', version):
        raise ValueError('Exact three-part version required')
    return version


def installer_payload(root, version):
    stem = f'TracerTransfer-Cliente-{validate_version(version)}-windows-x64-pilot-unsigned'
    installer = root / 'dist/installers' / version / (stem + '.exe')
    record = json.loads(installer.with_suffix('.json').read_text(encoding='utf-8'))
    if record['version'] != version or record['channel'] != 'pilot-unsigned' or record['broad_distribution_approved'] is not False:
        raise ValueError('Pilot provenance mismatch')
    if installer.resolve() != Path(record['installer']['path']).resolve() or calc_sha256(installer) != record['installer']['sha256']:
        raise ValueError('Installer differs from verified build')
    if record['installer']['authenticode'] != 'NotSigned':
        raise ValueError('Use separately authorized signed-release workflow')
    if calc_sha256(root / 'packaging/client.iss') != record['recipe_sha256']:
        raise ValueError('Recipe changed since verified build')
    if 'profile' in record:
        raise ValueError('Installer contains a private profile; public distribution forbidden')
    if 'updater' in record:
        raise ValueError('Obsolete fourth executable in installer provenance')
    return installer


def write_package(output, version, component, payloads):
    data = {}
    for name, path in payloads.items():
        if path.is_symlink() or not path.is_file():
            raise ValueError(f'Missing or linked payload: {name}')
        data[name] = path.read_bytes()
    manifest = {'schema': 1, 'version': version, 'component': component, 'channel': 'pilot-unsigned',
                'broad_distribution_approved': False,
                'files': {name: {'sha256': hashlib.sha256(value).hexdigest(), 'bytes': len(value)}
                          for name, value in data.items()}}
    if component == 'delivery':
        manifest['winsw'] = {'version': WINSW_VERSION, 'sha256': WINSW_SHA256}
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    path = output / f'TracerTransfer-{"Cliente" if component == "client" else "Entregador"}-{version}-Pacote.zip'
    with tempfile.NamedTemporaryFile(dir=output, suffix='.zip', delete=False) as stream:
        temporary = Path(stream.name)
    try:
        with zipfile.ZipFile(temporary, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            for name, value in data.items():
                archive.writestr(name, value)
            archive.writestr('SHA256.json', json.dumps(manifest, indent=2).encode('utf-8'))
        try:
            from scripts.verify_distribution import verify_zip
        except ModuleNotFoundError:
            from verify_distribution import verify_zip
        verify_zip(temporary, inspect_payloads=False)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return path


def build_client_zip(root=ROOT, version='0.1.3', output=None):
    root = Path(root)
    installer = installer_payload(root, version)
    return write_package(output or root / 'release' / version, version, 'client', {
        installer.name: installer, 'LEIA-ME.md': root / 'deploy/client-pilot/LEIA-ME.md'})


def build_delivery_zip(root=ROOT, version='0.1.3', output=None, winsw=None):
    validate_version(version)
    if winsw is None:
        raise ValueError('Explicit WinSW path required')
    winsw = Path(winsw)
    if winsw.is_symlink() or calc_sha256(winsw) != WINSW_SHA256:
        raise ValueError('WinSW SHA256 differs from pinned official version')
    root = Path(root)
    payloads = {'dist/TracerEntregador.exe': root / 'dist' / version / 'TracerEntregador.exe',
                'deploy/windows-delivery/WinSW.NET4.exe': winsw}
    payloads.update({f'deploy/windows-delivery/{name}': root / 'deploy/windows-delivery' / name for name in DELIVERY_FILES})
    return write_package(output or root / 'release' / version, version, 'delivery', payloads)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', required=True, type=validate_version)
    parser.add_argument('--winsw', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args(argv)
    if calc_sha256(args.winsw) != WINSW_SHA256:
        parser.error('WinSW SHA256 differs from pinned official version')
    for path in (build_client_zip(ROOT, args.version, args.output),
                 build_delivery_zip(ROOT, args.version, args.output, args.winsw)):
        print(json.dumps({'package': str(path), 'sha256': calc_sha256(path), 'bytes': path.stat().st_size}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
