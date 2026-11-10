"""Fail-closed distribution verification, including unpacked installer and PYZ bytes."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
import subprocess
import tempfile
import zipfile

try:
    from scripts.build_distribution_zips import DELIVERY_FILES, WINSW_SHA256, WINSW_VERSION, validate_version
except ModuleNotFoundError:
    from build_distribution_zips import DELIVERY_FILES, WINSW_SHA256, WINSW_VERSION, validate_version

MAX_ENTRY = 512 * 1024 * 1024
MAX_TOTAL = 1024 * 1024 * 1024
MAX_ENTRIES = 100


def checked_entries(archive):
    entries = archive.infolist()
    if len(entries) > MAX_ENTRIES:
        raise ValueError('ZIP entry count limit exceeded')
    seen = set(); total = 0
    for item in entries:
        name = item.filename
        parts = PurePosixPath(name).parts
        if (not name or '\\' in name or ':' in name or name.startswith('/') or
                any(p in ('', '.', '..') for p in name.split('/')) or not parts or
                any(p.rstrip(' .') != p for p in parts) or name.casefold() in seen):
            raise ValueError('ZIP unsafe or duplicate name')
        if item.is_dir() or stat.S_IFMT(item.external_attr >> 16) == stat.S_IFLNK:
            raise ValueError('ZIP directories/links forbidden')
        seen.add(name.casefold()); total += item.file_size
        if item.flag_bits & 1 or item.file_size > MAX_ENTRY or total > MAX_TOTAL:
            raise ValueError('ZIP encryption/expansion limit exceeded')
        if item.file_size > max(item.compress_size, 1) * 1000:
            raise ValueError('ZIP compression ratio limit exceeded')
    return entries


def inspect_installer(path, unpacker, scratch, private_rules=None):
    if not unpacker or not scratch:
        raise ValueError('Installer inspection requires --innounp and --scratch')
    try:
        from scripts.audit_publication import inspect_file
    except ModuleNotFoundError:
        from audit_publication import inspect_file
    with tempfile.TemporaryDirectory(dir=scratch, prefix='distribution-inno-') as folder:
        command = [str(Path(unpacker).resolve()), '-x', '-b', '-y', '-m', '-d' + folder, str(path)]
        result = subprocess.run(command, capture_output=True, timeout=120)
        if result.returncode:
            raise ValueError('Installer unpacking failed')
        files = [p for p in Path(folder).rglob('*') if p.is_file()]
        payloads = [p for p in files if p.parts[len(Path(folder).parts)] != 'embedded'
                    and p.relative_to(folder).as_posix() != 'install_script.iss']
        if {p.relative_to(folder).as_posix() for p in payloads} != {'{app}/TracerCliente.exe'}:
            raise ValueError('Public installer payload allowlist failed')
        for file in files:
            findings = inspect_file(file, private_rules=private_rules)
            if findings:
                raise ValueError('Installer contents failed privacy inspection: ' + file.name)


def verify_zip(path, *, inspect_payloads=True, innounp=None, scratch=None, private_rules=None):
    try:
        from scripts.audit_publication import inspect_bytes, inspect_file
    except ModuleNotFoundError:
        from audit_publication import inspect_bytes, inspect_file
    with zipfile.ZipFile(path) as archive:
        entries = checked_entries(archive)
        if archive.testzip() is not None:
            raise ValueError('ZIP CRC failure')
        if 'SHA256.json' not in archive.namelist():
            raise ValueError('ZIP hash manifest missing')
        def unique_object(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError('Duplicate JSON key')
                result[key] = value
            return result
        manifest = json.loads(archive.read('SHA256.json'), object_pairs_hook=unique_object)
        version = validate_version(manifest['version'])
        component = manifest['component']
        if manifest.get('schema') != 1 or manifest.get('channel') != 'pilot-unsigned' or manifest.get('broad_distribution_approved') is not False:
            raise ValueError('Unapproved manifest schema/channel')
        if component == 'client':
            expected = {f'TracerTransfer-Cliente-{version}-windows-x64-pilot-unsigned.exe', 'LEIA-ME.md'}
        elif component == 'delivery':
            expected = {'dist/TracerEntregador.exe', 'deploy/windows-delivery/WinSW.NET4.exe'}
            expected |= {f'deploy/windows-delivery/{name}' for name in DELIVERY_FILES}
            if manifest.get('winsw') != {'version': WINSW_VERSION, 'sha256': WINSW_SHA256}:
                raise ValueError('WinSW provenance mismatch')
        else:
            raise ValueError('Unknown distribution component')
        if set(archive.namelist()) != expected | {'SHA256.json'} or set(manifest['files']) != expected:
            raise ValueError('ZIP payload allowlist mismatch')
        for name in expected:
            data = archive.read(name)
            item = manifest['files'][name]
            if len(data) != item['bytes'] or hashlib.sha256(data).hexdigest() != item['sha256']:
                raise ValueError('ZIP payload hash/size mismatch')
            if name.endswith('/WinSW.NET4.exe') and hashlib.sha256(data).hexdigest() != WINSW_SHA256:
                raise ValueError('WinSW SHA256 mismatch')
            if inspect_bytes(data, private_rules):
                raise ValueError('ZIP privacy inspection failed: ' + name)
            if inspect_payloads and name.endswith('.exe') and not name.endswith('/WinSW.NET4.exe'):
                if not scratch:
                    raise ValueError('Executable inspection requires --scratch')
                Path(scratch).mkdir(parents=True, exist_ok=True)
                with tempfile.TemporaryDirectory(dir=scratch, prefix='distribution-exe-') as folder:
                    executable = Path(folder) / Path(name).name
                    executable.write_bytes(data)
                    if component == 'client':
                        inspect_installer(executable, innounp, scratch, private_rules)
                    elif inspect_file(executable, private_rules=private_rules, require_pyinstaller=True):
                        raise ValueError('Executable privacy inspection failed')
    return {'package': str(path), 'component': component, 'version': version, 'entries': len(entries),
            'payload_inspection': bool(inspect_payloads), 'sha256': hashlib.sha256(Path(path).read_bytes()).hexdigest()}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--innounp', default=os.environ.get('INNOUNP'))
    parser.add_argument('--scratch', default=os.environ.get('TRACER_TEST_TMP') or os.environ.get('TMPDIR'))
    parser.add_argument('--private-rules', type=Path)
    args = parser.parse_args(argv)
    packages = sorted(args.directory.glob('*.zip'))
    if len(packages) != 2:
        parser.error('Exactly two component ZIPs are required')
    rules = json.loads(args.private_rules.read_text(encoding='utf-8')) if args.private_rules else None
    results = [verify_zip(path, innounp=args.innounp, scratch=args.scratch, private_rules=rules) for path in packages]
    if {r['component'] for r in results} != {'client', 'delivery'} or len({r['version'] for r in results}) != 1:
        parser.error('Client and delivery versions must match')
    print(json.dumps({'result': 'PASS', 'packages': results}, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
