"""Windows update application. No shell commands or URLs are accepted from plans.

The signed envelope is revalidated in the detached helper, not merely its hash.
Only executable backups are restored: queue, DPAPI identity and journals stay live.
"""
from contextlib import contextmanager
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid

SERVICE_NAME = 'TracerTransferDelivery'
WRAPPER_SHA256 = '923111C7142B3DC783A3C722B19B8A21BCB78222D7A136AC33F0CA8A29F4CB66'


def real_path(path):
    """Reject reparse points before resolution (including junction ancestors)."""
    path = Path(os.path.abspath(path))
    for item in (path, *path.parents):
        if item.exists() or item.is_symlink():
            if item.is_symlink() or getattr(item.lstat(), 'st_file_attributes', 0) & 0x400:
                raise ValueError('Update paths cannot contain reparse points')
    return path


def client_paths():
    local = real_path(os.environ['LOCALAPPDATA'])
    return (local / 'Programs' / 'TracerTransfer' / 'Client',
            local / 'TracerTransfer' / 'client')


def atomic_state(path, value):
    path = Path(path)
    tmp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with tmp.open('x', encoding='utf-8') as out:
            json.dump(value, out, sort_keys=True)
            out.flush()
            os.fsync(out.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


@contextmanager
def exclusive_lock(path):
    import msvcrt
    with Path(path).open('a+b') as stream:
        stream.seek(0)
        if not stream.read(1):
            stream.write(b'0')
            stream.flush()
        stream.seek(0)
        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        try:
            yield
        finally:
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)


class ProcessHandle:
    """Hold the precise process object across shutdown, avoiding PID reuse races."""
    def __init__(self, pid, expected_executable):
        if os.name != 'nt' or type(pid) is not int or pid <= 0 or pid == os.getpid():
            raise ValueError('Invalid Windows parent PID')
        kernel = self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
        self.handle = kernel.OpenProcess(0x100000 | 0x1000, False, pid)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            buffer = ctypes.create_unicode_buffer(32768)
            size = wintypes.DWORD(len(buffer))
            if not kernel.QueryFullProcessImageNameW(self.handle, 0, buffer, ctypes.byref(size)):
                raise ctypes.WinError(ctypes.get_last_error())
            if real_path(buffer.value) != real_path(expected_executable):
                raise ValueError('Parent executable mismatch')
        except BaseException:
            self.close()
            raise

    def wait(self, seconds=180):
        result = self.kernel.WaitForSingleObject(self.handle, int(seconds * 1000))
        if result != 0:
            raise TimeoutError('Client did not exit; no binaries changed')

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


def verified_request(directory):
    from updates import VERSION
    from updates.core import verify_manifest, verify_artifact, version_tuple
    directory = real_path(directory)
    payload = verify_manifest(real_path(directory / 'manifest.json').read_bytes())
    if version_tuple(payload['version']) <= version_tuple(VERSION):
        raise ValueError('Client downgrade/replay rejected')
    metadata = payload['artifacts']['client']
    artifact = real_path(directory / metadata['name'])
    if artifact.parent != directory:
        raise ValueError('Artifact outside request')
    verify_artifact(artifact, metadata)
    return payload, artifact


def installer_arguments(artifact, install_dir):
    # No user-controlled switches, executable path or task selection from a plan.
    return [str(artifact), '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART',
            '/SP-', '/DIR=' + str(install_dir), '/TASKS=']


