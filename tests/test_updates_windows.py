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


def test_exact_outer_bootloader_handle_is_waited(tmp_path, monkeypatch):
    pid_file = tmp_path / 'child.pid'
    interpreter = str(Path(getattr(sys, '_base_executable', sys.executable)).resolve())  # No venv redirector or uv symlink.
    script = ('import subprocess,sys,time;from pathlib import Path;'
              'p=subprocess.Popen([sys.executable,"-c","import time;time.sleep(0.8)"]);'
              'Path(sys.argv[1]).write_text(str(p.pid));time.sleep(2.5);p.wait()')
    outer = subprocess.Popen([interpreter, '-c', script, str(pid_file)])
    handle = None
    try:
        deadline = time.monotonic() + 5
        while not pid_file.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        monkeypatch.setattr(sys, 'frozen', True, raising=False)
        handle = win.ProcessHandle(int(pid_file.read_text()), interpreter)
        assert handle.bootloader is not None
        with pytest.raises(TimeoutError, match='Bootloader'):
            handle.wait(1)
        handle.wait(5)
        assert outer.wait(timeout=5) == 0
    finally:
        if handle:
            handle.close()
        if outer.poll() is None:
            subprocess.run(['taskkill.exe', '/PID', str(outer.pid), '/T', '/F'], capture_output=True)
            outer.wait(timeout=5)


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
    assert installed_client(data)
    assert not (install / 'TracerAtualizador.exe').exists()
    assert not installed_client(tmp_path / 'portable')


def test_update_profile_preserves_generic_activated_server(monkeypatch):
    from dataclasses import replace
    from client.state import Config, COMPANY_SERVER
    from updates.runtime import activated_profile
    config = Config(server_url=COMPANY_SERVER, device_id='fixture', installation_id='fixture',
        activation_id='fixture', protected_token='not-used', role='sender')
    assert activated_profile(config)
    assert not activated_profile(replace(config, delivery_mode='direct'))
    monkeypatch.setattr('client.state.COMPANY_SERVER', '')
    assert activated_profile(replace(config, server_url='https://other.example'))
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
    helper = install / 'legacy-contingency.exe'
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
                  'sys.exit(int(sys.argv[3]))')
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
            assert helper.read_bytes() == b'original-helper'
            assert launched == [[str(client), '--background', '--data-dir', str(data)]]
        journal = json.loads((request / 'state.json').read_text())
        assert journal['phase'] == ('failed' if exit_code else 'complete')
        assert (request / 'backup' / client.name).read_bytes() == b'original-client'
        assert not (request / 'backup' / helper.name).exists()
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
    (tmp_path / 'updater').mkdir()
    (tmp_path / 'updater' / 'installed_version').write_text('0.1.3')
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
    atualizador = updater_dir / 'installed_version'
    entregador.write_bytes(b'exe')
    atualizador.write_text('0.1.3')

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


def test_client_internal_mode_precedes_single_instance(tmp_path, monkeypatch):
    from client import __main__ as entry
    calls = []
    monkeypatch.setattr(win, 'client_update_main', lambda path: calls.append(path) or 7, raising=False)
    monkeypatch.setattr(entry, 'SingleInstance', lambda *a: pytest.fail('must not acquire client lock'))
    assert entry.main(['--apply-update', str(tmp_path)]) == 7
    assert calls == [str(tmp_path)]


def test_service_internal_modes_precede_runtime_state(tmp_path, monkeypatch):
    from delivery import __main__ as entry
    calls = []
    monkeypatch.setattr(win, 'delivery_update_main', lambda base, request=None: calls.append((base, request)) or 7, raising=False)
    monkeypatch.setattr(entry, 'State', lambda *a: pytest.fail('must not acquire delivery lock'))
    assert entry.main(['--update', '--base-directory', str(tmp_path)]) == 7
    request = tmp_path / 'updater' / 'run-test'
    assert entry.main(['--apply-update', str(request), '--base-directory', str(tmp_path)]) == 7
    assert calls == [(tmp_path, None), (tmp_path, request)]


def test_delivery_launcher_copies_same_exe_to_protected_request(tmp_path, monkeypatch):
    (tmp_path / 'bin').mkdir()
    (tmp_path / 'updater').mkdir()
    installed = tmp_path / 'bin' / 'TracerEntregador.exe'
    installed.write_bytes(b'installed-trusted-image')
    monkeypatch.setattr(win, 'service_guard', lambda base: tmp_path)
    monkeypatch.setattr(sys, 'executable', str(installed))
    calls = []
    def start(arguments, **kwargs):
        calls.append(arguments)
        image, flag, request, base_flag, base = arguments
        request = Path(request)
        assert flag == '--apply-update' and base_flag == '--base-directory'
        assert request.parent == tmp_path / 'updater' and Path(base) == tmp_path
        assert Path(image) == request / installed.name
        assert Path(image).read_bytes() == installed.read_bytes()
        assert json.loads((request / 'request.json').read_text()) == {'schema': 1, 'pid': os.getpid()}
        (request / 'ready').write_text('ready')
        return SimpleNamespace(poll=lambda: None)
    monkeypatch.setattr(win.subprocess, 'Popen', start)
    win.detach_service(tmp_path)
    assert len(calls) == 1
    assert not list(tmp_path.rglob('TracerAtualizador.exe'))


