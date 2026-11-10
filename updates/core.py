"""Fail-closed, credential-free GitHub Releases discovery and signed downloads.

This module stages bytes only. Callers MUST reverify the manifest and artifact
immediately before applying them; it does not execute installers.
"""
from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile
from urllib.parse import urlsplit, urljoin

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

PINNED_PUBLIC_KEY = 'ahyjfdQIY100LuZktyPretfBo4FUEaUL4YYN8P1/pi0='
REPOSITORY = 'TheNordicBlizzard/TracerTransfer'
LATEST_URL = f'https://api.github.com/repos/{REPOSITORY}/releases/latest'
MAX_MANIFEST_BYTES = 128 * 1024
MAX_ARTIFACT_BYTES = 256 * 1024 * 1024
MAX_API_BYTES = 1024 * 1024
MAX_REDIRECTS = 5
KINDS = {'client': 'inno-client', 'delivery': 'exe-delivery', 'server': 'tar-server'}
_REDIRECT_HOSTS = {'github.com', 'release-assets.githubusercontent.com', 'objects.githubusercontent.com'}
_VERSION = re.compile(r'(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\Z')
_SHA256 = re.compile(r'[0-9a-f]{64}\Z')
_TIMESTAMP = re.compile(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|\+00:00)\Z')


class UpdateUnavailable(Exception):
    """No usable public transport (offline, private/absent release, rate limit)."""


class UpdateRejected(Exception):
    """Untrusted, malformed, replayed, or unsafe update data/path."""


@dataclass(frozen=True)
class PreparedUpdate:
    artifact: Path
    version: str
    component: str
    manifest_bytes: bytes
    artifact_metadata: dict


def canonical_payload(payload: dict) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=False, allow_nan=False).encode('utf-8')


def version_tuple(value: str) -> tuple[int, int, int]:
    if not isinstance(value, str) or len(value) > 64 or not _VERSION.fullmatch(value):
        raise UpdateRejected('Version must be strict x.y.z.')
    major, minor, patch = (int(part) for part in value.split('.'))
    return major, minor, patch


def artifact_name(component: str, version: str) -> str:
    version_tuple(version)
    if component == 'client':
        return f'TracerTransfer-Cliente-{version}-windows-x64-pilot-unsigned.exe'
    if component == 'delivery':
        return 'TracerEntregador.exe'
    if component == 'server':
        return f'TracerServidor-{version}-linux.tar.gz'
    raise UpdateRejected('Unknown update component.')


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise UpdateRejected('Duplicate JSON key.')
        result[key] = value
    return result


