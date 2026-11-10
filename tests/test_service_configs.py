"""Static contracts for the receiver service deployment; no local installation."""
from pathlib import Path
import xml.etree.ElementTree as ET
import pytest

ROOT = Path(__file__).resolve().parents[1] / 'deploy' / 'windows-services'
if not ROOT.is_dir():
    pytest.skip('deploy/windows-services not present in repository checkout', allow_module_level=True)


@pytest.mark.parametrize('service', ['TracerTransferServer', 'TracerTransferProxy'])
def test_service_identity_and_recovery(service):
    root = ET.parse(ROOT / (service + '.xml')).getroot()
    assert root.findtext('id') == service
    assert root.findtext('serviceaccount/domain') == 'NT AUTHORITY'
    assert root.findtext('serviceaccount/user') == 'LocalService'
    assert root.find('serviceaccount/password') is None
    assert root.findtext('startmode') == 'Automatic'
    assert root.find('delayedAutoStart') is not None
    assert root.findtext('workingdirectory') == r'C:\ProgramData\TracerTransfer-Pilot'
    assert root.findtext('stoptimeout') == '30sec'
    failures = root.findall('onfailure')
    assert failures and all(f.get('action') == 'restart' for f in failures)
    assert root.find('interactive') is None
    text = ET.tostring(root, encoding='unicode').lower()
    assert 'admin.dpapi' not in text and 'token' not in text
    assert 'cloudflared' not in text and 'localsystem' not in text
    assert root.findtext('logpath').startswith('%BASE%\\')


def test_server_loopback_and_existing_database():
    root = ET.parse(ROOT / 'TracerTransferServer.xml').getroot()
    assert root.findtext('executable') == r'C:\ProgramData\TracerTransfer-Pilot\bin\TracerServidor.exe'
    args = root.findtext('arguments')
    assert '--host 127.0.0.1' in args and '--port 8765' in args
    assert '--data-dir "C:\\ProgramData\\TracerTransfer-Pilot\\data"' in args
    assert '--output-dir "C:\\FreeForm\\Tracer"' in args
    assert '--bootstrap' not in args


def test_proxy_dependency_and_restricted_config():
    root = ET.parse(ROOT / 'TracerTransferProxy.xml').getroot()
    assert root.findtext('depend') == 'TracerTransferServer'
    assert 'Caddyfile.pilot' in root.findtext('arguments')
    assert '--adapter caddyfile' in root.findtext('arguments')


def test_installation_scope_and_backup_contract():
    text = (ROOT / 'Install-Services.ps1').read_text()
    assert 'ValidateOnly' in text and 'migration-state.json' in text
    assert 'CreationDate' in text and 'ExecutablePath' in text
    assert "'sidtype',$id,'unrestricted'" in text
    assert 'LocalSystem' not in text
    assert 'RestoreManual' in text
    assert 'Copy-Item -LiteralPath "$pilot\\data"' in text
    assert "Start-Service TracerTransferServer" in text
    assert "Start-Service TracerTransferProxy" in text


def test_rollback_never_restores_old_database():
    text = (ROOT / 'Uninstall-Services.ps1').read_text()
    assert 'SetSecurityDescriptorSddlForm' in text
    assert 'old backup NOT restored' in text
    assert 'RestoreManual' in text and 'ValidateOnly' in text
    assert 'Restart-Computer' not in text and 'shutdown.exe' not in text
