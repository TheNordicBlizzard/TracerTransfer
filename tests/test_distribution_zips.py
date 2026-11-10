"""Distribution contract tests use inert bytes, never run installers or services."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import zipfile
import warnings

import pytest
from scripts import build_distribution_zips as build


def fixture_tree(root):
    version = '0.1.3'
    installer = root / f'dist/installers/{version}/TracerTransfer-Cliente-{version}-windows-x64-pilot-unsigned.exe'
    installer.parent.mkdir(parents=True)
    installer.write_bytes(b'INERT INSTALLER FIXTURE')
    recipe = root / 'packaging/client.iss'
    recipe.parent.mkdir(); recipe.write_bytes(b'INERT RECIPE')
    record = {'version': version, 'channel': 'pilot-unsigned', 'broad_distribution_approved': False,
              'recipe_sha256': hashlib.sha256(recipe.read_bytes()).hexdigest(),
              'installer': {'path': str(installer), 'sha256': hashlib.sha256(installer.read_bytes()).hexdigest(), 'authenticode': 'NotSigned'}}
    installer.with_suffix('.json').write_text(json.dumps(record))
    paths = ['deploy/client-pilot/LEIA-ME.md', 'dist/0.1.3/TracerEntregador.exe']
    paths += ['deploy/windows-delivery/' + name for name in build.DELIVERY_FILES]
    for name in paths:
        path = root / name; path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'PUBLIC INERT FIXTURE')
    winsw = root / 'explicit-winsw.exe'; winsw.write_bytes(b'INERT WINSW FIXTURE')
    (root / 'deployment.local.json').write_text('PRIVATE MUST NEVER SHIP')
    return winsw


def test_client_zip_is_installer_guide_hash_only(tmp_path):
    fixture_tree(tmp_path)
    path = build.build_client_zip(tmp_path, '0.1.3', tmp_path / 'release')
    with zipfile.ZipFile(path) as archive:
        assert set(archive.namelist()) == {'TracerTransfer-Cliente-0.1.3-windows-x64-pilot-unsigned.exe', 'LEIA-ME.md', 'SHA256.json'}
        assert archive.testzip() is None


def test_delivery_requires_pinned_explicit_winsw(tmp_path):
    winsw = fixture_tree(tmp_path)
    with pytest.raises(ValueError, match='WinSW SHA256'):
        build.build_delivery_zip(tmp_path, '0.1.3', tmp_path / 'release', winsw)
    assert not (tmp_path / 'release').exists()


def test_delivery_layout_preserves_default_install_paths(tmp_path, monkeypatch):
    winsw = fixture_tree(tmp_path)
    monkeypatch.setattr(build, 'WINSW_SHA256', hashlib.sha256(winsw.read_bytes()).hexdigest())
    from scripts import verify_distribution as verify
    monkeypatch.setattr(verify, 'WINSW_SHA256', build.WINSW_SHA256)
    path = build.build_delivery_zip(tmp_path, '0.1.3', tmp_path / 'release', winsw)
    with zipfile.ZipFile(path) as archive:
        expected = {'dist/TracerEntregador.exe', 'deploy/windows-delivery/WinSW.NET4.exe', 'SHA256.json'}
        expected |= {'deploy/windows-delivery/' + name for name in build.DELIVERY_FILES}
        assert set(archive.namelist()) == expected
        assert 'deployment.local.json' not in archive.namelist()
    assert verify.verify_zip(path, inspect_payloads=False)['component'] == 'delivery'


@pytest.mark.parametrize('mutation', ['extra', 'hash', 'duplicate', 'traversal', 'case-collision', 'symlink', 'expansion'])
def test_zip_verifier_rejects_tampering(tmp_path, mutation):
    from scripts import verify_distribution as verify
    import stat
    fixture_tree(tmp_path)
    original = build.build_client_zip(tmp_path, '0.1.3', tmp_path / 'release')
    with zipfile.ZipFile(original) as archive:
        entries = [(entry.filename, archive.read(entry)) for entry in archive.infolist()]
    if mutation == 'extra':
        entries.append(('deployment.local.json', b'PRIVATE'))
    elif mutation == 'hash':
        entries = [(name, b'ALTERED' if name == 'LEIA-ME.md' else data) for name, data in entries]
    elif mutation == 'duplicate':
        entries.append(entries[0])
    elif mutation == 'case-collision':
        entries.append(('leia-me.md', b'COLLISION'))
    elif mutation == 'traversal':
        entries.append(('../private', b'OUTSIDE'))
    elif mutation == 'symlink':
        info = zipfile.ZipInfo('symlink'); info.external_attr = (stat.S_IFLNK | 0o777) << 16
        entries.append((info, b'outside'))
    elif mutation == 'expansion':
        entries.append(('huge.txt', b'0' * 2000000))
    path = tmp_path / 'tampered.zip'
    with warnings.catch_warnings():
        if mutation == 'duplicate':
            warnings.filterwarnings('ignore', message='Duplicate name:', category=UserWarning)
        with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as archive:
            for name, data in entries:
                archive.writestr(name, data)
    with pytest.raises(ValueError):
        verify.verify_zip(path, inspect_payloads=False)


def test_verification_cli_rejects_empty_directory(tmp_path):
    script = Path(build.__file__).with_name('verify_distribution.py')
    result = subprocess.run([sys.executable, str(script), '--directory', str(tmp_path)], capture_output=True, text=True)
    assert result.returncode != 0
    assert 'Exactly two' in result.stderr


def test_build_cli_rejects_unpinned_winsw_before_output(tmp_path):
    winsw = fixture_tree(tmp_path)
    result = subprocess.run([sys.executable, str(Path(build.__file__)), '--version', '0.1.3',
                             '--winsw', str(winsw), '--output', str(tmp_path / 'release')], capture_output=True, text=True)
    assert result.returncode != 0
    assert 'WinSW SHA256' in result.stderr
    assert not (tmp_path / 'release').exists()


def test_auditor_checks_exact_index_not_sanitized_worktree(tmp_path):
    from scripts.audit_publication import audit
    subprocess.run(['git', 'init', str(tmp_path)], check=True, capture_output=True)
    candidate = tmp_path / 'scripts/audit_publication.py'; candidate.parent.mkdir()
    indicator = 'PRIVATE_' + 'INDICATOR_QA'
    candidate.write_text(indicator)
    subprocess.run(['git', '-C', str(tmp_path), 'add', '.'], check=True, capture_output=True)
    candidate.write_text('SANITIZED CURRENT WORKTREE')
    result = audit(tmp_path, private_rules=[indicator], history=False)
    assert result['checked']['index'] == 1
    assert result['findings'] == [{'target': 'index:scripts/audit_publication.py', 'rules': ['private-indicator-1']}]


def test_auditor_inspects_compressed_zip_payload(tmp_path):
    from scripts.audit_publication import inspect_file
    indicator = 'PRIVATE_' + 'INDICATOR_QA'
    path = tmp_path / 'nested.zip'
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('inside.txt', indicator)
    assert inspect_file(path, [indicator]) == ['private-indicator-1']


@pytest.mark.parametrize('archive_type', ['script', 'pyz', 'embedded-zip'])
def test_frozen_python_constants_are_inspected(tmp_path, archive_type):
    from PyInstaller.archive.writers import CArchiveWriter, ZlibArchiveWriter
    from scripts.audit_publication import inspect_file
    indicator = 'PRIVATE_' + 'INDICATOR_QA'
    script = tmp_path / 'script.py'
    script.write_text('value = ' + repr(indicator))
    path = tmp_path / 'frozen.bin'
    if archive_type == 'script':
        entries = [('launch_client', str(script), True, 's')]
    elif archive_type == 'pyz':
        pyz = tmp_path / 'PYZ.pyz'
        ZlibArchiveWriter(str(pyz), [('qa_module', str(script), 'PYMODULE')],
                          {'qa_module': compile(script.read_text(), 'qa_module.py', 'exec')})
        entries = [('PYZ.pyz', str(pyz), False, 'z')]
    else:
        embedded = tmp_path / 'embedded.zip'
        with zipfile.ZipFile(embedded, 'w', zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('configuration.txt', indicator)
        entries = [('embedded.zip', str(embedded), True, 'x')]
    CArchiveWriter(str(path), entries, 'python313.dll')
    assert inspect_file(path, [indicator]) == ['private-indicator-1']


def test_build_evidence_rejects_source_change_during_build(tmp_path):
    import runpy
    module = runpy.run_path(str(Path(build.__file__).parents[1] / 'packaging/build_metadata.py'))
    subprocess.run(['git', 'init', str(tmp_path)], check=True, capture_output=True)
    source = tmp_path / 'launch_client.py'; source.write_text('print("before")')
    evidence = module['BuildEvidence'](tmp_path, 'client')
    source.write_text('print("changed while compiling")')
    executable = tmp_path / 'TracerCliente.exe'; executable.write_bytes(b'INERT')
    with pytest.raises(ValueError, match='changed during'):
        evidence.finish(executable)
    assert not executable.with_suffix('.build.json').exists()


def test_build_evidence_records_binary_and_actual_source_hashes(tmp_path):
    import runpy
    module = runpy.run_path(str(Path(build.__file__).parents[1] / 'packaging/build_metadata.py'))
    subprocess.run(['git', 'init', str(tmp_path)], check=True, capture_output=True)
    source = tmp_path / 'launch_client.py'; source.write_text('print("QA")')
    evidence = module['BuildEvidence'](tmp_path, 'client')
    executable = tmp_path / 'TracerCliente.exe'; executable.write_bytes(b'INERT')
    evidence.finish(executable)
    record = json.loads(executable.with_suffix('.build.json').read_text())
    assert record['binary_sha256'] == hashlib.sha256(b'INERT').hexdigest()
    assert record['sources']['launch_client.py'] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert record['commit'] is None  # unborn synthetic repository, not invented provenance


@pytest.mark.parametrize('component,name', [('client', 'TracerCliente'), ('delivery', 'TracerEntregador')])
def test_specs_emit_matching_receipts_without_running_pyinstaller(tmp_path, component, name):
    import types
    root = Path(build.__file__).parents[1]
    seen = []
    def analysis(*args, **kwargs):
        seen.append(kwargs)
        return types.SimpleNamespace(pure=[], scripts=[], binaries=[], datas=[])
    def exe(*args, **kwargs):
        output = tmp_path / (kwargs['name'] + '.exe')
        output.write_bytes(b'INERT SPEC CONTRACT FIXTURE')
        return types.SimpleNamespace(name=str(output))
    namespace = {'SPECPATH': str(root / 'packaging'), 'Analysis': analysis, 'PYZ': lambda *_: None, 'EXE': exe}
    spec = root / f'packaging/{component}.spec'
    exec(compile(spec.read_text(), str(spec), 'exec'), namespace)
    record = json.loads((tmp_path / (name + '.build.json')).read_text())
    assert record['component'] == component
    assert record['binary_sha256'] == hashlib.sha256(b'INERT SPEC CONTRACT FIXTURE').hexdigest()
    assert record['sources']['configuration.py'] == hashlib.sha256((root / 'configuration.py').read_bytes()).hexdigest()
    if component == 'client':
        assert seen[0]['datas'] == []
    else:
        assert seen[0]['datas'] == [(str(root / 'deploy/windows-delivery/Update-Delivery.ps1'), 'deploy/windows-delivery')]
        assert record['sources']['deploy/windows-delivery/Update-Delivery.ps1'] == hashlib.sha256((root / 'deploy/windows-delivery/Update-Delivery.ps1').read_bytes()).hexdigest()


def test_auditor_handles_python_surrogate_constants():
    from scripts.audit_publication import code_bytes
    assert list(code_bytes(chr(0xD800))) == [chr(0xD800).encode('utf-8', errors='surrogatepass')]


def test_auditor_rules_are_generic_and_do_not_self_exclude():
    from scripts import audit_publication as audit
    source = Path(audit.__file__).read_text()
    assert 'continue\n        full_path' not in source
    assert audit.inspect_bytes(source.encode()) == []
    assert audit.inspect_bytes(('10.' + '23.45.67').encode()) == ['private-ip']
