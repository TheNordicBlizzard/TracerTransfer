"""Offline, deterministic Linux code archives; QA artifacts are never release inputs."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tarfile
import tempfile
import tomllib

ROOT = Path(__file__).resolve().parents[1]
REQUIREMENTS = 'deploy/linux/requirements.txt'
RECIPE = 'scripts/build_server_archive.py'
MAX_FILE = 16 * 1024 * 1024
MAX_EXPANDED = 128 * 1024 * 1024
MAX_FILES = 2048


def sha256(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':')) + '\n').encode('utf-8')


def validate_version(value):
    if (not isinstance(value, str) or len(value) > 64
            or not re.fullmatch(r'(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)', value)):
        raise ValueError('Version must be strict x.y.z')
    return value


def git(root, *args):
    result = subprocess.run(['git', *args], cwd=root, capture_output=True, check=False)
    if result.returncode:
        raise ValueError('Git provenance unavailable')
    return result.stdout


def regular_bytes(root, name):
    path = root / name
    for part in (path, *path.parents):
        if part.is_symlink() or (hasattr(part, 'is_junction') and part.is_junction()):
            raise ValueError('Linked source or ancestor rejected')
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > MAX_FILE:
        raise ValueError('Source must be a bounded regular file without hardlinks')
    with path.open('rb') as stream:
        raw = stream.read(MAX_FILE + 1)
    if len(raw) > MAX_FILE:
        raise ValueError('Source exceeds limit')
    return raw


def snapshot(root):
    names = {'configuration.py', REQUIREMENTS, 'pyproject.toml', 'uv.lock', RECIPE}
    for folder in ('server', 'updates'):
        # Deliberately nonrecursive: nested modules are outside the updater contract.
        directory = root / folder
        if directory.is_symlink() or (hasattr(directory, 'is_junction') and directory.is_junction()):
            raise ValueError('Linked source directory rejected')
        names.update(p.relative_to(root).as_posix() for p in directory.glob('*.py'))
    required = {'server/__main__.py', 'server/app.py', 'server/update.py', 'updates/core.py'}
    if not required <= names:
        raise ValueError('Incomplete server sources')
    if len(names) > MAX_FILES:
        raise ValueError('Too many sources')
    return {name: regular_bytes(root, name) for name in sorted(names)}


def provenance(root):
    top = Path(os.fsdecode(git(root, 'rev-parse', '--show-toplevel')).strip()).resolve()
    if top != root.resolve():
        raise ValueError('Source root must be Git repository root')
    commit = git(root, 'rev-parse', '--verify', 'HEAD').decode('ascii').strip()
    if not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', commit):
        raise ValueError('Invalid Git commit')
    dirty = bool(git(root, 'status', '--porcelain=v1', '--untracked-files=all'))
    return commit, dirty


def check_payload(payload):
    if len(payload) > MAX_FILES or sum(map(len, payload.values())) > MAX_EXPANDED:
        raise ValueError('Archive limits exceeded')
    for name, raw in payload.items():
        if len(raw) > MAX_FILE:
            raise ValueError('Archive member limit exceeded')
        item = tarfile.TarInfo(name)
        item.tobuf(format=tarfile.USTAR_FORMAT, encoding='utf-8', errors='strict')
        # Conservative tripwires, not a replacement for a reviewed secret audit.
        if (re.search(rb'-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----', raw)
                or re.search(rb'https?://[^\s/]+:[^\s/@]+@', raw)
                or re.search(rb'\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})\b', raw)):
            raise ValueError('Potential embedded secret; package blocked (content suppressed)')


def write_archive(path, payload):
    with path.open('wb') as output:
        with gzip.GzipFile(filename='', mode='wb', fileobj=output, mtime=0, compresslevel=9) as compressed:
            with tarfile.open(fileobj=compressed, mode='w', format=tarfile.USTAR_FORMAT) as archive:
                for name, raw in sorted(payload.items()):
                    item = tarfile.TarInfo(name)
                    item.size = len(raw)
                    item.mode = 0o644
                    item.uid = item.gid = item.mtime = 0
                    item.uname = item.gname = ''
                    archive.addfile(item, io.BytesIO(raw))
        output.flush()
        os.fsync(output.fileno())


def build(root, version, output, *, qa=False, allow_dirty=False):
    """Bind exact archived bytes, recipe and lock to before/after Git/source snapshots."""
    root, output = Path(root).absolute(), Path(output).absolute()
    validate_version(version)
    if allow_dirty and not qa:
        raise ValueError('--allow-dirty requires --qa; QA cannot become release')
    if output.resolve().is_relative_to(root.resolve()):
        raise ValueError('Output must be outside source repository')
    before = snapshot(root)
    commit, dirty = provenance(root)
    if dirty and not (qa and allow_dirty):
        raise ValueError('Dirty tree rejected; explicit --qa --allow-dirty required')
    project_version = tomllib.loads(before['pyproject.toml'].decode('utf-8'))['project']['version']
    if version != project_version:
        raise ValueError('Version differs from pyproject.toml')
    if not qa:
        for name, raw in before.items():
            if git(root, 'show', f'{commit}:{name}') != raw:
                raise ValueError('Release source bytes differ from commit (including line endings)')
    payload = {name: raw for name, raw in before.items()
               if name == 'configuration.py' or name.startswith(('server/', 'updates/'))}
    payload['requirements.txt'] = before[REQUIREMENTS]
    payload['server-release.json'] = canonical({
        'schema': 1, 'version': version, 'requirements_sha256': sha256(payload['requirements.txt']),
        'database_compatible': True, 'migrations': 'none'})
    check_payload(payload)
    output.mkdir(parents=True, exist_ok=True)
    name = f'TracerServidor-{version}-linux' + ('-QA-ONLY' if qa else '') + '.tar.gz'
    target = output / name
    receipt = target.with_name(target.name + '.build.json')
    if target.exists() or receipt.exists():
        raise ValueError('Output already exists; refusing overwrite')
    with tempfile.TemporaryDirectory(prefix='.server-build-', dir=output) as work:
        archive = Path(work) / name
        write_archive(archive, payload)
        if snapshot(root) != before or provenance(root) != (commit, dirty):
            raise ValueError('Sources or Git provenance changed during build; discard artifact')
        record = {'schema': 1, 'component': 'server', 'version': version, 'commit': commit,
                  'dirty': dirty, 'channel': 'qa-only' if qa else 'release-candidate',
                  'release_eligible': not qa, 'publication_approved': False,
                  'sources': {key: sha256(raw) for key, raw in before.items()},
                  'files': {key: {'sha256': sha256(raw), 'bytes': len(raw)} for key, raw in sorted(payload.items())},
                  'artifact': {'name': name, 'sha256': sha256(archive.read_bytes()),
                               'size': archive.stat().st_size, 'kind': 'tar-server'}}
        # Exclusive creation: no existing package can be silently replaced, including races.
        try:
            with target.open('xb') as stream:
                stream.write(archive.read_bytes())
                stream.flush()
                os.fsync(stream.fileno())
        except FileExistsError:
            raise ValueError('Output already exists') from None
        try:
            with receipt.open('xb') as stream:
                stream.write(canonical(record))
                stream.flush()
                os.fsync(stream.fileno())
        except Exception:
            target.unlink()
            raise
    return target, record


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--version', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--qa', action='store_true')
    parser.add_argument('--allow-dirty', action='store_true')
    args = parser.parse_args(argv)
    try:
        path, record = build(args.root, args.version, args.output, qa=args.qa, allow_dirty=args.allow_dirty)
    except (ValueError, OSError, KeyError) as exc:
        parser.exit(1, f'Build rejected: {exc}\n')
    print(json.dumps({'package': str(path), **record['artifact'], 'channel': record['channel']}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