def apply_client(directory, *, run=subprocess.run, start=subprocess.Popen):
    directory = real_path(directory)
    install, data = client_paths()
    install, data = real_path(install), real_path(data)
    cache = real_path(data / 'updates')
    if directory.parent != cache or not directory.name.startswith('request-'):
        raise ValueError('Request outside installed user profile')
    plan = json.loads((directory / 'request.json').read_text(encoding='utf-8'))
    if set(plan) != {'schema', 'pid'} or plan['schema'] != 1:
        raise ValueError('Unexpected request fields')
    client = real_path(install / 'TracerCliente.exe')
    helper = real_path(install / 'TracerAtualizador.exe')
    with exclusive_lock(cache / 'apply.lock'):
        journal = directory / 'state.json'
        if journal.exists() and json.loads(journal.read_text(encoding='utf-8')).get('phase') in ('installing', 'failed', 'installed', 'complete'):
            raise ValueError('Existing application journal: inspect recovery; do not overwrite backup')
        verified_request(directory)
        handle = ProcessHandle(plan['pid'], client)
        try:
            atomic_state(journal, {'phase': 'waiting'})
            (directory / 'ready').write_text('ready', encoding='ascii')
            handle.wait()
        finally:
            handle.close()
        # Snapshot first binaries exactly once, after the client process releases them.
        backups = directory / 'backup'
        backups.mkdir(exist_ok=False)
        for binary in (client, helper):
            shutil.copy2(binary, backups / binary.name)
        atomic_state(journal, {'phase': 'installing'})
        try:
            payload, artifact = verified_request(directory)  # expiry, signature AND bytes immediately before execution
            result = run(installer_arguments(artifact, install), cwd=directory, check=False)
            if result.returncode != 0:  # 3010 is not accepted: no automatic reboot path.
                raise RuntimeError('Installer failed: ' + str(result.returncode))
            if not client.is_file() or not helper.is_file():
                raise RuntimeError('Installer did not leave installed executables')
            atomic_state(journal, {'phase': 'installed', 'version': payload['version']})
        except Exception as exc:
            for binary in (client, helper):
                shutil.copy2(backups / binary.name, binary)
            atomic_state(journal, {'phase': 'failed', 'error': type(exc).__name__, 'binaries_restored': True})
            raise
        # Same per-user data and installation; do not modify startup preference.
        start([str(client), '--background', '--data-dir', str(data)], cwd=install)
        atomic_state(journal, {'phase': 'complete', 'version': payload['version']})


