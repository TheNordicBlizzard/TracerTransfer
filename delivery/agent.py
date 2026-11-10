"""Authenticated HTTPS pull agent. No exactly-once claim for an external consumer.

The journal commits publishing BEFORE the atomic no-replace link. If the consumer
removed the file during a crash window, recovery reports uncertain, never replays.
Administrative data/staging must have private ACLs; output must not be writable by
untrusted identities. Hardlinked output retains the staging ACL (installer supplies
read access for existing FreeForm consumer identities explicitly).
"""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import ssl
import stat
import tempfile
import threading
import time
from urllib.parse import urlsplit
import uuid

import httpx
from delivery.state import State, atomic_json, protect_token, unprotect_token
from configuration import get_default_server_url

DEFAULT_SERVER = get_default_server_url()


def real_directory(path):
    path = Path(path).absolute()
    for ancestor in (path, *path.parents):
        info = ancestor.lstat()
        if reparse(info) or not stat.S_ISDIR(info.st_mode):
            raise ValueError('Directory or ancestor is a link/junction')
    return path


def reparse(info):
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, 'st_file_attributes', 0) & 0x400)


def validate_filename(name):
    # Kept aligned with server.storage without importing FastAPI/server runtime.
    if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_. -]{0,119}', name) or name.endswith((' ', '.')):
        raise ValueError('invalid_name')
    if Path(name).suffix.lower() not in ('.oma', '.vca'):
        raise ValueError('invalid_name')
    stem = name.split('.')[0].rstrip(' ').upper()
    if stem in {'CON','PRN','AUX','NUL','CLOCK$','CONIN$','CONOUT$'} or re.fullmatch(r'(COM|LPT)[0-9]', stem):
        raise ValueError('invalid_name')


def http_client(server_url=DEFAULT_SERVER, ca_file=None, transport=None):
    u = urlsplit(server_url)
    if u.scheme != 'https' or not u.hostname or u.username or u.password or u.query or u.fragment or u.path not in ('', '/'):
        raise ValueError('HTTPS origin required')
    return httpx.Client(base_url=server_url.rstrip('/'),
                        verify=ssl.create_default_context(cafile=ca_file),
                        timeout=httpx.Timeout(30, connect=10), follow_redirects=False,
                        trust_env=False, transport=transport)


def activate(directory, bootstrap, *, transport=None):
    """Run inside actual service identity. Bootstrap is retained until success.

    Caller verifies private bootstrap DACL. Never accepts a code on a command line.
    The persisted pre-request secret makes a lost activation response retryable.
    """
    directory = real_directory(directory)
    code = bootstrap['code']
    if not isinstance(code, str) or not re.fullmatch(r'TT-[0-9A-F]{40}', code):
        raise ValueError('Invalid activation format')
    path = directory / 'identity.json'
    server = bootstrap.get('server_url', DEFAULT_SERVER)
    ca = bootstrap.get('ca_file')
    if path.exists():
        identity = json.loads(path.read_text(encoding='utf-8'))
        if identity['server_url'] != server or identity.get('ca_file') != ca:
            raise ValueError('Activation origin changed')
    else:
        identity = {'installation_id': uuid.uuid4().hex,
                    'protected_token': protect_token(secrets.token_urlsafe(32)),
                    'server_url': server, 'ca_file': ca, 'role': None}
        atomic_json(path, identity)
    secret = unprotect_token(identity['protected_token'])
    with http_client(server, ca, transport) as client:
        response = client.post('/api/activate', json={
            'code': code, 'installation_id': identity['installation_id'],
            'installation_secret': secret, 'name': bootstrap.get('name', 'Windows delivery')})
        response.raise_for_status()
        result = response.json()
    if result.get('role') != 'delivery':
        raise ValueError('Activation is not a delivery identity')
    identity.update(device_id=result['device_id'], activation_id=result['activation_id'], role='delivery')
    atomic_json(path, identity)
    return identity


