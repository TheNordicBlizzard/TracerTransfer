"""Offline release-manifest signing; emits public data only, never private keys."""
from __future__ import annotations

import argparse
import base64
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import tempfile

# Works both as `python scripts/sign_update_release.py` and as a module.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from client.state import unprotect_token
from updates.core import (
    KINDS, MAX_ARTIFACT_BYTES, PINNED_PUBLIC_KEY, REPOSITORY, UpdateRejected,
    _safe_path, artifact_name, canonical_payload, verify_artifact, verify_manifest,
    version_tuple,
)


def sign_manifest(version, artifacts, key_file, days_valid=14, *, now=None):
    """Return public envelope bytes. Private material never leaves this function."""
    version_tuple(version)
    if type(days_valid) is not int or not 1 <= days_valid <= 365:
        raise UpdateRejected('Validity must be 1..365 days.')
    if not artifacts or not set(artifacts).issubset(KINDS):
        raise UpdateRejected('Supply at least one known component.')
    key_file = _safe_path(Path(key_file))
    if key_file.is_relative_to(ROOT):
        raise UpdateRejected('Signing key must remain outside the repository.')
    if not key_file.is_file() or key_file.stat().st_size > 64 * 1024:
        raise UpdateRejected('Invalid protected key file.')
    protected = key_file.read_text(encoding='utf-8').strip()
    # DPAPI CurrentUser; no plaintext fallback and no key generation.
    private_bytes = base64.b64decode(unprotect_token(protected), validate=True)
    private_key = Ed25519PrivateKey.from_private_bytes(private_bytes)
    public_bytes = private_key.public_key().public_bytes(serialization.Encoding.Raw,
                                                       serialization.PublicFormat.Raw)
    if base64.b64encode(public_bytes).decode('ascii') != PINNED_PUBLIC_KEY:
        raise UpdateRejected('Signing key does not match the application pin.')
    now = datetime.now(timezone.utc) if now is None else now
    if now.tzinfo is None or now.utcoffset() is None:
        raise UpdateRejected('Signing clock must be timezone-aware.')
    now = now.astimezone(timezone.utc)
    payload = {
        'schema': 1, 'repository': REPOSITORY, 'version': version, 'channel': 'pilot',
        'published_at': now.isoformat().replace('+00:00', 'Z'),
        'expires_at': (now + timedelta(days=days_valid)).isoformat().replace('+00:00', 'Z'),
        'artifacts': {},
    }
    for component, source in artifacts.items():
        source = _safe_path(Path(source))
        info = source.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise UpdateRejected('Signing input must be a regular, unlinked file.')
        size = info.st_size
        if not 0 < size <= MAX_ARTIFACT_BYTES:
            raise UpdateRejected('Artifact size outside release limits.')
        digest = hashlib.sha256()
        total = 0
        with source.open('rb') as stream:
            while chunk := stream.read(64 * 1024):
                total += len(chunk)
                if total > size:
                    raise UpdateRejected('Artifact changed during signing.')
                digest.update(chunk)
        metadata = {'name': artifact_name(component, version), 'sha256': digest.hexdigest(),
                    'size': size, 'kind': KINDS[component]}
        verify_artifact(source, metadata)
        payload['artifacts'][component] = metadata
    envelope = {'payload': payload, 'signature': base64.b64encode(
        private_key.sign(canonical_payload(payload))).decode('ascii')}
    data = json.dumps(envelope, sort_keys=True, indent=2, ensure_ascii=False).encode('utf-8') + b'\n'
    verify_manifest(data, now=now)
    return data


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', required=True, help='Strict release version x.y.z')
    for component in KINDS:
        parser.add_argument('--' + component, type=Path)
    parser.add_argument('--key-file', type=Path,
                        default=Path.home() / '.tracer-transfer' / 'release-signing-key.dpapi')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--days-valid', type=int, default=14)
    args = parser.parse_args(argv)
    temp = None
    try:
        data = sign_manifest(args.version,
                             {c: getattr(args, c) for c in KINDS if getattr(args, c) is not None},
                             args.key_file, args.days_valid)
        output = _safe_path(args.output)
        if os.path.lexists(output):
            raise UpdateRejected('Refusing to overwrite output.')
        output.parent.mkdir(parents=True, exist_ok=True)
        _safe_path(output.parent)
        fd, filename = tempfile.mkstemp(prefix='.manifest-', suffix='.tmp', dir=output.parent)
        temp = Path(filename)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if os.name == 'nt':
            temp.rename(output)  # Windows rename is atomic and refuses an existing target.
        else:
            os.link(temp, output)  # Exclusive publish on POSIX, then remove temporary link.
            temp.unlink()
        print(f'Public signed manifest written: {output}')
        return 0
    except Exception:
        # Never echo DPAPI/decoder/crypto exception values: these can include key data.
        print('Manifest signing failed; check inputs, pinned key, and DPAPI user.', file=sys.stderr)
        return 1
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)


if __name__ == '__main__':
    raise SystemExit(main())
