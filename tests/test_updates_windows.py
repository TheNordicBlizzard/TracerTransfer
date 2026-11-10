"""Windows kernel/filesystem tests plus controlled updater application fixtures.

The application fixtures substitute only release authorization and installed paths;
real filesystem copies, locks, process handles and subprocess exit codes are used.
They do NOT claim an Inno installation, SCM rollout, or clean-Windows validation.
No signing private keys are created by this module.
"""
import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from updates import core
from updates import windows as win
from updates.runtime import installed_client, ClientUpdates, installed_delivery, DeliveryUpdates

pytestmark = pytest.mark.skipif(os.name != 'nt', reason='Windows handles/locking/filesystem required')
ROOT = Path(__file__).resolve().parent.parent


def test_atomic_state_and_real_lock(tmp_path):
    target = tmp_path / 'state.json'
    win.atomic_state(target, {'phase': 'first'})
    win.atomic_state(target, {'phase': 'second'})
    assert json.loads(target.read_text()) == {'phase': 'second'}
    assert list(tmp_path.glob('*.tmp')) == []
    with win.exclusive_lock(tmp_path / 'apply.lock'):
        code = ('from updates.windows import exclusive_lock; import sys\n'
                'try:\n with exclusive_lock(sys.argv[1]): pass\n'
                'except OSError: sys.exit(23)\n')
        result = subprocess.run([sys.executable, '-c', code, str(tmp_path / 'apply.lock')], cwd=ROOT)
        assert result.returncode == 23
    with win.exclusive_lock(tmp_path / 'apply.lock'):
        pass


def test_real_process_handle_wait_and_image_identity():
    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(0.7)'])
    handle = None
    try:
        handle = win.ProcessHandle(child.pid, sys.executable)
        with pytest.raises(TimeoutError):
            handle.wait(0.001)
        handle.wait(5)
        assert child.wait(timeout=5) == 0
    finally:
        if handle:
            handle.close()
        if child.poll() is None:
            child.terminate()
            child.wait(timeout=5)
    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(10)'])
    try:
        with pytest.raises(ValueError, match='mismatch'):
            win.ProcessHandle(child.pid, Path(sys.executable).with_name('different.exe'))
    finally:
        child.terminate()
        child.wait(timeout=5)


def test_real_junction_rejected(tmp_path):
    destination = tmp_path / 'destination'
    destination.mkdir()
    junction = tmp_path / 'junction'
    # cmd builtin creates a directory junction without symlink privilege.
    result = subprocess.run([os.environ.get('COMSPEC', 'cmd.exe'), '/d', '/c',
                             'mklink', '/J', str(junction), str(destination)], capture_output=True)
    assert result.returncode == 0, result.stderr.decode(errors='replace')
    try:
        with pytest.raises(ValueError, match='reparse'):
            win.real_path(junction / 'artifact.exe')
    finally:
        os.rmdir(junction)  # only the link; never recurse through its target
    assert destination.is_dir()


def test_development_and_once_never_opt_in(tmp_path, monkeypatch):
    assert not installed_client(tmp_path)
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    assert not installed_client(tmp_path, once=True)
    assert not installed_client(tmp_path)


def test_installed_gate_requires_exact_default_profile_and_helper(tmp_path, monkeypatch):
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path))
    install, data = win.client_paths()
    install.mkdir(parents=True)
    data.mkdir(parents=True)
    client = install / 'TracerCliente.exe'
    client.write_bytes(b'fixture')
    monkeypatch.setattr(sys, 'executable', str(client))
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    assert not installed_client(data)
    (install / 'TracerAtualizador.exe').write_bytes(b'fixture')
    assert installed_client(data)
    assert not installed_client(tmp_path / 'portable')


def test_update_profile_does_not_accept_direct_or_custom_server():
    from dataclasses import replace
    from client.state import Config, COMPANY_SERVER
    from updates.runtime import activated_profile
    config = Config(server_url=COMPANY_SERVER, device_id='fixture', installation_id='fixture',
        activation_id='fixture', protected_token='not-used', role='sender')
    assert activated_profile(config)
    assert not activated_profile(replace(config, delivery_mode='direct'))
    assert not activated_profile(replace(config, server_url='https://other.example'))
    assert not activated_profile(replace(config, ca_file='custom.pem'))
    assert not activated_profile(replace(config, activation_id=''))