def test_detached_service_waits_exact_launcher_before_apply(tmp_path, monkeypatch):
    request = tmp_path / 'updater' / 'run-controlled'
    request.mkdir(parents=True)
    interpreter = sys.executable
    child = subprocess.Popen([interpreter, '-c', 'import time;time.sleep(0.5)'])
    win.atomic_state(request / 'request.json', {'schema': 1, 'pid': child.pid})
    monkeypatch.setattr(win, 'service_guard', lambda base: tmp_path)
    monkeypatch.setattr(sys, 'executable', str(request / 'TracerEntregador.exe'))
    real_handle = win.ProcessHandle
    monkeypatch.setattr(win, 'ProcessHandle', lambda pid, image: real_handle(pid, interpreter))
    applied = []
    def apply(base):
        assert child.poll() == 0
        assert (request / 'ready').is_file()
        applied.append(base)
    monkeypatch.setattr(win, 'apply_service', apply)
    try:
        win.apply_detached_service(tmp_path, request)
        assert applied == [tmp_path]
        with pytest.raises(ValueError, match='outside'):
            win.apply_detached_service(tmp_path, tmp_path / 'data' / 'run-attacker')
    finally:
        child.wait(timeout=5)


@pytest.mark.parametrize('kind', [ClientUpdates, DeliveryUpdates])
def test_discovery_close_joins_without_stopping_agent(tmp_path, kind):
    agent = SimpleNamespace(data_dir=tmp_path, stop_event=threading.Event())
    updates = kind(agent)
    entered = threading.Event()
    def work():
        entered.set()
        updates.stopping.wait(30)
    updates.thread = threading.Thread(target=work)
    updates.start()
    assert entered.wait(5)
    updates.close()
    assert not updates.thread.is_alive()
    assert not agent.stop_event.is_set()


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
    configured = os.environ.get('TRACER_DELIVERY_TEST_EXE')
    if not configured:
        pytest.skip('Set TRACER_DELIVERY_TEST_EXE to exercise the compiled delivery internal mode')
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
    assert help_result.returncode == 0 and '--apply-update' in help_result.stdout
    refused = subprocess.run([str(executable), '--apply-update', str(tmp_path / 'not-installed'), '--base-directory', str(tmp_path / 'absent-service')],
                             env=env, capture_output=True, text=True, timeout=30)
    assert refused.returncode == 1
    assert 'PermissionError' in refused.stderr
    # This imports the crypto/HTTP core before rejecting a scratch-only service
    # target. It does not name, stop or start a production service.
    service = subprocess.run([str(executable), '--update', '--base-directory', str(tmp_path / 'absent-service')],
                            env=env, capture_output=True, text=True, timeout=30)
    assert service.returncode == 1
    assert 'PermissionError' in service.stderr
    assert not (tmp_path / 'absent-service').exists()


def test_bundled_script_and_inno_macro():
    spec = (ROOT / 'packaging' / 'delivery.spec').read_text()
    assert 'Update-Delivery.ps1' in spec
    installer = (ROOT / 'packaging' / 'client.iss').read_text()
    assert 'TracerAtualizador.exe' not in installer
    assert 'UpdaterExe' not in installer


