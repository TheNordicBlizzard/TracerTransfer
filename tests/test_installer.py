"""Installer contract and real package regressions; never run Setup on the host."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import types

import pytest

from tests.installer_validation import (
    ROOT, pe_machine, powershell_parse, sections, sha256, unpack_and_verify,
    validate_embedded_client, validate_manifest, validate_recipe,
)

RECIPE = ROOT / 'packaging/client.iss'
SCRIPT = ROOT / 'scripts/build_installer.ps1'


@pytest.fixture
def recipe():
    return RECIPE.read_text(encoding='utf-8')


def test_per_user_contract(recipe):
    result = validate_recipe(recipe)
    assert 'AppId={{B2A7D908-2FA3-45B4-AC85-36035DDBE4DD}' in result['setup']
    assert 'ArchitecturesAllowed=x64compatible' in result['setup']


@pytest.mark.parametrize('old,new', [
    ('PrivilegesRequired=lowest', 'PrivilegesRequired=admin'),
    ('CloseApplications=no', 'CloseApplications=yes'),
    ('RestartApplications=no', 'RestartApplications=yes'),
    ('UsePreviousTasks=no', 'UsePreviousTasks=yes'),
    ('Flags: unchecked', 'Flags: checkedonce'),
    ('Root: HKCU;', 'Root: HKLM;'),
    ('Tasks: startup;', ''),
    ('--background --data-dir', '--headless'),
    ('UTF8Encode(Lowercase(', 'UTF8Encode(Uppercase('),
    ('SignedUninstaller=yes', 'SignedUninstaller=maybe'),
    ('skipifsilent unchecked', 'skipifsilent'),
])
def test_reject_contract_regressions(recipe, old, new):
    with pytest.raises(ValueError):
        validate_recipe(recipe.replace(old, new))


@pytest.mark.parametrize('section', ['InstallDelete', 'UninstallDelete'])
def test_reject_data_deletion(recipe, section):
    with pytest.raises(ValueError):
        validate_recipe(recipe + f'\n[{section}]\nType: filesandordirs; Name: "{{localappdata}}\\TracerTransfer"\n')


def test_optional_origin_profile_is_preserved_on_upgrade(recipe):
    entries = sections(recipe)['files']
    assert len(entries) == 2
    profile = next(line for line in entries if 'deployment.json' in line)
    assert 'onlyifdoesntexist' in profile
    assert '#ifdef ClientProfile' in recipe
    assert 'TracerAtualizador.exe' not in recipe


def test_only_public_client_is_bundled(recipe):
    mutated = recipe.replace('[Icons]', 'Source: "server.db"; DestDir: "{app}"\n\n[Icons]')
    with pytest.raises(ValueError, match='Only the public client'):
        validate_recipe(mutated)


def test_data_directory_not_owned_by_setup(recipe):
    # Read-only string references for Run command are allowed, data writes/deletes are not.
    for line in sections(recipe)['files'] + sections(recipe)['icons']:
        assert 'DestDir: "{localappdata}' not in line
    assert 'DelTree(' not in recipe
    assert 'DeleteFile(' not in recipe
    assert 'RegDeleteValue(HKCU' in recipe
    assert 'uninsdeletekey' not in recipe.lower()


def test_startup_matches_real_client_without_touching_hkcu(tmp_path, monkeypatch, recipe):
    from client.config_ui import set_startup
    saved = {}

    class Key:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

    def get_value(key, label):
        if label not in saved:
            raise FileNotFoundError(label)
        return saved[label], 1

    fake = types.SimpleNamespace(
        HKEY_CURRENT_USER=object(), REG_SZ=1,
        CreateKey=lambda *_: Key(),
        SetValueEx=lambda key, label, reserved, kind, command: saved.__setitem__(label, command),
        QueryValueEx=get_value,
        DeleteValue=lambda key, label: saved.pop(label),
    )
    monkeypatch.setitem(sys.modules, 'winreg', fake)
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    data = tmp_path / 'Usuário com espaços' / 'client'
    label = 'TracerTransfer-' + hashlib.sha256(str(data.resolve()).lower().encode('utf-8')).hexdigest()[:16]
    set_startup(data, True)
    assert list(saved) == [label]
    assert '--background --data-dir' in saved[label]
    assert str(data.resolve()) in saved[label]
    set_startup(data, False)
    assert saved == {}
    assert 'Copy(GetSHA256OfString(' in recipe
    assert 'UTF8Encode(Lowercase(' in recipe


@pytest.mark.skipif(not shutil.which('powershell.exe'), reason='Windows PowerShell required')
def test_real_powershell_parser():
    assert powershell_parse(SCRIPT) == 'PowerShell parser: OK'


@pytest.mark.skipif(not shutil.which('powershell.exe'), reason='Windows PowerShell required')
@pytest.mark.parametrize('extra,expected', [
    ([], 'PilotUnsigned'),
    (['-PilotUnsigned', '-SignToolCommand', 'not-a-real-signer'], 'nunca ambos'),
])
def test_build_fails_closed_without_explicit_channel(extra, expected):
    result = subprocess.run(['powershell.exe', '-NoProfile', '-File', str(SCRIPT), *extra],
                            capture_output=True, text=True, errors='replace')
    assert result.returncode != 0
    assert expected in result.stdout + result.stderr


@pytest.mark.skipif(not shutil.which('powershell.exe'), reason='Windows PowerShell required')
def test_reject_stale_client_before_compilation(tmp_path):
    fake = tmp_path / 'TracerCliente.exe'
    fake.write_bytes(b'MZ-not-a-real-payload')
    os.utime(fake, (1, 1))
    result = subprocess.run(['powershell.exe', '-NoProfile', '-File', str(SCRIPT),
                             '-PilotUnsigned', '-ClientExe', str(fake)],
                            capture_output=True, text=True, errors='replace')
    assert result.returncode != 0
    assert 'desatualizado' in result.stdout + result.stderr


def test_pe_parser_rejects_fake_executable(tmp_path):
    fake = tmp_path / 'fake.exe'
    fake.write_bytes(b'not an executable')
    with pytest.raises(ValueError, match='DOS'):
        pe_machine(fake)


@pytest.fixture
def artifact():
    import tomllib
    version = tomllib.loads((ROOT / 'pyproject.toml').read_text(encoding='utf-8'))['project']['version']
    import os
    explicit = os.environ.get('TRACER_INSTALLER_MANIFEST')
    path = Path(explicit) if explicit else ROOT / f'dist/installers/{version}/TracerTransfer-Cliente-{version}-windows-x64-pilot-unsigned.json'
    if not path.is_file():
        if explicit:
            pytest.fail('Explicit installer manifest does not exist: '+str(path))
        pytest.fail('Build the current pilot installer first: ' + str(path))
    return path


def test_real_manifest_and_current_embedded_bytecode(artifact):
    record = validate_manifest(artifact)
    assert len(validate_embedded_client(record['client']['path'])) == 7


@pytest.mark.parametrize('field', ['sha256', 'size'])
def test_detect_manifest_tampering(artifact, tmp_path, field):
    record = json.loads(artifact.read_text(encoding='utf-8'))
    record['installer'][field] = '0' * 64 if field == 'sha256' else 1
    altered = tmp_path / 'altered.json'
    altered.write_text(json.dumps(record), encoding='utf-8')
    with pytest.raises(ValueError, match='mismatch'):
        validate_manifest(altered)


def test_no_unproven_gate_claims(artifact, tmp_path):
    record = json.loads(artifact.read_text(encoding='utf-8'))
    record['clean_windows_validated'] = True
    altered = tmp_path / 'unproven.json'
    altered.write_text(json.dumps(record), encoding='utf-8')
    with pytest.raises(ValueError, match='Unproven'):
        validate_manifest(altered)


def test_real_package_extraction_without_installing(artifact, tmp_path):
    unpacker = os.environ.get('INNOUNP') or shutil.which('innounp.exe')
    if not unpacker:
        pytest.fail('Set INNOUNP for real package verification')
    record = validate_manifest(artifact)
    result = unpack_and_verify(record, unpacker, tmp_path)
    assert result['extracted_client_sha256'] == record['client']['sha256']
    assert set(result['public_payloads']) == {'{app}/TracerCliente.exe'}
    assert 'updater' not in record
