"""The default smoke must target this version and never the obsolete receiver."""
from pathlib import Path
import tomllib
from tests import smoke_executables as smoke


def test_default_paths_are_current_queued_binaries(monkeypatch):
    seen={}
    def run(**kwargs):seen.update(kwargs);return {'ok':True,'synthetic_contract_only':True}
    monkeypatch.setattr(smoke,'run',run)
    assert smoke.main([])==0
    version=tomllib.loads((smoke.ROOT/'pyproject.toml').read_text(encoding='utf-8'))['project']['version']
    assert seen['client_exe']==smoke.ROOT/'dist'/version/'TracerCliente.exe'
    assert seen['delivery_exe']==smoke.ROOT/'dist'/version/'TracerEntregador.exe'


def test_missing_explicit_binary_fails_before_any_process(tmp_path,monkeypatch):
    import pytest
    monkeypatch.setattr(smoke,'run',lambda **kwargs:pytest.fail('Must reject missing binary first'))
    with pytest.raises(FileNotFoundError):
        smoke.main(['--client-exe',str(tmp_path/'absent.exe')])
