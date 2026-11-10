"""Root-only Linux code updater; never restores application data.

Archive contract (USTAR gzip, regular files only, no directory headers):
server/*.py, updates/*.py, configuration.py, requirements.txt and
server-release.json. The latter has exactly schema=1, version,
requirements_sha256, database_compatible=true, migrations='none'. Dependencies
must match the already prepared root-owned venv; no pip/build hooks run here.
Install layout: root/releases/<version>, root/current -> releases/<version>,
root/venv (shared immutable dependencies), root/cache and root/update-state.json.
Data, identities, spool and backups MUST remain outside this tree.
An unfinished journal blocks further updates pending operator reconciliation.
Active caddy/cloudflared require root-owned root/update-health.json, a mapping
from their unit names to operator-approved /health URLs (HTTPS or loopback HTTP).
All active probes must return bounded JSON {"ok": true}; absence blocks before
stopping services. This adds no routes/listeners and never publishes admin.
Timer installation is not enablement. Legacy unversioned installs are rejected;
bootstrap, dependency changes and schema migrations require manual approval.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import gzip
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import uuid

from updates.core import (prepare_update, verify_artifact, verify_manifest,
                          version_tuple, UpdateRejected, UpdateUnavailable)

MAX_FILES = 2048
MAX_EXPANDED = 128 * 1024 * 1024
MAX_FILE = 16 * 1024 * 1024
CHAIN = ('tracer-transfer.service', 'tracer-proxy.service', 'tracer-cloudflared.service')


def require_root(*, platform=None, uid=None):
    platform = sys.platform if platform is None else platform
    uid = getattr(os, 'geteuid', lambda: -1)() if uid is None else uid
    if platform != 'linux' or uid != 0:
        raise ValueError('Server updates require Linux and effective UID 0')


def archive_path(name):
    if (not isinstance(name, str) or not name or len(name) > 240
            or '\\' in name or ':' in name or any(ord(c) < 32 for c in name)
            or any(p in ('', '.', '..') for p in name.split('/'))):
        raise ValueError('Unsafe archive path')
    path = PurePosixPath(name)
    if path.is_absolute():
        raise ValueError('Absolute archive path')
    if not (name in {'configuration.py', 'requirements.txt', 'server-release.json'}
            or (len(path.parts) == 2 and path.parts[0] in {'server', 'updates'}
                and path.suffix == '.py')):
        raise ValueError('File outside code allowlist')
    return path


def protected(path, *, directory=True):
    """Do not resolve away symlinks; even writable ancestors are forbidden."""
    path = Path(os.path.abspath(path))
    for entry in reversed((path, *path.parents)):
        info = entry.lstat()
        if (stat.S_ISLNK(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022
                or (entry != path and not stat.S_ISDIR(info.st_mode))):
            raise ValueError('Update paths and ancestors must be root-owned, not group/world-writable, without links')
    info = path.lstat()
    if directory and not stat.S_ISDIR(info.st_mode):
        raise ValueError('Expected protected directory')
    if not directory and (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1):
        raise ValueError('Expected protected regular file')
    return path


def unpack(archive, destination):
    """Parse physical headers before tarfile can allocate PAX/GNU metadata."""
    seen, total = set(), 0
    with gzip.open(archive, 'rb') as stream:
        while True:
            header = stream.read(512)
            if len(header) != 512:
                raise ValueError('Truncated tar header')
            if header == bytes(512):
                if stream.read(512) != bytes(512):
                    raise ValueError('Invalid tar terminator')
                # Bound and reject appended archives/nonzero trailing data.
                remaining = stream.read(10241)
                if len(remaining) > 10240 or any(remaining):
                    raise ValueError('Unexpected tar trailer')
                break
            item = tarfile.TarInfo.frombuf(header, 'utf-8', 'strict')
            if item.type not in (tarfile.REGTYPE, tarfile.AREGTYPE) or item.linkname:
                raise ValueError('Only regular files allowed; no PAX/GNU headers or links')
            path = archive_path(item.name)
            if item.name in seen or len(seen) >= MAX_FILES:
                raise ValueError('Duplicate file or file count exceeded')
            seen.add(item.name)
            total += item.size
            if item.size < 0 or item.size > MAX_FILE or total > MAX_EXPANDED:
                raise ValueError('Archive expansion limit exceeded')
            target = Path(destination).joinpath(*path.parts)
            target.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
            with target.open('xb') as output:
                left = item.size
                while left:
                    chunk = stream.read(min(left, 65536))
                    if not chunk:
                        raise ValueError('Truncated tar payload')
                    output.write(chunk)
                    left -= len(chunk)
                output.flush()
                os.fsync(output.fileno())
            target.chmod(0o644)
            padding = (-item.size) % 512
            if len(stream.read(padding)) != padding:
                raise ValueError('Truncated tar padding')
    required = {'server/__main__.py', 'server/app.py', 'updates/core.py',
                'requirements.txt', 'server-release.json'}
    if not required <= seen:
        raise ValueError('Incomplete server code archive')


def unique_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('Duplicate JSON field')
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=pairs)


def release_contract(directory, version, installed_requirements):
    descriptor = Path(directory) / 'server-release.json'
    if descriptor.stat().st_size > 8192:
        raise ValueError('Release descriptor too large')
    payload = unique_json(descriptor.read_text(encoding='utf-8'))
    expected = {'schema': 1, 'version': version,
                'requirements_sha256': hashlib.sha256(Path(installed_requirements).read_bytes()).hexdigest(),
                'database_compatible': True, 'migrations': 'none'}
    if (payload != expected or type(payload.get('schema')) is not int
            or payload.get('database_compatible') is not True):
        raise ValueError('Manual dependency/schema migration required; automatic application blocked')
    if hashlib.sha256((Path(directory) / 'requirements.txt').read_bytes()).hexdigest() != expected['requirements_sha256']:
        raise ValueError('Dependencies differ from installed pinned environment')


def atomic_json(path, payload):
    temporary = path.with_name(path.name + '.new')
    with temporary.open('x', encoding='utf-8') as output:
        json.dump(payload, output, sort_keys=True)
        output.flush()
        os.fsync(output.fileno())
    temporary.chmod(0o600)
    os.replace(temporary, path)
    fsync_directory(path.parent)


def fsync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


@contextmanager
def update_lock(root):
    import fcntl
    fd = os.open(root / 'update.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        protected(root / 'update.lock', directory=False)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


class Systemd:
    def __init__(self, probes=None):
        self.probes = {} if probes is None else probes

    def command(self, *args):
        return subprocess.run(['/usr/bin/systemctl', *args], check=True,
                              capture_output=True, text=True, timeout=100,
                              env={'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LC_ALL': 'C'})

    def snapshot(self):
        states = {}
        for unit in CHAIN:
            raw = self.command('show', unit, '--property=LoadState,ActiveState,SubState').stdout
            fields = dict(line.split('=', 1) for line in raw.splitlines() if '=' in line)
            if fields.get('LoadState') == 'not-found':
                if unit == CHAIN[0]:
                    raise ValueError('Backend unit missing')
                continue
            if fields.get('LoadState') != 'loaded' or fields.get('ActiveState') not in {'active', 'inactive'}:
                raise ValueError('Service chain is not in stable loaded active/inactive state')
            states[unit] = fields['ActiveState'] == 'active'
            if states[unit] and unit != CHAIN[0] and unit not in self.probes:
                raise ValueError('Active proxy/connector requires protected update-health.json readiness URL')
        return states

    def stop(self, states):
        for unit in reversed(CHAIN):
            if states.get(unit):
                self.command('stop', unit)

    def restore(self, states):
        for unit in CHAIN:
            if states.get(unit):
                self.command('start', unit)
        # Explicitly preserve inactive services; dependencies must not activate them.
        for unit in reversed(CHAIN):
            if unit in states and not states[unit]:
                self.command('stop', unit)
        if self.snapshot() != states:
            raise ValueError('Service chain restoration did not match snapshot')

    def ready(self, states):
        if not states.get(CHAIN[0]):
            return
        import httpx
        deadline = time.monotonic() + 40
        with httpx.Client(trust_env=False, follow_redirects=False, timeout=2) as client:
            while time.monotonic() < deadline:
                try:
                    urls = ['http://127.0.0.1:8765/health'] + [self.probes[unit] for unit in CHAIN[1:] if states.get(unit)]
                    healthy = True
                    for url in urls:
                        with client.stream('GET', url, headers={'Accept-Encoding': 'identity'}) as response:
                            if response.headers.get('content-encoding', 'identity').lower() != 'identity':
                                raise ValueError('Encoded readiness response rejected')
                            raw = bytearray()
                            for chunk in response.iter_raw(chunk_size=1024):
                                raw.extend(chunk)
                                if len(raw) > 8192 or time.monotonic() >= deadline:
                                    raise ValueError('Readiness response size/time limit')
                            body = unique_json(bytes(raw))
                            healthy = healthy and response.status_code == 200 and isinstance(body, dict) and body.get('ok') is True
                    if healthy and self.snapshot() == states:
                        return
                except (httpx.HTTPError, ValueError):
                    pass
                time.sleep(1)
        raise ValueError('Readiness timeout (backend JSON and original service chain)')


def switch(root, target):
    temporary = root / ('.current-' + uuid.uuid4().hex)
    temporary.symlink_to(target, target_is_directory=True)
    os.replace(temporary, root / 'current')
    fsync_directory(root)


def baseline(root):
    current = root / 'current'
    if not current.is_symlink():
        raise ValueError('Versioned install bootstrap required; current must be a managed link')
    target = os.readlink(current)
    parts = PurePosixPath(target).parts
    if len(parts) != 2 or parts[0] != 'releases' or target != '/'.join(parts):
        raise ValueError('Invalid managed current target')
    version_tuple(parts[1])
    protected(root / target)
    protected(root / target / 'server-release.json', directory=False)
    release_contract(root / target, parts[1], root / 'requirements.txt')
    return target, parts[1]


def apply_update(root, *, prepare=prepare_update, manifest_verifier=verify_manifest,
                 artifact_verifier=verify_artifact, services=None):
    require_root()
    root = protected(root)
    for name in ('releases', 'cache', 'venv'):
        protected(root / name)
    protected(root / 'requirements.txt', directory=False)
    if services is None:
        probes = {}
        health = root / 'update-health.json'
        if os.path.lexists(health):
            protected(health, directory=False)
            if health.stat().st_size > 8192:
                raise ValueError('Health configuration too large')
            probes = unique_json(health.read_text())
            from urllib.parse import urlsplit
            import ipaddress
            if not isinstance(probes, dict) or not set(probes) <= set(CHAIN[1:]):
                raise ValueError('Invalid readiness probe mapping')
            for url in probes.values():
                if not isinstance(url, str) or len(url) > 2048:
                    raise ValueError('Invalid readiness URL')
                parsed = urlsplit(url)
                if parsed.username or parsed.password or parsed.fragment or parsed.query or parsed.path != '/health':
                    raise ValueError('Readiness URLs must be credential-free /health endpoints')
                if parsed.scheme != 'https':
                    if parsed.scheme != 'http' or not ipaddress.ip_address(parsed.hostname).is_loopback:
                        raise ValueError('Readiness requires HTTPS or numeric loopback HTTP')
        services = Systemd(probes)
    with update_lock(root):
        journal = root / 'update-state.json'
        if journal.exists():
            protected(journal, directory=False)
            if unique_json(journal.read_text()).get('phase') not in {'complete', 'rolled-back'}:
                raise ValueError('Interrupted update: operator reconciliation required')
        old_target, current_version = baseline(root)
        candidate = prepare('server', current_version, root / 'cache')
        if candidate is None:
            return {'phase': 'no-update', 'version': current_version}
        protected(candidate.artifact, directory=False)
        if not candidate.artifact.is_relative_to(root / 'cache'):
            raise ValueError('Artifact outside protected cache')
        payload = manifest_verifier(candidate.manifest_bytes)
        metadata = payload['artifacts'].get('server')
        version = payload['version']
        if (candidate.component != 'server' or version != candidate.version
                or version_tuple(version) <= version_tuple(current_version)
                or metadata != candidate.artifact_metadata or metadata.get('kind') != 'tar-server'):
            raise ValueError('Signed server/version/artifact binding mismatch')
        artifact_verifier(candidate.artifact, metadata)
        target = root / 'releases' / version
        if os.path.lexists(target):
            raise ValueError('Version directory already exists')
        stage = Path(tempfile.mkdtemp(prefix='.stage-', dir=root / 'releases'))
        try:
            unpack(candidate.artifact, stage)
            release_contract(stage, version, root / 'requirements.txt')
            artifact_verifier(candidate.artifact, metadata)
            states = services.snapshot()  # Before any service/pointer mutation.
            record = {'phase': 'staged', 'old': old_target, 'new': 'releases/' + version, 'services': states}
            atomic_json(journal, record)
            stage.chmod(0o755)  # Backend UID needs RX; never grant write access.
            stage.rename(target)
            fsync_directory(target.parent)
            try:
                record['phase'] = 'stopping'
                atomic_json(journal, record)
                services.stop(states)
                record['phase'] = 'switching'
                atomic_json(journal, record)
                switch(root, record['new'])
                record['phase'] = 'starting'
                atomic_json(journal, record)
                services.restore(states)
                services.ready(states)
                record['phase'] = 'complete'
                atomic_json(journal, record)
                return record
            except Exception:
                record['phase'] = 'rolling-back'
                atomic_json(journal, record)
                services.stop(states)
                switch(root, old_target)
                services.restore(states)
                services.ready(states)
                record['phase'] = 'rolled-back'
                atomic_json(journal, record)
                raise
        finally:
            if stage.exists():
                shutil.rmtree(stage)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--install-dir', type=Path, default=Path('/opt/tracer-transfer'))
    args = parser.parse_args(argv)
    try:
        print(json.dumps(apply_update(args.install_dir), sort_keys=True))
    except (ValueError, OSError, UpdateRejected, UpdateUnavailable, subprocess.SubprocessError) as exc:
        parser.exit(1, str(exc) + '\n')