def _json(data: bytes) -> dict:
    try:
        result = json.loads(data.decode('utf-8'), object_pairs_hook=_unique_object,
                            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        if not isinstance(result, dict):
            raise ValueError()
        return result
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise UpdateRejected('Invalid JSON object.') from exc


def _timestamp(value):
    if not isinstance(value, str) or not _TIMESTAMP.fullmatch(value):
        raise UpdateRejected('Expected an ISO UTC timestamp.')
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError as exc:
        raise UpdateRejected('Invalid timestamp.') from exc


def _metadata(metadata):
    if not isinstance(metadata, dict) or set(metadata) != {'name', 'sha256', 'size', 'kind'}:
        raise UpdateRejected('Invalid artifact metadata.')
    if (type(metadata['size']) is not int or not 0 < metadata['size'] <= MAX_ARTIFACT_BYTES
            or not isinstance(metadata['sha256'], str)
            or not _SHA256.fullmatch(metadata['sha256'])):
        raise UpdateRejected('Invalid artifact size/hash.')
    name = metadata['name']
    if (not isinstance(name, str) or len(name) > 160 or '/' in name or '\\' in name
            or not name.endswith(('.exe', '.tar.gz')) or ':' in name or name in {'.', '..'}):
        raise UpdateRejected('Unsafe artifact name.')
    if not isinstance(metadata['kind'], str) or metadata['kind'] not in KINDS.values():
        raise UpdateRejected('Unknown artifact kind.')


def verify_manifest(envelope_bytes: bytes, *, public_key=PINNED_PUBLIC_KEY, now=None) -> dict:
    if not isinstance(envelope_bytes, bytes) or len(envelope_bytes) > MAX_MANIFEST_BYTES:
        raise UpdateRejected('Manifest exceeds size limit.')
    envelope = _json(envelope_bytes)
    if set(envelope) != {'payload', 'signature'} or not isinstance(envelope['payload'], dict):
        raise UpdateRejected('Invalid signed envelope.')
    payload = envelope['payload']
    try:
        if not isinstance(public_key, (str, bytes)):
            raise ValueError('Invalid public key encoding.')
        key_bytes = base64.b64decode(public_key, validate=True)
        key = Ed25519PublicKey.from_public_bytes(key_bytes)
        signature = base64.b64decode(envelope['signature'], validate=True)
        key.verify(signature, canonical_payload(payload))
    except (InvalidSignature, ValueError, TypeError, binascii.Error, RecursionError) as exc:
        raise UpdateRejected('Manifest signature rejected.') from exc
    if set(payload) != {'schema', 'repository', 'version', 'channel', 'published_at', 'expires_at', 'artifacts', 'component', 'tag', 'commit'}:
        raise UpdateRejected('Unknown manifest schema fields.')
    if type(payload['schema']) is not int or payload['schema'] != 2 or payload['repository'] != REPOSITORY or payload['channel'] != 'pilot':
        raise UpdateRejected('Manifest scope/schema mismatch.')
    version_tuple(payload['version'])
    if (not isinstance(payload['component'], str) or payload['component'] not in KINDS or payload['tag'] != 'v' + payload['version']
            or not isinstance(payload['commit'], str)
            or not re.fullmatch('[0-9a-f]{40}', payload['commit'])):
        raise UpdateRejected('Manifest component/tag/commit mismatch.')
    published, expires = _timestamp(payload['published_at']), _timestamp(payload['expires_at'])
    now = datetime.now(timezone.utc) if now is None else now
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise UpdateRejected('Verification clock must be timezone-aware.')
    if published > now or expires <= now or expires <= published:
        raise UpdateRejected('Manifest expired or published in the future.')
    artifacts = payload['artifacts']
    if not isinstance(artifacts, dict) or set(artifacts) != {payload['component']}:
        raise UpdateRejected('Invalid artifact components.')
    for component, metadata in artifacts.items():
        _metadata(metadata)
        if metadata['kind'] != KINDS[component] or metadata['name'] != artifact_name(component, payload['version']):
            raise UpdateRejected('Artifact/component binding mismatch.')
    return payload


def _safe_path(path: Path) -> Path:
    """Check every existing ancestor without resolving away links/junctions."""
    path = Path(os.path.abspath(path))
    for entry in reversed((path, *path.parents)):
        try:
            info = entry.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise UpdateRejected('Reparse point/symlink in update path.')
    return path


def verify_artifact(path, metadata) -> None:
    _metadata(metadata)
    path = _safe_path(Path(path))
    try:
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise UpdateRejected('Artifact must be a regular, unlinked file.')
        flags = os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0)
        fd = os.open(path, flags)
        with os.fdopen(fd, 'rb') as stream:
            start = os.fstat(stream.fileno())
            if (start.st_dev, start.st_ino) != (before.st_dev, before.st_ino) or start.st_nlink != 1:
                raise UpdateRejected('Artifact changed while opening.')
            if start.st_size != metadata['size']:
                raise UpdateRejected('Artifact size mismatch.')
            digest = hashlib.sha256()
            size = 0
            while chunk := stream.read(64 * 1024):
                size += len(chunk)
                if size > metadata['size']:
                    raise UpdateRejected('Artifact grew during verification.')
                digest.update(chunk)
            end = os.fstat(stream.fileno())
        _safe_path(path)
        after = path.lstat()
        # Python 3.13 Windows lstat reports creation time as st_ctime, whereas
        # fstat may report NTFS change time. Compare ctime only within the same
        # API; cross-API identity, mtime, size and link count must still agree.
        fingerprint = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_nlink)
        if (fingerprint(start) != fingerprint(end) or fingerprint(end) != fingerprint(after)
                or fingerprint(before) != fingerprint(after)
                or start.st_ctime_ns != end.st_ctime_ns or before.st_ctime_ns != after.st_ctime_ns
                or size != metadata['size'] or digest.hexdigest() != metadata['sha256']):
            raise UpdateRejected('Artifact changed/hash mismatch.')
    except OSError as exc:
        raise UpdateRejected('Artifact cannot be safely read.') from exc