def test_unsigned_payload_is_not_authorized_even_with_correct_checksum(tmp_path):
    binary = tmp_path / 'payload.exe'
    binary.write_bytes(b'MZ fixture')
    metadata = {'name': binary.name, 'sha256': hashlib.sha256(binary.read_bytes()).hexdigest(),
                'size': binary.stat().st_size, 'kind': 'inno-client'}
    core.verify_artifact(binary, metadata)
    (tmp_path / 'manifest.json').write_text(json.dumps({'payload': {'version': '99.0.0'},
        'signature': base64.b64encode(b'0' * 64).decode()}))
    with pytest.raises(core.UpdateRejected, match='signature'):
        win.verified_request(tmp_path)


def test_plan_rejects_commands_before_execution(tmp_path, monkeypatch):
    install = tmp_path / 'installed'
    data = tmp_path / 'data'
    request = data / 'updates' / 'request-test'
    request.mkdir(parents=True)
    monkeypatch.setattr(win, 'client_paths', lambda: (install, data))
    win.atomic_state(request / 'request.json', {'schema': 1, 'pid': os.getpid(), 'command': 'anything'})
    with pytest.raises(ValueError, match='fields'):
        win.apply_client(request)
    assert not (request / 'backup').exists()


def test_request_outside_cache_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(win, 'client_paths', lambda: (tmp_path / 'installed', tmp_path / 'data'))
    with pytest.raises(ValueError, match='outside'):
        win.apply_client(tmp_path / 'arbitrary')


@pytest.mark.parametrize('exit_code', [0, 1, 3010])
def test_application_real_files_subprocess_exit_and_binary_recovery(tmp_path, monkeypatch, exit_code):
    install = tmp_path / 'installed'
    data = tmp_path / 'data'
    request = data / 'updates' / 'request-controlled'
    install.mkdir()
    request.mkdir(parents=True)
    client = install / 'TracerCliente.exe'
    helper = install / 'TracerAtualizador.exe'
    client.write_bytes(b'original-client')
    helper.write_bytes(b'original-helper')
    # Real data sentinels must survive both success and rollback, unchanged.
    for name in ('queue.sqlite3', 'config.json', 'token.dpapi'):
        (data / name).write_bytes(b'preserve-' + name.encode())
    artifact = request / 'fixture.exe'
    artifact.write_bytes(b'controlled installer fixture, not a Windows EXE')
    metadata = {'name': artifact.name, 'sha256': hashlib.sha256(artifact.read_bytes()).hexdigest(),
                'size': artifact.stat().st_size, 'kind': 'inno-client'}
    count = []
    def authorize_fixture(directory):
        # Explicit substitution: crypto acceptance is covered by core tests;
        # file verification and re-verification still use the real implementation.
        assert directory == request
        core.verify_artifact(artifact, metadata)
        count.append('verified')
        return {'version': '0.1.3'}, artifact
    monkeypatch.setattr(win, 'client_paths', lambda: (install, data))
    monkeypatch.setattr(win, 'verified_request', authorize_fixture)
    real_handle = win.ProcessHandle
    monkeypatch.setattr(win, 'ProcessHandle', lambda pid, expected: real_handle(pid, sys.executable))
    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(0.3)'])
    win.atomic_state(request / 'request.json', {'schema': 1, 'pid': child.pid})
    launched = []
    def run_fixture(arguments, **kwargs):
        assert child.poll() == 0  # helper really waited on the kernel handle
        assert arguments == win.installer_arguments(artifact, install)
        assert count == ['verified', 'verified']
        # Real subprocess mutates only isolated fixture files, then exits.
        script = ('from pathlib import Path;import sys;'
                  'Path(sys.argv[1]).write_bytes(b"new-client");'
                  'Path(sys.argv[2]).write_bytes(b"new-helper");sys.exit(int(sys.argv[3]))')
        return subprocess.run([sys.executable, '-c', script, str(client), str(helper), str(exit_code)], **kwargs)
    def start_fixture(arguments, **kwargs):
        launched.append(arguments)
        return subprocess.Popen([sys.executable, '-c', 'pass'], **kwargs)
    try:
        if exit_code:
            with pytest.raises(RuntimeError, match='Installer failed'):
                win.apply_client(request, run=run_fixture, start=start_fixture)
            assert client.read_bytes() == b'original-client'
            assert helper.read_bytes() == b'original-helper'
            assert launched == []
        else:
            win.apply_client(request, run=run_fixture, start=start_fixture)
            assert client.read_bytes() == b'new-client'
            assert helper.read_bytes() == b'new-helper'
            assert launched == [[str(client), '--background', '--data-dir', str(data)]]
        journal = json.loads((request / 'state.json').read_text())
        assert journal['phase'] == ('failed' if exit_code else 'complete')
        assert (request / 'backup' / client.name).read_bytes() == b'original-client'
        assert (request / 'backup' / helper.name).read_bytes() == b'original-helper'
        for name in ('queue.sqlite3', 'config.json', 'token.dpapi'):
            assert (data / name).read_bytes() == b'preserve-' + name.encode()
        # Reusing a partial/completed request cannot overwrite the first backup.
        with pytest.raises(ValueError, match='journal'):
            win.apply_client(request, run=run_fixture, start=start_fixture)
        assert (request / 'backup' / client.name).read_bytes() == b'original-client'
    finally:
        child.wait(timeout=5)