def launch_client(prepared, data_dir):
    """Called only between transfer cycles under Agent.lock. Handshake before exit."""
    install, expected_data = client_paths()
    if real_path(data_dir) != real_path(expected_data) or real_path(sys.executable) != real_path(install / 'TracerCliente.exe'):
        raise ValueError('Automatic updates require installed per-user client')
    cache = real_path(expected_data / 'updates')
    cache.mkdir(exist_ok=True)
    request = cache / ('request-' + uuid.uuid4().hex)
    request.mkdir()
    (request / 'manifest.json').write_bytes(prepared.manifest_bytes)
    artifact = request / prepared.artifact_metadata['name']
    if artifact.parent != request:
        raise ValueError('Invalid artifact name')
    shutil.copy2(prepared.artifact, artifact)
    verified_request(request)
    atomic_state(request / 'request.json', {'schema': 1, 'pid': os.getpid()})
    # Never run the installed helper in place: Inno must be able to replace it.
    helper = request / 'TracerAtualizador.exe'
    shutil.copy2(real_path(install / helper.name), helper)
    process = subprocess.Popen([str(helper), '--client-request', str(request)], cwd=request,
                               creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if (request / 'ready').exists():
            return request
        if process.poll() is not None:
            raise RuntimeError('Detached updater rejected request')
        time.sleep(0.05)
    # The helper has not acknowledged: leave client alive; its handle wait times out safely.
    raise TimeoutError('Detached updater handshake timed out')


def launch_delivery(prepared, base_dir):
    """Trigger or launch delivery service update."""
    base = real_path(base_dir)
    private = base / 'updater'
    helper = private / 'TracerAtualizador.exe'
    if not helper.is_file():
        raise FileNotFoundError('TracerAtualizador.exe missing from updater directory')
    try:
        res = subprocess.run(['schtasks.exe', '/run', '/tn', 'TracerTransferDelivery-AutoUpdate'],
                             capture_output=True, check=False)
        if res.returncode == 0:
            return True
    except (OSError, FileNotFoundError):
        pass
    flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    flags |= getattr(subprocess, 'DETACHED_PROCESS', 0x00000008)
    subprocess.Popen([str(helper), '--service', '--base-directory', str(base)],
                     cwd=private, creationflags=flags)
    return True


def service_guard(base):
    """Validate privileged destination and SCM identity before any update/download."""
    base = real_path(base)
    script = r'''$ErrorActionPreference='Stop'
$b=$env:TT_UPDATE_BASE
$id='TracerTransferDelivery'
$trustedInstaller=(New-Object Security.Principal.NTAccount('NT SERVICE','TrustedInstaller')).Translate([Security.Principal.SecurityIdentifier]).Value
$ancestor=[IO.Path]::GetDirectoryName($b)
while($ancestor){
 $acl=Get-Acl -LiteralPath $ancestor
 if($acl.GetOwner([Security.Principal.SecurityIdentifier]).Value -notin @('S-1-5-18','S-1-5-32-544',$trustedInstaller)){throw 'Untrusted update ancestor owner'}
 foreach($rule in $acl.Access){
  $sid=$rule.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value
  if($rule.AccessControlType -eq 'Allow' -and !($rule.PropagationFlags -band [Security.AccessControl.PropagationFlags]::InheritOnly) -and $sid -notin @('S-1-5-18','S-1-5-32-544',$trustedInstaller) -and ($rule.FileSystemRights -band [Security.AccessControl.FileSystemRights]::DeleteSubdirectoriesAndFiles -or $rule.FileSystemRights -band [Security.AccessControl.FileSystemRights]::ChangePermissions -or $rule.FileSystemRights -band [Security.AccessControl.FileSystemRights]::TakeOwnership)){throw 'Replaceable update ancestor'}
 }
 $ancestor=[IO.Path]::GetDirectoryName($ancestor)
}
$wrapper=Join-Path $b ($id+'.exe')
if((Get-FileHash -LiteralPath $wrapper -Algorithm SHA256).Hash -ne '923111C7142B3DC783A3C722B19B8A21BCB78222D7A136AC33F0CA8A29F4CB66'){throw 'Wrapper hash'}
$s=Get-CimInstance Win32_Service -Filter "Name='$id'"
if(!$s -or ![String]::Equals([IO.Path]::GetFullPath($s.PathName.Trim('"')),$wrapper,[StringComparison]::OrdinalIgnoreCase) -or $s.StartName -notin @('NT AUTHORITY\LocalService','NT AUTHORITY\Local Service')){throw 'Service identity/path'}
if($s.State -ne 'Running'){throw 'Delivery stopped; automatic updater must not start a stopped service'}
[xml]$config=Get-Content -LiteralPath "$b\$id.xml" -Raw
if($config.service.id -ne $id -or $config.service.executable -cne '%BASE%\bin\TracerEntregador.exe' -or $config.service.serviceaccount.user -ne 'LocalService'){throw 'Unexpected wrapper configuration'}
foreach($p in @($b,"$b\bin","$b\bin\TracerEntregador.exe",$wrapper,"$b\$id.xml","$b\updater")){
 $a=Get-Acl -LiteralPath $p
 $owner=$a.GetOwner([Security.Principal.SecurityIdentifier]).Value
 if($owner -notin @('S-1-5-18','S-1-5-32-544')){throw 'Privileged owner required'}
 foreach($r in $a.Access){
  $sid=$r.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value
  if($r.AccessControlType -eq 'Allow' -and $sid -notin @('S-1-5-18','S-1-5-32-544') -and ($r.FileSystemRights -band [Security.AccessControl.FileSystemRights]::Write -or $r.FileSystemRights -band [Security.AccessControl.FileSystemRights]::Delete -or $r.FileSystemRights -band [Security.AccessControl.FileSystemRights]::DeleteSubdirectoriesAndFiles -or $r.FileSystemRights -band [Security.AccessControl.FileSystemRights]::ChangePermissions -or $r.FileSystemRights -band [Security.AccessControl.FileSystemRights]::TakeOwnership)){throw 'Writable privileged destination'}
 }
 if($p -eq "$b\updater" -and !$a.AreAccessRulesProtected){throw 'Updater DACL must be protected'}
}
'''
    for item in (base / 'bin' / 'TracerEntregador.exe', base / (SERVICE_NAME + '.exe'), base / (SERVICE_NAME + '.xml'), base / 'updater'):
        real_path(item)
    result = subprocess.run([powershell_path(), '-NoProfile', '-NonInteractive', '-Command', script],
                            env={**os.environ, 'TT_UPDATE_BASE': str(base)}, capture_output=True)
    if result.returncode:
        raise PermissionError('Delivery service identity/ACL validation failed')
    return base


def powershell_path():
    return str(Path(os.environ['WINDIR']) / 'System32' / 'WindowsPowerShell' / 'v1.0' / 'powershell.exe')


def apply_service(base):
    from updates.core import prepare_update, verify_manifest, verify_artifact, version_tuple, UpdateUnavailable, UpdateRejected
    base = service_guard(base)
    private = real_path(base / 'updater')
    with exclusive_lock(private / 'apply.lock'):
        version_file = private / 'installed_version'
        if not version_file.is_file():
            raise ValueError('Installed delivery version missing; bootstrap must record the real version')
        real_path(version_file)
        current = version_file.read_text(encoding='ascii').strip()
        version_tuple(current)
        try:
            prepared = prepare_update('delivery', current, private / 'cache')
        except (UpdateUnavailable, UpdateRejected) as exc:
            atomic_state(private / 'state.json', {'phase': 'unavailable' if isinstance(exc, UpdateUnavailable) else 'rejected', 'version': current})
            raise
        if prepared is None:
            atomic_state(private / 'state.json', {'phase': 'current', 'version': current})
            return
        payload = verify_manifest(prepared.manifest_bytes)
        if version_tuple(payload['version']) <= version_tuple(current):
            raise ValueError('Delivery downgrade/replay rejected')
        metadata = payload['artifacts']['delivery']
        verify_artifact(prepared.artifact, metadata)
        script = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent.parent)) / 'deploy' / 'windows-delivery' / 'Update-Delivery.ps1'
        if not script.is_file():
            raise FileNotFoundError('Bundled Update-Delivery.ps1 missing')
        atomic_state(private / 'state.json', {'phase': 'applying', 'version': payload['version']})
        # Reverify against the pinned key immediately before handing bytes to fixed bundled code.
        verify_manifest(prepared.manifest_bytes)
        verify_artifact(prepared.artifact, metadata)
        result = subprocess.run([powershell_path(), '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
                                 '-File', str(script), '-BaseDirectory', str(base), '-BinaryPath', str(prepared.artifact),
                                 '-ExpectedBinarySha256', metadata['sha256']], check=False)
        if result.returncode:
            atomic_state(private / 'state.json', {'phase': 'failed', 'version': payload['version']})
            raise RuntimeError('Delivery update failed; inspect binary rollback/readiness')
        # Only after successful polling readiness; no journal/database rollback.
        tmp = version_file.with_suffix('.tmp')
        tmp.write_text(payload['version'], encoding='ascii')
        os.replace(tmp, version_file)
        atomic_state(private / 'state.json', {'phase': 'complete', 'version': payload['version']})


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description='TracerTransfer detached Windows updater')
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--client-request', type=Path)
    group.add_argument('--service', action='store_true')
    parser.add_argument('--base-directory', type=Path)
    args = parser.parse_args(argv)
    if os.name != 'nt':
        parser.error('Windows required')
    try:
        if args.client_request:
            if args.base_directory:
                parser.error('base-directory is service-only')
            apply_client(args.client_request)
        else:
            if args.base_directory is None:
                parser.error('service requires base-directory')
            apply_service(args.base_directory)
        return 0
    except Exception as exc:
        # No network responses, credentials, or attacker-controlled command strings in logs.
        print('Update unavailable/failed: ' + type(exc).__name__, file=sys.stderr)
        return 1
