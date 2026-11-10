#!/usr/bin/python3
"""Forced-command receiver. No shell evaluation; archives are validated before publication."""
from __future__ import annotations
import contextlib
import fcntl
import gzip
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import select
import shutil
import sqlite3
import stat
import sys
import tarfile
import tempfile
import time
import uuid
from datetime import datetime, timezone

ROOT = Path('/var/backups/tracer-transfer-auto')
MAX_ARCHIVE = 2 * 1024**3  # Also bounded by the total retention budget.
MAX_EXPANDED = 4 * 1024**3
RESERVE = 8 * 1024**3
MAX_TOTAL = 2 * 1024**3
KEEP = 7
NAME = re.compile(r'auto-[0-9]{8}T[0-9]{12}Z-[a-f0-9]{32}\.tar\.gz')


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def fsync_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_json(path, report):
    fd, temporary = tempfile.mkstemp(prefix='.status-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(report, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        fsync_dir(path.parent)
    finally:
        Path(temporary).unlink(missing_ok=True)


def private_directory(path):
    for parent in (path, *path.parents):
        info = parent.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_mode & 0o022:
            raise ValueError('unsafe directory ancestry')
    info = path.stat()
    if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise ValueError('private destination required')


def valid_path(name):
    p = PurePosixPath(name)
    if not name or name != p.as_posix() or p.is_absolute() or '..' in p.parts or ':' in name or '\\' in name or any(ord(c) < 32 for c in name):
        raise ValueError('unsafe archive path')
    return p


def validate_restore(archive, workspace):
    """Manual exclusive extraction, exact manifest, hashes, isolated restore and SQLite."""
    extracted = workspace / 'snapshot'
    extracted.mkdir(mode=0o700)
    seen, total, files = set(), 0, {}
    # Parse fixed-size USTAR headers directly: tarfile's PAX/GNU extension parser
    # may allocate attacker-declared multi-gigabyte metadata before yielding a member.
    with gzip.open(archive, 'rb') as tar:
        while True:
            header = tar.read(512)
            if header == b'\0' * 512:
                if tar.read(512) != b'\0' * 512:
                    raise ValueError('invalid tar end marker')
                tail = tar.read(10241)
                if len(tail) > 10240 or any(tail):
                    raise ValueError('unexpected tar trailing data')
                break
            if len(header) != 512:
                raise ValueError('truncated tar header')
            member = tarfile.TarInfo.frombuf(header, 'utf-8', 'strict')
            relative = valid_path(member.name)
            if member.name in seen or len(seen) >= 100000:
                raise ValueError('duplicate or excessive entries')
            seen.add(member.name)
            if member.type not in (tarfile.REGTYPE, tarfile.AREGTYPE) or member.size < 0:
                raise ValueError('only regular USTAR files allowed')
            total += member.size
            if total > MAX_EXPANDED or (member.name == 'manifest.json' and member.size > 8 * 1024**2):
                raise ValueError('expanded size limit')
            if shutil.disk_usage(workspace).free < RESERVE + 2 * member.size:
                raise ValueError('restore reserve')
            target = extracted.joinpath(*relative.parts)
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with target.open('xb') as out:
                remaining = member.size
                while remaining:
                    chunk = tar.read(min(remaining, 1024 * 1024))
                    if not chunk:
                        raise ValueError('truncated member')
                    out.write(chunk)
                    remaining -= len(chunk)
            padding = (-member.size) % 512
            if len(tar.read(padding)) != padding:
                raise ValueError('truncated padding')
            files[member.name] = (target.stat().st_size, sha(target))
    manifest = json.loads((extracted / 'manifest.json').read_text())
    if manifest.get('tracer_backup_version') != 1 or not isinstance(manifest.get('files'), list):
        raise ValueError('manifest version')
    expected = {}
    for entry in manifest['files']:
        name = entry['path']
        valid_path(name)
        if name in expected or name == 'manifest.json' or type(entry['size']) is not int or entry['size'] < 0 or not re.fullmatch('[a-f0-9]{64}', entry['sha256']):
            raise ValueError('invalid manifest entry')
        expected[name] = (entry['size'], entry['sha256'])
    if {k: v for k, v in files.items() if k != 'manifest.json'} != expected or 'tracer.sqlite3' not in expected:
        raise ValueError('manifest inventory or digest mismatch')
    if shutil.disk_usage(workspace).free < RESERVE + sum(size for size, _ in expected.values()):
        raise ValueError('aggregate restore reserve')
    # The same application restore routine, installed root-owned from the approved source.
    from tracer_backup_maintenance import restore
    restored = restore(extracted, workspace / 'restored')
    with contextlib.closing(sqlite3.connect((restored / 'tracer.sqlite3').as_uri() + '?mode=ro', uri=True)) as db:
        if db.execute('PRAGMA quick_check').fetchall() != [('ok',)]:
            raise ValueError('SQLite integrity')
    return {'sqlite_quick_check': 'ok', 'manifest_files': len(expected),
            'restored_files': len(expected), 'restored_database_sha256': sha(restored / 'tracer.sqlite3'),
            'expanded_bytes': total, 'temporary_restore_removed': True}


def retention(root, *, keep=KEEP, budget=MAX_TOTAL):
    eligible = sorted((p for p in root.iterdir() if NAME.fullmatch(p.name) and stat.S_ISREG(p.lstat().st_mode)), reverse=True)
    retained, used = [], 0
    for path in eligible:
        size = path.stat().st_size
        if len(retained) < keep and used + size <= budget:
            retained.append(path.name)
            used += size
        else:
            path.unlink()
    fsync_dir(root)
    return {'retained_count': len(retained), 'retained_bytes': used}


def receive(stream_fd, root=ROOT):
    os.umask(0o077)
    private_directory(root)
    with (root / '.receive.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with tempfile.TemporaryDirectory(prefix='.incoming-', dir=root) as temp:
            workspace = Path(temp)
            incoming = workspace / 'archive.tar.gz'
            deadline, size = time.monotonic() + 600, 0
            with incoming.open('xb') as out:
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0 or not select.select([stream_fd], [], [], min(remaining, 30))[0]:
                        raise ValueError('upload timeout')
                    chunk = os.read(stream_fd, 1024 * 1024)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > MAX_ARCHIVE or shutil.disk_usage(root).free < RESERVE + len(chunk):
                        raise ValueError('archive or free-space limit')
                    out.write(chunk)
                out.flush()
                os.fsync(out.fileno())
            proof = validate_restore(incoming, workspace)
            digest = sha(incoming)
            now = datetime.now(timezone.utc)
            final = root / ('auto-' + now.strftime('%Y%m%dT%H%M%S%fZ-') + uuid.uuid4().hex + '.tar.gz')
            # Exclusive publication; no overwrite even if an identifier collided.
            os.link(incoming, final)
            fsync_dir(root)
        report = {'ok': True, 'checked_at': now.isoformat(), 'archive': str(final),
                  'sha256': digest, 'bytes': size, 'restore': proof, **retention(root)}
        atomic_json(root / 'last-status.json', report)
        return report


def main():
    if os.environ.get('SSH_ORIGINAL_COMMAND') != 'receive-v1':
        print('Denied: backup receive-v1 only', file=sys.stderr)
        return 64
    try:
        report = receive(sys.stdin.fileno())
    except Exception as exc:
        # No archive content, paths supplied by the peer, or secrets in errors.
        print('Backup rejected: ' + type(exc).__name__, file=sys.stderr)
        return 1
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
