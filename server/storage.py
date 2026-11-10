"""Bounded streaming uploads with hash verification and atomic publication."""
import asyncio
import errno
import hashlib
import os
import re
import shutil
import stat
import tempfile
import threading
import uuid
from pathlib import Path
from fastapi import HTTPException
from server.database import utcnow


def validate_filename(name):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_. -]{0,119}', name) or name.endswith((' ', '.')):
        raise HTTPException(400, 'Invalid filename')
    if Path(name).suffix.lower() not in ('.oma', '.vca'):
        raise HTTPException(400, 'Only .oma and .vca files are accepted')
    stem = name.split('.')[0].rstrip(' ').upper()
    if stem in {'CON','PRN','AUX','NUL','CLOCK$','CONIN$','CONOUT$'} or re.fullmatch(r'(COM|LPT)[0-9]', stem):
        raise HTTPException(400, 'Reserved Windows filename')


def is_reparse(info):
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400))


def validate_output_directory(path):
    """Check ancestors before resolving away any junction/symlink."""
    directory = Path(path).absolute()
    for current in (directory, *directory.parents):
        info = current.lstat()
        if is_reparse(info) or not stat.S_ISDIR(info.st_mode):
            raise ValueError('Output directory and ancestors must be real directories, not links/junctions')
    return directory