def test_periodic_discovery_does_not_take_transfer_lock_or_stop_on_404(tmp_path, monkeypatch):
    from client.agent import Agent
    from client.state import Config, save_config, COMPANY_SERVER
    agent = Agent(tmp_path)
    agent.user_mode = True
    save_config(tmp_path, Config(server_url=COMPANY_SERVER, device_id='fixture', installation_id='fixture',
        activation_id='fixture', protected_token='not-used', role='sender'))
    entered = threading.Event()
    def unavailable(*args):
        entered.set()
        raise core.UpdateUnavailable('HTTP 404')
    monkeypatch.setattr(core, 'prepare_update', unavailable)
    updates = ClientUpdates(agent)
    try:
        with agent.lock:
            updates.start()
            assert entered.wait(5), 'discovery was blocked by transfer lock'
            deadline = time.monotonic() + 5
            while not (tmp_path / 'updates' / 'status.json').exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            state = json.loads((tmp_path / 'updates' / 'status.json').read_text())
            assert state['state'] == 'unavailable'
            assert not agent.stop_event.is_set()
            assert not agent.update_requested.is_set()
    finally:
        agent.stop_event.set()
        updates.close()
        agent.close()


def test_delivery_periodic_discovery_records_unavailable(tmp_path, monkeypatch):
    data, output = tmp_path / 'data', tmp_path / 'output'
    data.mkdir(); output.mkdir()
    from delivery.agent import DeliveryAgent
    from delivery.state import atomic_json, protect_token
    atomic_json(data / 'identity.json', {'role': 'delivery', 'server_url': 'https://delivery.example',
                'protected_token': protect_token('test-secret')})
    backend = SimpleNamespace(handle=lambda req: None)
    import httpx
    agent = DeliveryAgent(data, output, transport=httpx.MockTransport(backend.handle))
    entered = threading.Event()
    def unavailable(*args):
        entered.set()
        raise core.UpdateUnavailable('HTTP 404')
    monkeypatch.setattr(core, 'prepare_update', unavailable)
    updates = DeliveryUpdates(agent)
    try:
        with agent.lock:
            updates.start()
            assert entered.wait(5), 'delivery discovery was blocked'
            deadline = time.monotonic() + 5
            while not (data / 'updates' / 'status.json').exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            state = json.loads((data / 'updates' / 'status.json').read_text())
            assert state['state'] == 'unavailable'
            assert not agent.stop_event.is_set()
            assert not agent.update_requested.is_set()
    finally:
        agent.stop_event.set()
        updates.close()
        agent.close()


def test_installed_delivery_contracts(tmp_path, monkeypatch):
    base = tmp_path / 'TracerTransfer-Delivery'
    bin_dir = base / 'bin'
    data_dir = base / 'data'
    updater_dir = base / 'updater'
    bin_dir.mkdir(parents=True)
    data_dir.mkdir(parents=True)
    updater_dir.mkdir(parents=True)

    entregador = bin_dir / 'TracerEntregador.exe'
    atualizador = updater_dir / 'TracerAtualizador.exe'
    entregador.write_bytes(b'exe')
    atualizador.write_bytes(b'exe')

    assert not installed_delivery(data_dir, once=True)

    monkeypatch.setattr(sys, 'frozen', False, raising=False)
    assert not installed_delivery(data_dir)

    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(sys, 'executable', str(entregador))
    assert installed_delivery(data_dir)


def test_launch_delivery_contracts(tmp_path, monkeypatch):
    base = tmp_path / 'TracerTransfer-Delivery'
    updater_dir = base / 'updater'
    updater_dir.mkdir(parents=True)
    atualizador = updater_dir / 'TracerAtualizador.exe'
    atualizador.write_bytes(b'updater')

    calls = []
    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(subprocess, 'run', fake_run)

    assert win.launch_delivery(None, base)
    assert calls[0][0:2] == ['schtasks.exe', '/run']


