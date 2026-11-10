"""Explicit real Inno/self-EXE upgrade in a disposable per-user installation.

Build a real, distinct future version in scratch first. This tests the detached
applier, not public GitHub availability, real enrollment or clean Windows.
The old *test* process is terminated after the self-EXE handshake to exercise
waiting/recovery; production processes and industrial directories are untouched.
"""
import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import tomllib

from client.agent import Agent
from client.state import Config, protect_token, save_config, unprotect_token
from tests.installer_upgrade import inventory, product, startup_snapshot

from updates.core import verify_manifest

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def check_run(args, **kwargs):
    result = subprocess.run([str(x) for x in args], capture_output=True, timeout=120, **kwargs)
    if result.returncode:
        raise RuntimeError('Controlled test command failed: ' + Path(str(args[0])).name)
    return result


def stop_test_processes(executable):
    escaped = str(executable).replace("'", "''")
    script = ("@(Get-CimInstance Win32_Process -Filter \"Name='TracerCliente.exe'\" | "
              "Where-Object {$_.ExecutablePath -eq '" + escaped + "'} | "
              "Select-Object -ExpandProperty ProcessId)|ConvertTo-Json -Compress")
    raw = check_run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script]).stdout
    pids = json.loads(raw.decode('utf-8-sig')) if raw.strip() else []
    if isinstance(pids, int):
        pids = [pids]
    for pid in pids:
        subprocess.run(['taskkill.exe', '/PID', str(pid), '/T', '/F'], capture_output=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--allow-current-user-install', action='store_true', required=True)
    parser.add_argument('--future-root', type=Path, required=True)
    parser.add_argument('--signing-key', type=Path, required=True, help='Explicit private DPAPI signing-key path; never published')
    parser.add_argument('--scratch', type=Path, required=True)
    args = parser.parse_args()
    if product() or startup_snapshot():
        raise RuntimeError('Existing installation/startup preference: nothing changed')
    actual_default = Path(os.environ['LOCALAPPDATA']) / 'TracerTransfer/client'
    actual_install = Path(os.environ['LOCALAPPDATA']) / 'Programs/TracerTransfer/Client'
    if (actual_install / 'TracerCliente.exe').exists():
        raise RuntimeError('Existing default executable: nothing changed')
    original_profile = inventory(actual_default)
    original_startup = startup_snapshot()
    old_version = tomllib.loads((ROOT / 'pyproject.toml').read_text(encoding='utf-8'))['project']['version']
    new_version = tomllib.loads((args.future_root / 'pyproject.toml').read_text(encoding='utf-8'))['project']['version']
    from updates.core import version_tuple
    if version_tuple(new_version) <= version_tuple(old_version):
        raise ValueError('Future QA build must have a strictly newer version')
    old_record = json.loads((ROOT / f'dist/installers/{old_version}/TracerTransfer-Cliente-{old_version}-windows-x64-pilot-unsigned.json').read_text())
    new_record = json.loads((args.future_root / f'dist/installers/{new_version}/TracerTransfer-Cliente-{new_version}-windows-x64-pilot-unsigned.json').read_text())
    for record in (old_record, new_record):
        assert 'updater' not in record
        for kind in ('installer', 'client'):
            assert digest(record[kind]['path']) == record[kind]['sha256']
    args.scratch.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix='tracer-auto-installed-', dir=args.scratch))
    local = work / 'local'; local.mkdir()
    install = local / 'Programs/TracerTransfer/Client'
    data = local / 'TracerTransfer/client'
    source = work / 'empty-source'; source.mkdir()
    env = {**os.environ, 'LOCALAPPDATA': str(local)}
    secret = secrets.token_urlsafe(32)
    # Local preservation fixture, intentionally NOT an enrolled production device.
    config = Config(source_dir=str(source), paused=True, poll_seconds=.2,
                    device_id='QA-only', installation_id='QA-only',
                    protected_token=protect_token(secret))
    save_config(data, config)
    snapshot = data / 'spool' / 'fixture'; snapshot.parent.mkdir()
    snapshot.write_bytes(b'SYNTHETIC LOCAL QUEUE - NEVER INDUSTRIAL\r\n')
    agent = Agent(data)
    # Agent startup removes unreferenced snapshots: create after initialization.
    snapshot.write_bytes(b'SYNTHETIC LOCAL QUEUE - NEVER INDUSTRIAL\r\n')
    row = ('QA-source', digest(snapshot), 'QA-only.oma', str(snapshot), 'pending')
    agent.db.execute('INSERT INTO queue(path,sha,name,snapshot,state) VALUES(?,?,?,?,?)', row)
    agent.db.commit(); agent.close()
    before_config = (data / 'config.json').read_bytes()
    parent = helper = None
    installed = False
    report = {}
    try:
        installed = True
        check_run([old_record['installer']['path'], '/VERYSILENT', '/SUPPRESSMSGBOXES',
                   '/NORESTART', '/SP-', '/TASKS=', '/DIR=' + str(install)], env=env)
        assert product()['DisplayVersion'] == old_version
        assert not (install / 'TracerAtualizador.exe').exists()
        origin = install / 'deployment.json'
        origin.write_text(json.dumps({'server_url': 'https://qa.invalid'}))
        before_origin = origin.read_bytes()
        manifest = work / 'update-manifest.json'
        check_run([sys.executable, str(ROOT / 'scripts/sign_update_release.py'),
                   '--version', new_version, '--client', new_record['installer']['path'],
                   '--commit', new_record['build']['commit'], '--key-file', str(args.signing_key),
                   '--output', str(manifest)], cwd=ROOT)
        payload = verify_manifest(manifest.read_bytes())
        assert payload['version'] == new_version
        parent = subprocess.Popen([str(install / 'TracerCliente.exe'), '--headless',
                                   '--data-dir', str(data)], env=env,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(2)
        assert parent.poll() is None
        request = data / 'updates/request-real-installed'; request.mkdir(parents=True)
        (request / 'request.json').write_text(json.dumps({'schema': 1, 'pid': parent.pid}))
        (request / 'manifest.json').write_bytes(manifest.read_bytes())
        shutil.copy2(new_record['installer']['path'], request / payload['artifacts']['client']['name'])
        shutil.copy2(install / 'TracerCliente.exe', request / 'TracerCliente.exe')
        helper = subprocess.Popen([str(request / 'TracerCliente.exe'), '--apply-update', str(request)],
                                  env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        deadline = time.monotonic() + 30
        while not (request / 'ready').exists() and time.monotonic() < deadline:
            if helper.poll() is not None:
                raise RuntimeError('Frozen helper rejected real signed request')
            time.sleep(.1)
        assert (request / 'ready').is_file()
        assert helper.poll() is None and product()['DisplayVersion'] == old_version
        # Only the Popen tree created above. Simulates shutdown/crash in this lab.
        check_run(['taskkill.exe', '/PID', str(parent.pid), '/T', '/F'])
        parent.wait(timeout=20)
        _, error = helper.communicate(timeout=150)
        if helper.returncode:
            raise RuntimeError('Frozen helper application failed; inspect private state: ' + error.decode(errors='replace'))
        state = json.loads((request / 'state.json').read_text())
        assert state == {'phase': 'complete', 'version': new_version}
        assert product()['DisplayVersion'] == new_version
        for kind, name in [('client', 'TracerCliente.exe')]:
            assert digest(install / name) == new_record[kind]['sha256']
            assert digest(request / 'backup' / name) == old_record[kind]['sha256']
        assert not (install / 'TracerAtualizador.exe').exists()
        assert origin.read_bytes() == before_origin
        assert (data / 'config.json').read_bytes() == before_config
        assert unprotect_token(json.loads(before_config)['protected_token']) == secret
        with closing(sqlite3.connect(data / 'queue.sqlite3')) as db:
            assert db.execute('SELECT path,sha,name,snapshot,state FROM queue').fetchall() == [row]
        assert digest(snapshot) == row[1]
        report = {'result': 'PASS', 'from': old_version, 'to': new_version + '-QA-only',
                  'signed_with_real_product_pin': True, 'real_inno_installer': True,
                  'real_frozen_self_executable': True, 'installed_origin_preserved': True, 'parent_handshake_and_wait': True,
                  'first_binary_backups_verified': True, 'queue_identity_snapshot_preserved': True,
                  'new_client_hash_verified': True, 'fourth_executable_absent': True,
                  'production_device_enrollment_tested': False,
                  'public_github_discovery_tested': False, 'clean_windows': False,
                  'controlled_parent_termination': True, 'work_dir': str(work)}
    finally:
        stop_test_processes(install / 'TracerCliente.exe')
        if helper and helper.poll() is None:
            helper.terminate(); helper.wait(timeout=20)
        record = product()
        if installed and record:
            uninstaller = Path(record['UninstallString'].strip('"'))
            if uninstaller.parent != install or not uninstaller.name.startswith('unins'):
                raise RuntimeError('Unexpected uninstaller; refusing cleanup outside test installation')
            check_run([uninstaller, '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART'], env=env)
            deadline = time.monotonic() + 20
            while product() and time.monotonic() < deadline:
                time.sleep(.1)
        assert product() is None
        assert inventory(actual_default) == original_profile
        assert startup_snapshot() == original_startup
    report['installation_removed'] = True
    report['real_default_profile_and_autostart_preserved'] = True
    (work / 'result.json').write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
