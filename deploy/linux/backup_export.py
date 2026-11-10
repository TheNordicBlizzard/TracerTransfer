#!/usr/bin/python3
"""Daily consistent application checkpoint sent using a dedicated SSH identity."""
from __future__ import annotations
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tarfile
import tempfile
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from server import maintenance
from server.maintenance import _json_atomic
from configuration import get_backup_ssh_host

ROOT = Path('/var/backups/tracer-transfer-export')
STATUS = ROOT / 'last-status.json'
HOST = get_backup_ssh_host('tracer-backup@192.0.2.20')


def ssh_arguments(command='receive-v1'):
    credential = Path(os.environ['CREDENTIALS_DIRECTORY']) / 'backup_ssh_key'
    info = credential.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid not in (0, os.geteuid()) or info.st_mode & 0o007:
        raise ValueError('unsafe SSH credential')
    return ['/usr/bin/ssh', '-F', '/dev/null', '-i', str(credential),
            '-o', 'UserKnownHostsFile=/etc/tracer-transfer/backup/known_hosts',
            '-o', 'StrictHostKeyChecking=yes', '-o', 'IdentitiesOnly=yes',
            '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=15',
            '-o', 'ServerAliveInterval=15', '-o', 'ServerAliveCountMax=3',
            '-o', 'ClearAllForwardings=yes', '-o', 'RequestTTY=no', HOST, command]


def consistent_checkpoint(data_dir, destination):
    """Reuse the application snapshot without changing its local daily-backup status.

    This single-threaded exporter owns its interpreter. Redirect only the routine's
    maintenance-status publication to export scratch; manifest writes are unchanged.
    Never restore an old live status over another maintenance process's new result.
    """
    data, destination = Path(data_dir), Path(destination)
    writer = maintenance._json_atomic

    def export_writer(path, payload):
        if path == data / 'maintenance-status.json':
            path = destination.parent / 'snapshot-status.json'
        return writer(path, payload)

    maintenance._json_atomic = export_writer
    try:
        return maintenance.backup(data, destination, keep=1)
    finally:
        maintenance._json_atomic = writer


def main():
    os.umask(0o077)
    report = {'ok': False, 'checked_at': datetime.now(timezone.utc).isoformat()}
    with (ROOT / '.export.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            with tempfile.TemporaryDirectory(prefix='.export-', dir=ROOT) as temporary:
                work = Path(temporary)
                snapshot = consistent_checkpoint('/var/lib/tracer-transfer', work / 'snapshots')
                archive = work / 'checkpoint.tar.gz'
                with tarfile.open(archive, 'x:gz', dereference=False, format=tarfile.USTAR_FORMAT) as tar:
                    for source in sorted(snapshot.rglob('*')):
                        if source.is_symlink():
                            raise ValueError('linked snapshot input')
                        if source.is_file():
                            tar.add(source, arcname=source.relative_to(snapshot).as_posix(), recursive=False)
                size = archive.stat().st_size
                if size > 2 * 1024**3:
                    raise ValueError('archive exceeds receiver retention budget')
                with archive.open('rb') as stream:
                    digest = hashlib.file_digest(stream, 'sha256').hexdigest()
                    stream.seek(0)
                    result = subprocess.run(ssh_arguments(), stdin=stream, capture_output=True, timeout=900, check=True)
                if len(result.stdout) > 65536:
                    raise ValueError('oversized acknowledgement')
                remote = json.loads(result.stdout)
                if remote.get('ok') is not True or remote.get('sha256') != digest or remote.get('bytes') != size or remote.get('restore', {}).get('sqlite_quick_check') != 'ok':
                    raise ValueError('remote verification failed')
                report.update(ok=True, sha256=digest, bytes=size, remote=remote,
                              credential_source='systemd LoadCredential', service_uid=os.geteuid())
        except Exception as exc:
            report['error_type'] = type(exc).__name__
            _json_atomic(STATUS, report)
            print(json.dumps(report, sort_keys=True))
            return 1
        _json_atomic(STATUS, report)
        print(json.dumps(report, sort_keys=True))
        return 0


if __name__ == '__main__':
    raise SystemExit(main())
