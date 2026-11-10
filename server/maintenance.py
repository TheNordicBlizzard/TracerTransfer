"""Offline-capable maintenance; SQLite snapshots never copy an active WAL blindly."""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
import stat
import tempfile
import time
import urllib.request
import uuid


class MaintenanceError(RuntimeError):
    pass


def _regular(path: Path) -> bool:
    info = path.lstat()
    return stat.S_ISREG(info.st_mode) and not (getattr(info, 'st_file_attributes', 0) & 0x400)


def _safe_directory(path: Path) -> Path:
    path = path.absolute()
    for parent in (path, *path.parents):
        info = parent.lstat()
        if not stat.S_ISDIR(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise MaintenanceError('Directory links/junctions are not allowed')
    return path


def _sha(path: Path) -> str:
    if not _regular(path):
        raise MaintenanceError('Only regular files are permitted')
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def _json_atomic(path: Path, payload: dict) -> None:
    fd, temp = tempfile.mkstemp(prefix='.maintenance-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        Path(temp).unlink(missing_ok=True)


def _snapshot_database(source: Path, target: Path) -> None:
    if not _regular(source):
        raise MaintenanceError('Missing regular application database')
    deadline = time.monotonic() + 60
    def progress(_status, _remaining, _total):
        if time.monotonic() > deadline:
            raise MaintenanceError('SQLite snapshot timed out')
    with closing(sqlite3.connect(source.as_uri()+'?mode=ro', uri=True, timeout=10)) as original:
        with closing(sqlite3.connect(target)) as snapshot:
            original.backup(snapshot, pages=256, progress=progress, sleep=.05)
            # The backup is a standalone database; never ship live WAL/SHM files.
            if snapshot.execute('PRAGMA journal_mode=DELETE').fetchone()[0]!='delete':
                raise MaintenanceError('Cannot finalize standalone database snapshot')
            if snapshot.execute('PRAGMA quick_check').fetchall() != [('ok',)]:
                raise MaintenanceError('SQLite integrity check failed')



def backup(data_dir, backup_dir, *, keep=7, min_free_bytes=256*1024*1024) -> Path:
    """Snapshot DB plus available immutable payloads; reject changing files."""
    data = _safe_directory(Path(data_dir))
    destination = Path(backup_dir).absolute()
    if destination == data or data in destination.parents:
        raise MaintenanceError('Backups must be outside the live data tree')
    if not isinstance(keep, int) or keep < 1 or min_free_bytes < 0:
        raise ValueError('Invalid retention/free-space policy')
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    _safe_directory(destination)
    if shutil.disk_usage(destination).free < min_free_bytes:
        raise MaintenanceError('Insufficient backup space')
    staging = Path(tempfile.mkdtemp(prefix='.incomplete-', dir=destination))
    manifest = {'tracer_backup_version':1, 'created_at':datetime.now(timezone.utc).isoformat(),
                'source':str(data), 'files':[], 'coverage':'Consistent SQLite snapshot and stable files available at copy time; not a transactional snapshot of an external industrial consumer.'}
    try:
        _snapshot_database(data/'tracer.sqlite3', staging/'tracer.sqlite3')
        required = []
        with closing(sqlite3.connect((staging/'tracer.sqlite3').as_uri()+'?mode=ro',uri=True)) as snapshot:
            if snapshot.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='deliveries'").fetchone():
                for identifier,digest in snapshot.execute("SELECT id,sha256 FROM deliveries WHERE status IN ('pending','conflict','uncertain')"):
                    if not re.fullmatch('[a-f0-9]{32}',identifier) or not re.fullmatch('[a-f0-9]{64}',digest):
                        raise MaintenanceError('Unsafe queued payload identity')
                    required.append((Path('spool')/identifier,digest))
        for root, directories, files in os.walk(data, followlinks=False):
            base = Path(root)
            directories[:] = sorted(d for d in directories if d not in {'staging','__pycache__'})
            for directory in directories:
                _safe_directory(base/directory)
            for name in sorted(files):
                if name in {'tracer.sqlite3','tracer.sqlite3-wal','tracer.sqlite3-shm','server.lock','maintenance-status.json'} or name.startswith('audit.log') or name.startswith('.maintenance-'):
                    continue
                source = base/name
                if not _regular(source):
                    raise MaintenanceError('Unsafe backup input')
                before = source.stat()
                relative = source.relative_to(data)
                target = staging/relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
                after = source.stat()
                if (before.st_size,before.st_mtime_ns,before.st_ino) != (after.st_size,after.st_mtime_ns,after.st_ino) or _sha(source) != _sha(target):
                    raise MaintenanceError('Source changed while backing up; retry required')
        for relative,digest in required:
            candidate=staging/relative
            if not candidate.exists() or _sha(candidate)!=digest:
                raise MaintenanceError('Pending payload missing or inconsistent in backup')
        manifest['required_payloads']=[p.as_posix() for p,_ in required]
        for file in sorted(staging.rglob('*')):
            if file.is_file():
                manifest['files'].append({'path':file.relative_to(staging).as_posix(),'size':file.stat().st_size,'sha256':_sha(file)})
        _json_atomic(staging/'manifest.json',manifest)
        final = destination/('backup-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')+'-'+uuid.uuid4().hex[:8])
        staging.rename(final)
        eligible=[]
        for candidate in destination.glob('backup-*'):
            if candidate.is_symlink() or not candidate.is_dir():
                continue
            _safe_directory(candidate)
            try:
                report=json.loads((candidate/'manifest.json').read_text(encoding='utf-8'))
                if report.get('tracer_backup_version')==1 and report.get('source')==str(data):
                    eligible.append(candidate)
            except (OSError,ValueError):
                continue
        for old in sorted(eligible, key=lambda p:p.name, reverse=True)[keep:]:
            shutil.rmtree(old)
        _json_atomic(data/'maintenance-status.json',{'last_backup':str(final),'last_backup_at':manifest['created_at'],'backup_ok':True,'checked_at':datetime.now(timezone.utc).isoformat()})
        return final
    except BaseException as exc:
        if staging.exists():
            shutil.rmtree(staging)
        try:
            previous=json.loads((data/'maintenance-status.json').read_text(encoding='utf-8'))
        except (OSError,ValueError):
            previous={}
        try:
            _json_atomic(data/'maintenance-status.json',{**previous,'backup_ok':False,'backup_error':type(exc).__name__,'checked_at':datetime.now(timezone.utc).isoformat()})
        except OSError:
            pass
        raise


def restore(backup_dir, destination) -> Path:
    """Restore only into a new directory; never overwrite a running installation."""
    source = _safe_directory(Path(backup_dir))
    target = Path(destination).absolute()
    if target.exists():
        raise MaintenanceError('Restore target must not exist')
    _safe_directory(target.parent)
    manifest=json.loads((source/'manifest.json').read_text(encoding='utf-8'))
    if manifest.get('tracer_backup_version') != 1 or not isinstance(manifest.get('files'),list):
        raise MaintenanceError('Invalid backup manifest')
    entries=[]
    for entry in manifest['files']:
        relative=PurePosixPath(entry['path'])
        if relative.is_absolute() or '..' in relative.parts or ':' in entry['path'] or '\\' in entry['path'] or not relative.parts:
            raise MaintenanceError('Unsafe restore path')
        path=source/Path(*relative.parts)
        _safe_directory(path.parent)
        if not re.fullmatch('[a-f0-9]{64}',entry['sha256']) or _sha(path)!=entry['sha256'] or path.stat().st_size!=entry['size']:
            raise MaintenanceError('Backup checksum mismatch')
        entries.append((path,relative,entry['sha256']))
    if not any(str(relative)=='tracer.sqlite3' for _,relative,_ in entries):
        raise MaintenanceError('Database missing from backup')
    target.mkdir(mode=0o700)
    try:
        for path,relative,digest in entries:
            out=target/Path(*relative.parts)
            out.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(path,out)
            if _sha(out)!=digest:
                raise MaintenanceError('Restored checksum mismatch')
        with closing(sqlite3.connect((target/'tracer.sqlite3').as_uri()+'?mode=ro',uri=True)) as db:
            if db.execute('PRAGMA quick_check').fetchall()!=[('ok',)]:
                raise MaintenanceError('Restored database failed integrity check')

        return target
    except BaseException:
        shutil.rmtree(target)
        raise


def check(data_dir, *, health_url='http://127.0.0.1:8765/health', min_free_bytes=256*1024*1024) -> dict:
    data=_safe_directory(Path(data_dir))
    try:
        old=json.loads((data/'maintenance-status.json').read_text(encoding='utf-8'))
    except (OSError,ValueError):
        old={}
    report={**old,'checked_at':datetime.now(timezone.utc).isoformat(), 'free_bytes':shutil.disk_usage(data).free,'health_ok':False}
    try:
        with urllib.request.urlopen(health_url,timeout=5) as response:
            report['health_ok']=response.status==200 and json.loads(response.read(1024))=={'ok':True}
    except (OSError,ValueError):
        pass
    report['space_ok']=report['free_bytes']>=min_free_bytes
    _json_atomic(data/'maintenance-status.json',report)
    return report


def scheduled(data_dir, backup_dir, *, keep=7):
    report=check(data_dir)
    try:
        last=datetime.fromisoformat(report['last_backup_at'])
        due=not report.get('backup_ok') or datetime.now(timezone.utc)-last>=timedelta(days=1)
    except (KeyError,ValueError,TypeError):
        due=True
    if due:
        try:
            backup(data_dir,backup_dir,keep=keep)
        except Exception as exc:
            report.update(backup_ok=False,backup_error=type(exc).__name__)
            _json_atomic(Path(data_dir)/'maintenance-status.json',report)
            raise
        report=check(data_dir)
    return report


def main(argv=None):
    parser=argparse.ArgumentParser(description='TracerTransfer maintenance (no credentials on command line)')
    parser.add_argument('operation',choices=['backup','restore','check','scheduled'])
    parser.add_argument('--data-dir',required=True,type=Path)
    parser.add_argument('--backup-dir',type=Path)
    parser.add_argument('--destination',type=Path)
    parser.add_argument('--keep',type=int,default=7)
    args=parser.parse_args(argv)
    if args.operation=='scheduled':
        if args.backup_dir is None:parser.error('--backup-dir required')
        report=scheduled(args.data_dir,args.backup_dir,keep=args.keep)
        print(json.dumps(report));return 0 if report['health_ok'] and report['space_ok'] else 1
    elif args.operation=='backup':
        if args.backup_dir is None:parser.error('--backup-dir required')
        print(backup(args.data_dir,args.backup_dir,keep=args.keep))
    elif args.operation=='restore':
        if args.backup_dir is None or args.destination is None:parser.error('--backup-dir and --destination required')
        print(restore(args.backup_dir,args.destination))
    else:
        report=check(args.data_dir);print(json.dumps(report));return 0 if report['health_ok'] and report['space_ok'] else 1
    return 0


if __name__=='__main__':
    raise SystemExit(main())