class DeliveryAgent:
    def __init__(self, data_dir, output_dir, *, transport=None, max_size=20 * 1024 * 1024, state=None):
        self.data_dir = real_directory(data_dir)
        self.output = real_directory(output_dir)
        if self.output == self.data_dir or self.output in self.data_dir.parents or self.data_dir in self.output.parents:
            raise ValueError('Output and private data must be separate trees')
        self.staging = self.data_dir / 'staging'
        self.staging.mkdir(exist_ok=True)
        real_directory(self.staging)
        if self.staging.stat().st_dev != self.output.stat().st_dev:
            raise ValueError('Staging and output must be on the same volume')
        identity = json.loads((self.data_dir / 'identity.json').read_text(encoding='utf-8'))
        if identity.get('role') != 'delivery':
            raise ValueError('Delivery identity required')
        self.client = http_client(identity['server_url'], identity.get('ca_file'), transport)
        self.client.headers['Authorization'] = 'Bearer ' + unprotect_token(identity['protected_token'])
        self.state = state if state is not None else State(self.data_dir)
        self.max_size = max_size
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.update_requested = threading.Event()
        self.auto_update = False

    def close(self):
        self.stop_event.set()
        self.client.close()
        self.state.close()

    def destination(self, name):
        validate_filename(name)
        real_directory(self.output)
        found = [p for p in self.output.iterdir() if p.name.casefold() == name.casefold()]
        if len(found) > 1:
            raise ValueError('ambiguous_name')
        return found[0] if found else self.output / name

    @staticmethod
    def matches(path, expected, size):
        try:
            before = path.lstat()
            if reparse(before) or not stat.S_ISREG(before.st_mode) or before.st_size != size:
                return False
            digest = hashlib.sha256()
            with path.open('rb') as stream:
                opened = os.fstat(stream.fileno())
                if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                    return False
                for chunk in iter(lambda: stream.read(128 * 1024), b''):
                    digest.update(chunk)
                after = os.fstat(stream.fileno())
            current = path.lstat()
            signature = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns)
            return not reparse(current) and signature(before) == signature(after) == signature(current) and digest.hexdigest() == expected
        except OSError:
            # An unreadable/exclusively locked destination is not a verified
            # duplicate. Preserve it and surface conflict/uncertainty instead
            # of retrying forever without a terminal journal entry.
            return False

    def terminal(self, row, result, error=None):
        self.state.update(row['id'], phase='terminal', result=result, error=error, acked=0)

    def clean_stage(self, row):
        # Only a generated basename in our private staging tree. Never destinations.
        name = row.get('stage')
        if name and re.fullmatch(r'delivery-[a-zA-Z0-9_-]+\.part', name):
            real_directory(self.staging)
            path = self.staging / name
            try:
                info = path.lstat()
                if stat.S_ISREG(info.st_mode) and not reparse(info):
                    path.unlink()
            except FileNotFoundError:
                pass

    def recover(self):
        # A crash after terminal commit may precede staging unlink.
        for row in self.state.db.execute("SELECT * FROM deliveries WHERE phase='terminal' AND stage IS NOT NULL").fetchall():
            row = dict(row)
            self.clean_stage(row)
            self.state.update(row['id'], stage=None)
        for row in self.state.interrupted():
            if row['phase'] == 'publishing':
                try:
                    target = self.destination(row['name'])
                    if self.matches(target, row['sha256'], row['size']):
                        self.terminal(row, 'delivered')
                    else:
                        self.terminal(row, 'uncertain', 'publication_interrupted')
                except (ValueError, OSError):
                    self.terminal(row, 'uncertain', 'unsafe_destination')
            else:
                # Only pre-publication work is safe to retry on another valid lease.
                self.state.update(row['id'], phase='leased', stage=None)
            self.clean_stage(row)

    def ack(self, row):
        response = self.client.post('/api/delivery/ack/' + row['id'], json={
            'lease_token': row['lease_token'], 'sha256': row['sha256'],
            'result': row['result'], 'error': row['error']})
        if response.status_code in (404, 409, 410):
            # Expired/stale lease: retain terminal journal, refresh token only when
            # backend grants this receipt again. NEVER re-download/re-publish it.
            return False
        response.raise_for_status()
        self.state.update(row['id'], acked=1)
        return True

    def validate_item(self, item):
        if not isinstance(item.get('id'), str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', item['id']):
            raise ValueError('Invalid receipt id')
        if not isinstance(item.get('sha256'), str) or not re.fullmatch(r'[0-9a-f]{64}', item['sha256']):
            raise ValueError('Invalid checksum')
        if type(item.get('size')) is not int or not 0 < item['size'] <= self.max_size:
            raise ValueError('Invalid size')
        if not isinstance(item.get('lease_token'), str) or not 1 <= len(item['lease_token']) <= 1024:
            raise ValueError('Invalid lease')
        self.lease_valid(item)

    @staticmethod
    def lease_valid(item):
        until = datetime.fromisoformat(item['lease_until'].replace('Z', '+00:00'))
        if until.tzinfo is None or until <= datetime.now(timezone.utc):
            raise ValueError('lease_expired')

    def deliver(self, row):
        try:
            target = self.destination(row['name'])
        except ValueError:
            self.terminal(row, 'uncertain', 'invalid_name')
            return
        if target.exists() or target.is_symlink():
            if self.matches(target, row['sha256'], row['size']):
                self.terminal(row, 'delivered')
            else:
                self.terminal(row, 'conflict', 'destination_conflict')
            return
        fd, stage_path = tempfile.mkstemp(prefix='delivery-', suffix='.part', dir=self.staging)
        stage = Path(stage_path)
        self.state.update(row['id'], phase='downloading', stage=stage.name)
        row['stage'] = stage.name
        try:
            digest, length, started = hashlib.sha256(), 0, time.monotonic()
            with os.fdopen(fd, 'wb') as stream:
                with self.client.stream('GET', '/api/delivery/files/' + row['id'],
                                        headers={'X-Delivery-Lease': row['lease_token']}) as response:
                    response.raise_for_status()
                    for chunk in response.iter_bytes(128 * 1024):
                        length += len(chunk)
                        if length > row['size'] or time.monotonic() - started > 300:
                            raise ValueError('download_size_or_timeout')
                        digest.update(chunk)
                        stream.write(chunk)
                stream.flush()
                os.fsync(stream.fileno())
            if length != row['size'] or digest.hexdigest() != row['sha256']:
                raise ValueError('checksum_mismatch')
            self.lease_valid(row)
            real_directory(self.staging)
            target = self.destination(row['name'])
            # Commit before the first possible visibility to FreeForm.
            self.state.update(row['id'], phase='publishing')
            try:
                os.link(stage, target)  # Atomic no-replace; never os.replace here.
            except FileExistsError:
                if self.matches(target, row['sha256'], row['size']):
                    self.terminal(row, 'delivered')
                else:
                    self.terminal(row, 'conflict', 'destination_conflict')
            except OSError:
                self.terminal(row, 'uncertain', 'publication_failed')
                raise
            else:
                self.terminal(row, 'delivered')
        except ValueError as exc:
            code = str(exc)
            self.terminal(row, 'uncertain', code if code in {'checksum_mismatch','download_size_or_timeout','lease_expired'} else 'unsafe_destination')
        finally:
            # If journal commit fails, publishing remains durable for recovery.
            self.clean_stage(row)

    def run_once(self):
        self.recover()
        for row in self.state.pending():
            self.ack(row)
        response = self.client.post('/api/delivery/lease', json={'limit': 1})
        response.raise_for_status()
        items = response.json()['items']
        if not isinstance(items, list) or len(items) > 1:
            raise ValueError('Lease limit violated')
        for item in items:
            self.validate_item(item)
            row = self.state.get(item['id'])
            if row:
                if any(row[key] != item[key] for key in ('name','sha256','size')):
                    raise ValueError('Receipt identity changed')
                self.state.update(row['id'], lease_token=item['lease_token'], lease_until=item['lease_until'], acked=0)
            else:
                self.state.remember(item)
            row = self.state.get(item['id'])
            if row['phase'] != 'terminal':
                self.deliver(row)
            row = self.state.get(item['id'])
            if row['phase'] == 'terminal':
                self.ack(row)
        # Lease polling is the delivery heartbeat. /api/heartbeat is sender-only
        # in some backends, so never call it or upload/admin routes from this role.
        from updates import VERSION
        atomic_json(self.data_dir / 'status.json', {'status': 'poll_ok', 'at': datetime.now(timezone.utc).isoformat(),
                    'pid': os.getpid(), 'version': VERSION, 'pending_ack': len(self.state.pending())})
        return len(items)
