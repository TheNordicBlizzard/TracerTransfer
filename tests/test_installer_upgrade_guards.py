"""Upgrade harness guards: synthetic paths, no Setup/registry/process changes."""
import sys
import pytest

pytest.importorskip('winreg')
from tests import installer_upgrade as upgrade


def test_refuse_uninstaller_outside_owned_product_path(monkeypatch):
    monkeypatch.setattr(upgrade,'product',lambda:{'UninstallString':'C:\\UnrelatedSoftware\\unins000.exe'})
    monkeypatch.setattr(upgrade,'invoke',lambda *args:pytest.fail('Unexpected executable invocation'))
    with pytest.raises(RuntimeError,match='Unexpected uninstaller path'):
        upgrade.uninstall()


def test_existing_user_installation_is_not_repurposed_for_test(monkeypatch):
    monkeypatch.setattr(sys,'argv',['installer_upgrade','--allow-current-user-install'])
    monkeypatch.setattr(upgrade,'product',lambda:{'DisplayVersion':'user-owned'})
    monkeypatch.setattr(upgrade,'setup',lambda *args:pytest.fail('Preexisting installation must remain untouched'))
    with pytest.raises(RuntimeError,match='Existing client installation'):
        upgrade.main()


def test_profile_inventory_is_read_only_and_detects_mutation(tmp_path):
    path=tmp_path/'synthetic-profile';path.mkdir()
    file=path/'config-sentinel.txt';file.write_text('SYNTHETIC NONSECRET SENTINEL')
    before=upgrade.inventory(path)
    assert upgrade.inventory(path)==before
    file.write_text('CHANGED SYNTHETIC SENTINEL')
    assert upgrade.inventory(path)!=before
    assert upgrade.inventory(tmp_path/'absent')=={}