def _private_directory(path: Path):
    _safe_path(path)
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    _safe_path(path)
    if os.name != 'nt':
        path.chmod(0o700)
        return
    # Protected DACL: only SYSTEM and the owner. Children inherit this DACL.
    import ctypes
    from ctypes import wintypes
    advapi = ctypes.WinDLL('advapi32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    descriptor = ctypes.c_void_p()
    convert = advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW
    convert.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.DWORD)]
    convert.restype = wintypes.BOOL
    set_security = advapi.SetFileSecurityW
    set_security.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p]
    set_security.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    if not convert('D:P(A;OICI;FA;;;SY)(A;OICI;FA;;;OW)', 1, ctypes.byref(descriptor), None):
        raise UpdateRejected('Cannot construct private cache ACL.')
    try:
        if not set_security(str(path), 0x80000004, descriptor):
            raise UpdateRejected('Cannot protect cache ACL.')
    finally:
        kernel.LocalFree(descriptor)


def _url(url: str, *, initial=False, tag=None, name=None):
    if not isinstance(url, str) or len(url) > 8192 or any(ord(c) < 33 for c in url) or '\\' in url:
        raise UpdateRejected('Unsafe download URL.')
    try:
        parsed = urlsplit(url)
        if (parsed.scheme != 'https' or parsed.username is not None or parsed.password is not None
                or parsed.port not in (None, 443) or parsed.fragment or parsed.hostname not in _REDIRECT_HOSTS
                or parsed.netloc not in {parsed.hostname, parsed.hostname + ':443'}):
            raise ValueError()
        if initial and (parsed.hostname != 'github.com' or parsed.query
                        or parsed.path != f'/{REPOSITORY}/releases/download/{tag}/{name}'):
            raise ValueError()
    except (ValueError, TypeError) as exc:
        raise UpdateRejected('Download URL outside allowed release origins.') from exc


def _download(http, url, limit, *, sink=None, api=False):
    """Bound both declared and streamed sizes; never follow redirects implicitly."""
    total = 0
    result = bytearray()
    for hop in range(MAX_REDIRECTS + 1):
        if api:
            if url != LATEST_URL:
                raise UpdateRejected('API redirect forbidden.')
        else:
            _url(url)
        try:
            with http.stream('GET', url, follow_redirects=False,
                             headers={'Accept': 'application/vnd.github+json' if api else 'application/octet-stream',
                                      'Accept-Encoding': 'identity', 'User-Agent': 'TracerTransfer-Updater'}) as response:
                if response.status_code in (301, 302, 303, 307, 308):
                    if api or hop == MAX_REDIRECTS:
                        raise UpdateRejected('Redirect limit/API redirect rejected.')
                    location = response.headers.get('location')
                    if not location:
                        raise UpdateRejected('Redirect missing location.')
                    url = urljoin(url, location)
                    _url(url)
                    continue
                if response.status_code != 200:
                    raise UpdateUnavailable(f'Public update HTTP {response.status_code}.')
                encoding = response.headers.get('content-encoding', 'identity')
                if encoding.lower() != 'identity':
                    raise UpdateRejected('Encoded update response rejected.')
                length = response.headers.get('content-length')
                if length is not None:
                    if not length.isascii() or not length.isdecimal() or len(length) > 12 or int(length) > limit:
                        raise UpdateRejected('Response exceeds size limit.')
                for chunk in response.iter_bytes(chunk_size=64 * 1024):
                    total += len(chunk)
                    if total > limit:
                        raise UpdateRejected('Stream exceeds size limit.')
                    if sink is None:
                        result.extend(chunk)
                    else:
                        sink.write(chunk)
                if length is not None and total != int(length):
                    raise UpdateRejected('Truncated response.')
                return bytes(result)
        except httpx.HTTPError as exc:
            raise UpdateUnavailable('Public update service unreachable.') from exc
    raise UpdateRejected('Too many redirects.')


