"""Validate the public installer without executing Setup or modifying HKCU.

python -m tests.installer_validation --innounp PATH --scratch PATH
An unpacker is required: parsing a recipe alone is not package verification.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import tomllib

ROOT = Path(__file__).resolve().parents[1]


def sha256(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def sections(text):
    result = {}
    current = None
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith('[') and line.endswith(']'):
            current = line[1:-1].casefold()
            result[current] = []
        elif current and line and not line.startswith((';', '#')):
            result[current].append(line)
    return result


def validate_recipe(text):
    parsed = sections(text)
    setup = parsed['setup']
    required = {
        'PrivilegesRequired=lowest', 'DisableDirPage=yes',
        r'DefaultDirName={localappdata}\Programs\TracerTransfer\Client',
        'CloseApplications=no', 'RestartApplications=no',
        'UsePreviousTasks=no', 'SignedUninstaller=yes', 'SignedUninstaller=no',
    }
    if not required.issubset(setup):
        raise ValueError('Missing per-user/preservation/signature directives')
    expected_names = {'TracerCliente.exe', 'TracerAtualizador.exe'}
    actual_names = set()
    import re
    for entry in parsed['files']:
        match = re.search(r'DestName:\s*"([^"]+)"', entry)
        if not match or 'DestDir: "{app}"' not in entry:
            raise ValueError('Only the public client and pinned updater may be bundled')
        actual_names.add(match.group(1))
    if len(parsed['files']) != 2 or actual_names != expected_names:
        raise ValueError('Only the public client and pinned updater may be bundled')
    if '[UninstallDelete]' in text or '[InstallDelete]' in text:
        raise ValueError('Data deletion sections are forbidden')
    if any('Flags: unchecked' not in task for task in parsed['tasks']):
        raise ValueError('Optional tasks must be unchecked by default')
    registry = parsed['registry']
    if len(registry) != 1 or 'Root: HKCU;' not in registry[0] or 'Tasks: startup;' not in registry[0]:
        raise ValueError('Autostart must be one opt-in HKCU value')
    if '--background --data-dir' not in registry[0] or 'StartupValueName' not in registry[0]:
        raise ValueError('Autostart must share the client launcher and value')
    if 'UTF8Encode(Lowercase(' not in text or 'GetSHA256OfString' not in text:
        raise ValueError('Startup value must use the client UTF-8 SHA256 convention')
    if len(parsed['icons']) != 3 or '{uninstallexe}' not in '\n'.join(parsed['icons']):
        raise ValueError('Start menu, optional desktop and uninstall shortcuts required')
    if any('unchecked' not in entry or 'skipifsilent' not in entry for entry in parsed['run']):
        raise ValueError('Postinstall launch must require explicit consent')
    return parsed


def pe_machine(path):
    with Path(path).open('rb') as stream:
        if stream.read(2) != b'MZ':
            raise ValueError('Missing DOS executable header')
        stream.seek(0x3C)
        offset = struct.unpack('<I', stream.read(4))[0]
        stream.seek(offset)
        if stream.read(4) != b'PE\0\0':
            raise ValueError('Missing PE signature')
        return struct.unpack('<H', stream.read(2))[0]


def powershell_parse(path):
    escaped = str(Path(path).resolve()).replace("'", "''")
    expression = (
        "$t=$null; $e=$null; [void][System.Management.Automation.Language.Parser]::"
        f"ParseFile('{escaped}',[ref]$t,[ref]$e); "
        "if($e.Count){$e | Out-String | Write-Output; exit 1}; 'PowerShell parser: OK'"
    )
    result = subprocess.run(['powershell.exe', '-NoProfile', '-Command', expression],
                            capture_output=True, text=True, check=True)
    return result.stdout.strip()


def validate_manifest(manifest, root=ROOT):
    record = json.loads(Path(manifest).read_text(encoding='utf-8'))
    version = tomllib.loads((root / 'pyproject.toml').read_text(encoding='utf-8'))['project']['version']
    if record['schema'] != 1 or record['version'] != version:
        raise ValueError('Manifest schema/version mismatch')
    if record['channel'] != 'pilot-unsigned':
        raise ValueError('This pilot validator expects an explicitly unsigned artifact')
    for kind in ('installer', 'client', 'updater', 'compiler'):
        item = record[kind]
        target = Path(item['path'])
        if sha256(target) != item['sha256']:
            raise ValueError(f'{kind} SHA256 mismatch')
        if 'size' in item and target.stat().st_size != item['size']:
            raise ValueError(f'{kind} size mismatch')
    if record['installer']['authenticode'] != 'NotSigned' or record['client']['authenticode'] != 'NotSigned':
        raise ValueError('Pilot must not claim a signature')
    if record['updater']['authenticode'] != 'NotSigned':
        raise ValueError('Pilot updater must not claim a signature')
    if record['compiler']['authenticode'] != 'Valid':
        raise ValueError('Compiler provenance missing')
    if record['recipe_sha256'] != sha256(root / 'packaging/client.iss'):
        raise ValueError('Recipe changed after compilation')
    for gate in ('clean_windows_validated', 'gui_validated', 'broad_distribution_approved'):
        if record[gate] is not False:
            raise ValueError(f'Unproven gate claimed: {gate}')
    if pe_machine(record['client']['path']) != 0x8664:
        raise ValueError('Payload must be AMD64')
    if pe_machine(record['installer']['path']) not in (0x14C, 0x8664):
        raise ValueError('Unexpected installer architecture')
    sources = [root / 'launch_client.py', root / 'packaging/client.spec', root / 'pyproject.toml']
    sources += [p for folder in ('client', 'common', 'updates') for p in (root / folder).rglob('*.py')]
    if any(p.stat().st_mtime_ns > Path(record['client']['path']).stat().st_mtime_ns for p in sources):
        raise ValueError('Client executable is older than its source')
    return record


def validate_embedded_client(path):
    from PyInstaller.archive.readers import CArchiveReader
    archive = CArchiveReader(str(path))
    if 'launch_client' not in archive.toc:
        raise ValueError('Public client launcher not found')
    pyz = archive.open_embedded_archive('PYZ.pyz')
    modules = ('client.__main__', 'client.activation', 'client.config_ui', 'client.state',
               'client.agent', 'client.status', 'client.instance')
    for module in modules:
        source = ROOT / (module.replace('.', '/') + '.py')
        actual = pyz.extract(module)
        expected = compile(source.read_bytes(), str(source), 'exec', dont_inherit=True, optimize=0)
        if actual != expected:
            raise ValueError(f'Embedded bytecode differs from current source: {module}')
    return list(modules)


def unpack_and_verify(record, unpacker, scratch):
    scratch = Path(scratch).resolve()
    scratch.mkdir(parents=True, exist_ok=True)
    integrity = subprocess.run([str(Path(unpacker).resolve()), '-t', '-b',
                                record['installer']['path']], capture_output=True,
                               text=True, errors='replace', timeout=120)
    if integrity.returncode:
        raise ValueError(f'Package integrity failed ({integrity.returncode}): {integrity.stdout} {integrity.stderr}')
    with tempfile.TemporaryDirectory(prefix='tracer-installer-', dir=scratch) as folder:
        # This executes only the independent unpacker, never Setup/Uninstall/the client.
        args = [str(Path(unpacker).resolve()), '-x', '-b', '-y', '-m', '-d' + folder,
                record['installer']['path']]
        result = subprocess.run(args, capture_output=True, text=True, errors='replace', timeout=120)
        if result.returncode:
            raise ValueError(f'Package extraction failed ({result.returncode}): {result.stdout} {result.stderr}')
        files = [p for p in Path(folder).rglob('*') if p.is_file()]
        payloads = [p for p in files if p.name.casefold() == 'tracercliente.exe']
        if len(payloads) != 1 or sha256(payloads[0]) != record['client']['sha256']:
            raise ValueError('Extracted client count/hash mismatch')
        # Inno internal metadata includes uninstall resources and reconstructed recipe.
        public = [p for p in files if p.parts[len(Path(folder).parts)] != 'embedded'
                  and p.relative_to(folder).as_posix() != 'install_script.iss']
        if {p.relative_to(folder).as_posix() for p in public} != {'{app}/TracerCliente.exe', '{app}/TracerAtualizador.exe'}:
            raise ValueError('Unexpected public payload: ' + repr([str(p) for p in public]))
        updater_payloads = [p for p in public if p.name.casefold() == 'traceratualizador.exe']
        if len(updater_payloads) != 1 or sha256(updater_payloads[0]) != record['updater']['sha256']:
            raise ValueError('Extracted updater count/hash mismatch')
        if pe_machine(updater_payloads[0]) != 0x8664:
            raise ValueError('Updater must be AMD64')
        recovered = sections((Path(folder) / 'install_script.iss').read_text(encoding='utf-8-sig'))
        for directive in ('PrivilegesRequired=lowest', f"AppVersion={record['version']}",
                          r'DefaultDirName={localappdata}\Programs\TracerTransfer\Client'):
            if directive not in recovered['setup']:
                raise ValueError('Compiled setup metadata mismatch: ' + directive)
        if len(recovered['registry']) != 1 or 'Tasks: startup;' not in recovered['registry'][0]:
            raise ValueError('Compiled autostart is not a single opt-in task')
        return {'package_integrity': 'PASS',
                'compiled_setup_metadata': 'PASS',
                'extracted_client_sha256': sha256(payloads[0]),
                'extracted_updater_sha256': sha256(updater_payloads[0]),
                'public_payloads': [p.relative_to(folder).as_posix() for p in public],
                'unpacker_sha256': sha256(unpacker),
                'embedded_modules_match_current_source': validate_embedded_client(payloads[0])}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path)
    parser.add_argument('--innounp', default=os.environ.get('INNOUNP') or shutil.which('innounp.exe'))
    parser.add_argument('--scratch', default=os.environ.get('TRACER_TEST_TMP') or os.environ.get('TMPDIR'))
    args = parser.parse_args(argv)
    if not args.innounp or not args.scratch:
        parser.error('--innounp and --scratch (or INNOUNP and TMPDIR) are required')
    version = tomllib.loads((ROOT / 'pyproject.toml').read_text(encoding='utf-8'))['project']['version']
    manifest = args.manifest or ROOT / f'dist/installers/TracerTransfer-Cliente-{version}-windows-x64-pilot-unsigned.json'
    validate_recipe((ROOT / 'packaging/client.iss').read_text(encoding='utf-8'))
    ps_result = powershell_parse(ROOT / 'scripts/build_installer.ps1')
    record = validate_manifest(manifest)
    package = unpack_and_verify(record, args.innounp, args.scratch)
    print(json.dumps({'result': 'PASS', 'powershell_parser': ps_result,
                      'installer': record['installer'], 'package': package,
                      'not_tested': ['clean Windows install/update/uninstall', 'GUI/tray',
                                     'real login', 'signed release', 'broad distribution']}, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
