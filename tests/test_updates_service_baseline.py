"""Service version is an administrative baseline, never inferred from the helper."""
import os
import pytest
from updates import core, windows
from tests.test_updates_core import signed, NOW, BINARY

pytestmark = pytest.mark.skipif(os.name != 'nt', reason='Windows byte-range lock')


def environment(tmp_path, monkeypatch):
    (tmp_path / 'updater').mkdir()
    monkeypatch.setattr(windows, 'service_guard', lambda base: tmp_path)
    return tmp_path / 'updater'


def test_missing_baseline_never_contacts_release_service(tmp_path, monkeypatch):
    private = environment(tmp_path, monkeypatch)
    calls = []
    monkeypatch.setattr(core, 'prepare_update', lambda *args: calls.append(args))
    with pytest.raises(ValueError, match='version missing'):
        windows.apply_service(tmp_path)
    assert calls == []
    assert not (private / 'installed_version').exists()


@pytest.mark.parametrize('version', ['1.2', '01.2.3', '-1.2.3', '1.2.3-beta', 'x.y.z'])
def test_invalid_baseline_never_contacts_release_service(tmp_path, monkeypatch, version):
    private = environment(tmp_path, monkeypatch)
    (private / 'installed_version').write_text(version, encoding='ascii')
    calls = []
    monkeypatch.setattr(core, 'prepare_update', lambda *args: calls.append(args))
    with pytest.raises(core.UpdateRejected):
        windows.apply_service(tmp_path)
    assert calls == []


def test_real_delivery_baseline_is_not_helper_version(tmp_path, monkeypatch):
    private = environment(tmp_path, monkeypatch)
    version = private / 'installed_version'
    version.write_text('0.1.1', encoding='ascii')
    calls = []
    monkeypatch.setattr(core, 'prepare_update', lambda *args: calls.append(args) or None)
    windows.apply_service(tmp_path)
    assert calls[0][0:2] == ('delivery', '0.1.1')
    assert version.read_text(encoding='ascii') == '0.1.1'


def test_runtime_missing_baseline_is_not_running_helper_version(tmp_path):
    from types import SimpleNamespace
    from updates.runtime import DeliveryUpdates
    updates = DeliveryUpdates(SimpleNamespace(data_dir=tmp_path / 'data'))
    with pytest.raises(ValueError, match='version missing'):
        updates._current_version()


@pytest.mark.parametrize('phase', ['applying', 'failed'])
def test_interrupted_service_update_requires_reconciliation(tmp_path, monkeypatch, phase):
    private = environment(tmp_path, monkeypatch)
    (private / 'installed_version').write_text('0.1.3')
    windows.atomic_state(private / 'state.json', {'phase': phase, 'version': '0.1.4'})
    calls = []
    monkeypatch.setattr(core, 'prepare_update', lambda *a: calls.append(a))
    with pytest.raises(ValueError, match='recovery'):
        windows.apply_service(tmp_path)
    assert not calls


def test_delivery_task_failure_never_falls_back_to_localservice(tmp_path, monkeypatch):
    from types import SimpleNamespace
    calls = []
    monkeypatch.setattr(windows.subprocess, 'run', lambda *a, **k: SimpleNamespace(returncode=1))
    monkeypatch.setattr(windows.subprocess, 'Popen', lambda *a, **k: calls.append(a))
    with pytest.raises(PermissionError):
        windows.launch_delivery(None, tmp_path)
    assert not calls


@pytest.mark.parametrize('outcome', ['success', 'script-failed', 'wrong-component', 'wrong-name', 'tampered'])
def test_privileged_service_reverification_and_checkpoint(tmp_path, monkeypatch, signed, outcome):
    from pathlib import Path
    from types import SimpleNamespace
    private = environment(tmp_path, monkeypatch)
    (private / 'installed_version').write_text('0.1.1')
    payload = signed.payload
    if outcome == 'wrong-component':
        payload['component'] = 'server'
        metadata = dict(signed.metadata, name=core.artifact_name('server', '0.1.2'), kind='tar-server')
        payload['artifacts'] = {'server': metadata}
    metadata = next(iter(payload['artifacts'].values()))
    artifact = private / (metadata['name'] if outcome != 'wrong-name' else 'wrong.exe')
    artifact.write_bytes(BINARY if outcome != 'tampered' else b'tampered')
    prepared = core.PreparedUpdate(artifact, '0.1.2', 'delivery', signed.envelope(), metadata)
    monkeypatch.setattr(core, 'prepare_update', lambda *args: prepared)
    original_verify = core.verify_manifest
    verified = []
    def verify(data):
        verified.append(data)
        return original_verify(data, public_key=signed.public, now=NOW)
    monkeypatch.setattr(core, 'verify_manifest', verify)
    calls = []
    def run(arguments, **kwargs):
        calls.append(arguments)
        assert Path(arguments[arguments.index('-BinaryPath') + 1]) == artifact
        assert arguments[arguments.index('-ExpectedBinarySha256') + 1] == metadata['sha256']
        return SimpleNamespace(returncode=int(outcome == 'script-failed'))
    monkeypatch.setattr(windows.subprocess, 'run', run)
    if outcome == 'success':
        windows.apply_service(tmp_path)
        assert (private / 'installed_version').read_text() == '0.1.2'
        assert len(verified) == 2 and len(calls) == 1
    else:
        with pytest.raises((ValueError, RuntimeError, core.UpdateRejected)):
            windows.apply_service(tmp_path)
        assert (private / 'installed_version').read_text() == '0.1.1'
        assert len(calls) == int(outcome == 'script-failed')