def _release_assets(release, version):
    if (release.get('draft') is not False or release.get('prerelease') is not False
            or release.get('tag_name') != f'v{version}'):
        raise UpdateRejected('Release/tag/version mismatch or draft/prerelease.')
    assets = release.get('assets')
    if not isinstance(assets, list):
        raise UpdateRejected('Missing release assets.')
    indexed = {}
    for asset in assets:
        if not isinstance(asset, dict) or not isinstance(asset.get('name'), str):
            raise UpdateRejected('Malformed release asset.')
        name = asset['name']
        if name in indexed:
            raise UpdateRejected('Duplicate release asset name.')
        indexed[name] = asset
    return indexed


def prepare_update(component, current_version, cache_dir, *, http=None,
                   public_key=PINNED_PUBLIC_KEY, now=None) -> PreparedUpdate | None:
    if component not in KINDS:
        raise UpdateRejected('Unknown component.')
    current = version_tuple(current_version)
    if http is None:
        with httpx.Client(verify=True, trust_env=False, follow_redirects=False,
                          timeout=httpx.Timeout(30.0, connect=10.0), auth=None) as client:
            return prepare_update(component, current_version, cache_dir, http=client, public_key=public_key, now=now)
    release = _json(_download(http, LATEST_URL, MAX_API_BYTES, api=True))
    tag = release.get('tag_name')
    if not isinstance(tag, str) or not tag.startswith('v'):
        raise UpdateRejected('Invalid release tag.')
    version = tag[1:]
    candidate = version_tuple(version)
    assets = _release_assets(release, version)
    if candidate < current:
        raise UpdateRejected('Downgrade rejected.')
    if candidate == current:
        return None  # Same release is not an update and no artifact is accepted.
    feed_name = f'update-feed-{component}.json'
    manifest_asset = assets.get(feed_name)
    if manifest_asset is None:
        raise UpdateUnavailable('Signed update manifest not published.')
    manifest_url = manifest_asset.get('browser_download_url')
    _url(manifest_url, initial=True, tag=tag, name=feed_name)
    manifest_bytes = _download(http, manifest_url, MAX_MANIFEST_BYTES)
    payload = verify_manifest(manifest_bytes, public_key=public_key, now=now)
    if (payload['version'] != version or payload['component'] != component
            or release.get('target_commitish') != payload['commit']):
        raise UpdateRejected('Signed manifest not bound to release tag.')
    # Bind every signed artifact (not only the requested component) to this release.
    for metadata in payload['artifacts'].values():
        asset = assets.get(metadata['name'])
        if asset is None or type(asset.get('size')) is not int or asset['size'] != metadata['size']:
            raise UpdateRejected('Signed artifact absent/size mismatch in release.')
        _url(asset.get('browser_download_url'), initial=True, tag=tag, name=metadata['name'])
    metadata = payload['artifacts'].get(component)
    if metadata is None:
        return None
    cache = _safe_path(Path(cache_dir))
    work = None
    complete = False
    try:
        _private_directory(cache)
        work = Path(tempfile.mkdtemp(prefix='update-', dir=cache))
        _private_directory(work)
        partial = work / 'artifact.part'
        artifact = work / metadata['name']
        with partial.open('xb') as stream:
            _download(http, assets[metadata['name']]['browser_download_url'], metadata['size'], sink=stream)
            stream.flush()
            os.fsync(stream.fileno())
        verify_artifact(partial, metadata)
        _safe_path(work)
        if os.path.lexists(artifact):
            raise UpdateRejected('Artifact target already exists.')
        if os.name == 'nt':
            partial.rename(artifact)
        else:
            os.link(partial, artifact)
            partial.unlink()
        # Persist the ORIGINAL envelope for independent verification by the applier.
        with (work / 'update-manifest.json').open('xb') as stream:
            stream.write(manifest_bytes)
            stream.flush()
            os.fsync(stream.fileno())
        verify_manifest(manifest_bytes, public_key=public_key, now=now)
        verify_artifact(artifact, metadata)
        complete = True
        return PreparedUpdate(artifact, version, component, manifest_bytes, dict(metadata))
    except OSError as exc:
        raise UpdateRejected('Cannot create safe private update cache.') from exc
    finally:
        # Failed preparations leave no partial files. Successful staging is retained.
        if work is not None and not complete:
            try:
                _safe_path(work)
                shutil.rmtree(work)
            except (OSError, UpdateRejected):
                pass
