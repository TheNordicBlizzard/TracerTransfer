"""Synthetic packaging fixtures only; no executable is launched by these tests."""
import hashlib
import json
import zipfile

import pytest
from scripts.package_release import package_client


def setup_package(tmp_path):
    (tmp_path/'pyproject.toml').write_text('[project]\nversion="0.1.0"\n')
    (tmp_path/'packaging').mkdir()
    recipe=tmp_path/'packaging/client.iss';recipe.write_bytes(b'SYNTHETIC RECIPE FIXTURE')
    folder=tmp_path/'dist/installers';folder.mkdir(parents=True)
    artifact=folder/'TracerTransfer-Cliente-0.1.0-windows-x64-pilot-unsigned.exe'
    artifact.write_bytes(b'SYNTHETIC PACKAGE FIXTURE - NEVER EXECUTE')
    record={'version':'0.1.0','channel':'pilot-unsigned','broad_distribution_approved':False,
            'recipe_sha256':hashlib.sha256(recipe.read_bytes()).hexdigest(),
            'installer':{'path':str(artifact),'sha256':hashlib.sha256(artifact.read_bytes()).hexdigest(),'authenticode':'NotSigned'}}
    artifact.with_suffix('.json').write_text(json.dumps(record))
    guide=tmp_path/'deploy/client-pilot/LEIA-ME.md';guide.parent.mkdir(parents=True);guide.write_text('Synthetic pilot guide')
    (tmp_path/'secret.sqlite3').write_bytes(b'PRIVATE SENTINEL - MUST NOT SHIP')
    return artifact,record


def test_public_zip_has_exact_client_only_whitelist(tmp_path):
    artifact,_=setup_package(tmp_path)
    result=package_client(tmp_path)
    with zipfile.ZipFile(result['package']) as archive:
        assert set(archive.namelist())=={artifact.name,'LEIA-ME.md','SHA256.json'}
        assert archive.testzip() is None
        assert archive.read(artifact.name)==artifact.read_bytes()
        assert json.loads(archive.read('SHA256.json'))['broad_distribution_approved'] is False


def test_modified_installer_rejected_before_package_write(tmp_path):
    artifact,_=setup_package(tmp_path)
    artifact.write_bytes(b'MODIFIED SYNTHETIC FIXTURE')
    with pytest.raises(ValueError,match='differs'):
        package_client(tmp_path)
    assert not (tmp_path/'release').exists()


def test_broad_release_flag_cannot_silently_override_pilot_channel(tmp_path):
    artifact,record=setup_package(tmp_path)
    record['broad_distribution_approved']=True
    artifact.with_suffix('.json').write_text(json.dumps(record))
    with pytest.raises(ValueError,match='provenance'):
        package_client(tmp_path)