@pytest.mark.parametrize('mode', ['success', 'stopped', 'stopfail', 'badhealth', 'wrongproc'])
def test_delivery_powershell_transaction_without_real_scm(tmp_path, mode):
    """Execute the real transaction with inert SCM/token fixtures, real file copies.

    No Get/Stop/Start-Service or CIM operation reaches Windows SCM.
    """
    base = tmp_path / 'service'
    (base / 'bin').mkdir(parents=True)
    (base / 'data').mkdir()
    installed = base / 'bin' / 'TracerEntregador.exe'
    installed.write_bytes(b'original')
    (base / 'data' / 'identity.json').write_text('{}')
    (base / 'data' / 'journal.sqlite').write_bytes(b'preserve-live-journal')
    (base / 'TracerTransferDelivery.exe').write_bytes(b'fixture-wrapper')
    (base / 'TracerTransferDelivery.xml').write_text('<service/>')
    incoming = tmp_path / 'incoming.exe'
    incoming.write_bytes(b'new-binary')
    source = (ROOT / 'deploy/windows-delivery/Update-Delivery.ps1').read_text()
    admin = "if(!([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)){throw 'Administrator token required'}"
    assert source.count(admin) == 1
    fixture = tmp_path / 'transaction.ps1'
    fixture.write_text(source.replace(admin, '# Privilege check substituted ONLY in isolated transaction fixture'), encoding='utf-8')
    driver = r'''
$ErrorActionPreference='Stop'
$global:calls=New-Object Collections.Generic.List[string]
$global:starts=0
$global:clock=[datetime]'2026-11-10T12:00:00Z'
$global:state=if($env:TT_MODE -eq 'stopped'){'Stopped'}else{'Running'}
function Get-Date {$global:clock=$global:clock.AddSeconds(120);return $global:clock}
function Start-Sleep {param($Seconds)}
function Get-FileHash {param($LiteralPath,$Algorithm)
 if(!$LiteralPath){$LiteralPath=$args[0]}
 if($LiteralPath.EndsWith('TracerTransferDelivery.exe')){return @{Hash='923111C7142B3DC783A3C722B19B8A21BCB78222D7A136AC33F0CA8A29F4CB66'}}
 Microsoft.PowerShell.Utility\Get-FileHash -LiteralPath $LiteralPath -Algorithm SHA256
}
function Get-CimInstance {param($ClassName,$Filter)
 if($ClassName -eq 'Win32_Service'){return [pscustomobject]@{State=$global:state;PathName=($env:TT_BASE+'\TracerTransferDelivery.exe');StartName='NT AUTHORITY\LocalService';ProcessId=100}}
 if($ClassName -eq 'Win32_Process'){return [pscustomobject]@{ExecutablePath=($env:TT_BASE+'\bin\TracerEntregador.exe');ParentProcessId=100;CreationDate=([datetime]'2999-01-01Z')}}
 throw 'Unexpected fixture CIM'
}
function Get-Service {param($Name)
 $obj=[pscustomobject]@{Status=$global:state}
 $obj|Add-Member ScriptMethod WaitForStatus {param($State,$Timeout) if($env:TT_MODE -eq 'stopfail'){throw 'fixture stop timeout'}}
 return $obj
}
function Stop-Service {param($Name,$ErrorAction) $global:calls.Add('stop');$global:state='Stopped'}
function Start-Service {param($Name)
 $global:calls.Add('start');$global:state='Running';$global:starts++
 $status=if($env:TT_MODE -eq 'badhealth' -and $global:starts -eq 1){'error'}else{'poll_ok'}
 @{status=$status;at='2999-01-01T00:00:00Z';pid=200}|ConvertTo-Json|Set-Content ($env:TT_BASE+'\data\status.json')
}
if($env:TT_MODE -eq 'wrongproc'){
 function Get-CimInstance {param($ClassName,$Filter)
  if($ClassName -eq 'Win32_Service'){return [pscustomobject]@{State=$global:state;PathName=($env:TT_BASE+'\TracerTransferDelivery.exe');StartName='NT AUTHORITY\LocalService';ProcessId=100}}
  $path=if($global:starts -eq 1){'C:\wrong.exe'}else{$env:TT_BASE+'\bin\TracerEntregador.exe'}
  return [pscustomobject]@{ExecutablePath=$path;ParentProcessId=100;CreationDate=([datetime]'2999-01-01Z')}
 }
}
try{
 . $env:TT_SCRIPT -BaseDirectory $env:TT_BASE -BinaryPath $env:TT_BINARY -ExpectedBinarySha256 $env:TT_HASH
 $code=0
}catch{Write-Output $_.Exception.Message;$code=17}
ConvertTo-Json -InputObject @($global:calls.ToArray()) -Compress | Set-Content $env:TT_CALLS
exit $code
'''
    calls_file = tmp_path / 'calls.json'
    result = subprocess.run([win.powershell_path(), '-NoProfile', '-NonInteractive', '-Command', driver],
        env={**os.environ, 'TT_MODE': mode, 'TT_BASE': str(base), 'TT_SCRIPT': str(fixture),
             'TT_BINARY': str(incoming), 'TT_HASH': hashlib.sha256(b'new-binary').hexdigest(),
             'TT_CALLS': str(calls_file)}, capture_output=True, text=True, timeout=30)
    calls = json.loads(calls_file.read_text(encoding='utf-8-sig'))
    assert result.returncode == (0 if mode == 'success' else 17), result.stdout + result.stderr
    assert installed.read_bytes() == (b'new-binary' if mode == 'success' else b'original')
    assert (base / 'data' / 'journal.sqlite').read_bytes() == b'preserve-live-journal'
    assert not list((base / 'bin').glob('*.incoming.*'))
    if mode == 'stopped':
        assert calls == []
    elif mode == 'stopfail':
        assert calls == ['stop']
        assert not list((base / 'bin').glob('*.previous.*'))
    else:
        backups = list((base / 'bin').glob('*.previous.*'))
        assert len(backups) == 1 and backups[0].read_bytes() == b'original'
        assert calls == (['stop', 'start'] if mode == 'success' else ['stop', 'start', 'stop', 'start'])
