"""Service version is an administrative baseline, never inferred from the helper."""
import os
import pytest
from updates import core, windows

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