class Storage:
    def __init__(self, db, max_size, *, chunk_timeout=15, total_timeout=60, output_dir=None, delivery_mode='direct', min_free_bytes=256 * 1024 * 1024):
        if delivery_mode not in ('direct', 'queued'):
            raise ValueError('Invalid delivery mode')
        if delivery_mode == 'queued' and output_dir is not None:
            raise ValueError('Queued mode cannot publish to an output directory')
        self.delivery_mode = delivery_mode
        if type(min_free_bytes) is not int or min_free_bytes < 0:
            raise ValueError('Minimum free space must be a nonnegative integer')
        self.min_free_bytes = min_free_bytes
        if chunk_timeout <= 0 or total_timeout <= 0:
            raise ValueError('Upload timeouts must be positive')
        self.chunk_timeout, self.total_timeout = chunk_timeout, total_timeout
        self.db, self.max_size = db, max_size
        self.slots = threading.BoundedSemaphore(4)
        self.publish_lock = threading.Lock()
        self.staging = db.data_dir / 'staging'
        self.staging.mkdir(exist_ok=True)
        self.spool = db.data_dir / 'spool'
        self.spool.mkdir(exist_ok=True)
        self.output_dir = validate_output_directory(output_dir) if output_dir is not None else None
        if self.output_dir is not None:
            if self.output_dir.stat().st_dev != self.staging.stat().st_dev:
                raise ValueError('Output and staging must be on the same volume for atomic publication')
            if self.output_dir in (db.data_dir, self.staging):
                raise ValueError('Output directory must be separate from administrative data and staging')

    def ensure_space(self, required):
        try:
            available = shutil.disk_usage(self.staging).free
        except OSError:
            raise HTTPException(507, 'Cannot verify storage capacity') from None
        if available < self.min_free_bytes + required:
            raise HTTPException(507, 'Insufficient storage capacity')

    async def receive(self, request, device, name):
        validate_filename(name)
        expected = request.headers.get('x-content-sha256', '')
        if not re.fullmatch(r'[a-fA-F0-9]{64}', expected):
            raise HTTPException(400, 'Valid X-Content-SHA256 required')
        expected = expected.lower()
        length = request.headers.get('content-length')
        if length is not None:
            if not re.fullmatch(r'[0-9]{1,20}', length):
                raise HTTPException(400, 'Invalid Content-Length')
            length = int(length)
            if length == 0:
                raise HTTPException(400, 'Empty file')
            if length > self.max_size:
                raise HTTPException(413, 'File too large')
        if not self.slots.acquire(blocking=False):
            raise HTTPException(429, 'Upload capacity reached', headers={'Retry-After':'2'})
        temp = None
        try:
            # Reject before body reads and recheck chunks for external usage.
            self.ensure_space(length if length is not None else self.max_size)
            fd, temp = tempfile.mkstemp(prefix='upload-', dir=self.staging)
            digest, size = hashlib.sha256(), 0
            # Bound both stalled reads and clients trickling bytes forever.
            try:
                async with asyncio.timeout(self.total_timeout):
                    with os.fdopen(fd, 'wb') as out:
                        stream = request.stream().__aiter__()
                        while True:
                            try:
                                async with asyncio.timeout(self.chunk_timeout):
                                    chunk = await anext(stream)
                            except StopAsyncIteration:
                                break
                            size += len(chunk)
                            if size > self.max_size:
                                raise HTTPException(413, 'File too large')
                            if length is not None and size > length:
                                raise HTTPException(400, 'Content-Length mismatch')
                            self.ensure_space(len(chunk))
                            digest.update(chunk)
                            out.write(chunk)
                        out.flush()
                        os.fsync(out.fileno())
            except TimeoutError:
                raise HTTPException(408, 'Upload timed out') from None
            if size == 0:
                raise HTTPException(400, 'Empty file')
            if length is not None and size != length:
                raise HTTPException(400, 'Content-Length mismatch')
            if digest.hexdigest() != expected:
                raise HTTPException(422, 'SHA-256 mismatch')
            # SQLite contention and integrity rehashes must not block the loop.
            # A cancelled request must not delete staging while its worker uses it.
            publication = asyncio.create_task(asyncio.to_thread(
                self._publish, temp, device['id'], name, expected, size))
            try:
                return await asyncio.shield(publication)
            except asyncio.CancelledError:
                while not publication.done():
                    try:
                        await asyncio.shield(publication)
                    except asyncio.CancelledError:
                        continue
                    except Exception:
                        break
                # Retrieve any worker exception, but preserve request cancellation.
                if not publication.cancelled():
                    publication.exception()
                raise
        except OSError as error:
            if error.errno in {errno.ENOSPC, errno.EDQUOT}:
                raise HTTPException(507, 'Insufficient storage capacity') from None
            raise
        finally:
            try:
                if temp is not None:
                    Path(temp).unlink(missing_ok=True)
            finally:
                self.slots.release()

    @staticmethod
    def _integrity_matches(destination, expected, size):
        try:
            before = destination.lstat()
            if is_reparse(before) or not stat.S_ISREG(before.st_mode) or before.st_size != size:
                return False
            digest = hashlib.sha256()
            with destination.open('rb') as existing:
                opened = os.fstat(existing.fileno())
                if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
                    return False
                for chunk in iter(lambda: existing.read(128 * 1024), b''):
                    digest.update(chunk)
                after = os.fstat(existing.fileno())
            current = destination.lstat()
            return (not is_reparse(current)
                    and (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns)
                    == (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
                    == (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
                    and digest.hexdigest() == expected)
        except FileNotFoundError:
            return False

    def _destination(self, device_id, expected, name):
        if self.output_dir is None:
            destination = self.db.data_dir/'received'/device_id/expected/name
            destination.parent.mkdir(parents=True, exist_ok=True)
            return destination
        validate_output_directory(self.output_dir)
        candidate = self.output_dir / name
        if os.path.lexists(candidate):
            return candidate
        for entry in self.output_dir.iterdir():
            if entry.name.casefold() == name.casefold():
                return entry
        return candidate

    def _publish(self, temp, device_id, name, expected, size):
        if self.delivery_mode == 'queued':
            return self._enqueue(temp, device_id, name, expected, size)
        # Serialize publication/compensation even if SQLite itself auto-rolls back
        # on an I/O error. The normal rollback remains inside BEGIN IMMEDIATE.
        with self.publish_lock, self.db.connect(write=True) as c:
            if not c.execute('SELECT 1 FROM devices WHERE id=? AND enabled=1', (device_id,)).fetchone():
                raise HTTPException(401, 'Device revoked')
            row = c.execute('SELECT name FROM files WHERE device_id=? AND name=? COLLATE NOCASE AND sha256=?',
                            (device_id, name, expected)).fetchone()
            duplicate = row is not None
            name = row['name'] if row else name
            destination = self._destination(device_id, expected, name)
            created = False
            try:
                try:
                    # Atomic, same-volume and never overwrites an existing file.
                    os.link(temp, destination)
                    created = True
                except FileExistsError:
                    if not self._integrity_matches(destination, expected, size):
                        message = ('Filename conflict: existing content differs or is unsafe; original preserved'
                                   if self.output_dir is not None else 'Existing file failed integrity check')
                        raise HTTPException(409, message)
                    if self.output_dir is not None:
                        duplicate = True
                if row is None:
                    c.execute('INSERT INTO files(device_id,name,sha256,size,received_at) VALUES(?,?,?,?,?)',
                              (device_id, name, expected, size, utcnow()))
                # Commit here so commit failures are covered by compensation,
                # before the context manager releases the SQLite write lock.
                c.commit()
            except BaseException:
                if created and destination.exists() and os.path.samefile(temp, destination):
                    destination.unlink()
                raise
            return {'sha256': expected, 'duplicate': duplicate, 'name': name}

    def _enqueue(self, temp, device_id, name, expected, size):
        with self.publish_lock, self.db.connect(write=True) as c:
            if not c.execute("SELECT 1 FROM devices d JOIN activations a ON a.id=d.activation_id AND a.device_id=d.id "
                             "WHERE d.id=? AND d.enabled=1 AND d.role='sender' AND a.revoked=0", (device_id,)).fetchone():
                raise HTTPException(403, 'Activated sender required')
            row = c.execute('SELECT * FROM deliveries WHERE device_id=? AND name=? COLLATE NOCASE AND sha256=?',
                            (device_id, name, expected)).fetchone()
            if row:
                return {'receipt_id': row['id'], 'sha256': row['sha256'], 'name': row['name'],
                        'duplicate': True, 'status': row['status']}
            identifier = uuid.uuid4().hex
            destination = self.spool / identifier
            created = False
            try:
                os.link(temp, destination)
                created = True
                if os.name != 'nt':
                    # Persist the new directory entry before SQLite promises a receipt.
                    directory_fd = os.open(self.spool, os.O_RDONLY | os.O_DIRECTORY)
                    try:
                        os.fsync(directory_fd)
                    finally:
                        os.close(directory_fd)
                c.execute('INSERT INTO deliveries(id,device_id,name,sha256,size,received_at) VALUES(?,?,?,?,?,?)',
                          (identifier, device_id, name, expected, size, utcnow()))
                c.commit()
            except BaseException:
                if created and destination.exists() and os.path.samefile(temp, destination):
                    destination.unlink()
                raise
            return {'receipt_id': identifier, 'sha256': expected, 'name': name, 'duplicate': False, 'status': 'pending'}