def test_powershell_parser_and_scheduled_system_contract():
    paths = [ROOT / 'scripts' / 'build.ps1', ROOT / 'deploy' / 'windows-delivery' / 'Enable-AutoUpdate.ps1',
             ROOT / 'deploy' / 'windows-delivery' / 'Update-Delivery.ps1']
    # Parse only. Do NOT execute scripts that install tasks or change services.
    script = "$ErrorActionPreference='Stop'; foreach($p in $args){$tokens=$null;$errors=$null;[void][System.Management.Automation.Language.Parser]::ParseFile($p,[ref]$tokens,[ref]$errors);if($errors.Count){$errors|Out-String|Write-Error};Write-Output ('PARSED '+$p)}"
    # -Command arguments are parsed by PowerShell; use environment for inert paths.
    command = "$paths=$env:TT_PARSE_PATHS|ConvertFrom-Json;" + script.replace('in $args', 'in $paths')
    result = subprocess.run([win.powershell_path(), '-NoProfile', '-NonInteractive', '-Command', command],
        env={**os.environ, 'TT_PARSE_PATHS': json.dumps([str(p) for p in paths])}, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    # The helper's fixed PowerShell guard must parse too; never invoke SCM here.
    import ast
    import inspect
    tree = ast.parse(inspect.getsource(win.service_guard))
    guard = next(node.value.value for node in ast.walk(tree)
                 if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'script' for t in node.targets))
    parse_input = "$tokens=$null;$errors=$null;[void][System.Management.Automation.Language.Parser]::ParseInput($env:TT_GUARD_SOURCE,[ref]$tokens,[ref]$errors);if($errors.Count){$errors|Out-String|Write-Error;exit 1}"
    parsed = subprocess.run([win.powershell_path(), '-NoProfile', '-NonInteractive', '-Command', parse_input],
        env={**os.environ, 'TT_GUARD_SOURCE': guard}, capture_output=True, text=True)
    assert parsed.returncode == 0, parsed.stdout + parsed.stderr
    enable = paths[1].read_text()
    assert "-UserId 'S-1-5-18'" in enable
    assert '-Hours 6' in enable and '-Enable required' in enable
    assert 'CheckProtectedWrites' in enable
    update = paths[2].read_text()
    assert '$exe.previous.$tag' in update
    assert 'WaitReady $since' in update
    assert 'taskkill' not in update.lower()
    assert 'sqlite' in update.lower()


def test_frozen_helper_when_supplied(tmp_path):
    configured = os.environ.get('TRACER_UPDATER_TEST_EXE')
    if not configured:
        pytest.skip('Set TRACER_UPDATER_TEST_EXE to exercise a compiled helper')
    executable = Path(configured)
    assert executable.is_file()
    # Freeze imports, bundled script and fail-closed CLI under a real bootloader.
    from PyInstaller.archive.readers import CArchiveReader
    archive = CArchiveReader(str(executable))
    bundled = next(name for name in archive.toc if name.replace('\\', '/').endswith('deploy/windows-delivery/Update-Delivery.ps1'))
    assert archive.extract(bundled) == (ROOT / 'deploy' / 'windows-delivery' / 'Update-Delivery.ps1').read_bytes()
    modules = archive.open_embedded_archive('PYZ.pyz').toc
    for name in ('updates.core', 'updates.windows', 'httpx', 'cryptography'):
        assert name in modules
    env = {**os.environ, 'TEMP': str(tmp_path), 'TMP': str(tmp_path), 'TMPDIR': str(tmp_path)}
    help_result = subprocess.run([str(executable), '--help'], env=env, capture_output=True, text=True, timeout=30)
    assert help_result.returncode == 0 and '--client-request' in help_result.stdout
    refused = subprocess.run([str(executable), '--client-request', str(tmp_path / 'not-installed')],
                             env=env, capture_output=True, text=True, timeout=30)
    assert refused.returncode == 1
    assert 'ValueError' in refused.stderr
    # This imports the crypto/HTTP core before rejecting a scratch-only service
    # target. It does not name, stop or start a production service.
    service = subprocess.run([str(executable), '--service', '--base-directory', str(tmp_path / 'absent-service')],
                            env=env, capture_output=True, text=True, timeout=30)
    assert service.returncode == 1
    assert 'PermissionError' in service.stderr
    assert not (tmp_path / 'absent-service').exists()


def test_bundled_script_and_inno_macro():
    spec = (ROOT / 'packaging' / 'updater.spec').read_text()
    assert "console=True" in spec and "'Update-Delivery.ps1'" in spec
    installer = (ROOT / 'packaging' / 'client.iss').read_text()
    assert '#ifdef UpdaterExe' in installer
    assert 'DestName: "TracerAtualizador.exe"' in installer
