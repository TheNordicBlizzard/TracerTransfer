"""Hermetic security-contract tests; no GitHub credentials or live releases."""
import base64
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from updates import VERSION
from updates import core
from scripts import sign_update_release as signing

NOW = datetime(2026, 11, 10, 12, tzinfo=timezone.utc)
BINARY = b'MZ-test-not-an-executable\0' * 100


@pytest.fixture
def signed():
    key = Ed25519PrivateKey.generate()  # Test-only ephemeral key, never production key.
    public = base64.b64encode(key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode()
    metadata = {'name': 'TracerEntregador.exe', 'sha256': hashlib.sha256(BINARY).hexdigest(),
                'size': len(BINARY), 'kind': 'exe-delivery'}
    payload = {'schema': 1, 'repository': core.REPOSITORY, 'version': '0.1.2',
               'channel': 'pilot', 'published_at': (NOW - timedelta(minutes=1)).isoformat(),
               'expires_at': (NOW + timedelta(days=1)).isoformat(),
               'artifacts': {'delivery': metadata}}
    def envelope(value=None):
        value = payload if value is None else value
        return json.dumps({'payload': value, 'signature': base64.b64encode(
            key.sign(core.canonical_payload(value))).decode()}).encode()
    return SimpleNamespace(key=key, public=public, payload=payload, metadata=metadata, envelope=envelope)


def release_for(signed):
    assets = [{'name': 'update-manifest.json', 'size': len(signed.envelope()),
               'browser_download_url': download_url('update-manifest.json')}]
    assets.extend({'name': m['name'], 'size': m['size'], 'browser_download_url': download_url(m['name'])}
                  for m in signed.payload['artifacts'].values())
    return {'tag_name': 'v0.1.2', 'prerelease': False, 'draft': False, 'assets': assets}


def download_url(name):
    return f'https://github.com/{core.REPOSITORY}/releases/download/v0.1.2/{name}'


def client_for(signed, *, release=None, manifest=None, binary=BINARY, intercept=None):
    calls = []
    def handler(request):
        calls.append(request)
        assert 'authorization' not in request.headers
        assert 'cookie' not in request.headers
        if intercept:
            result = intercept(request)
            if result is not None:
                return result
        if str(request.url) == core.LATEST_URL:
            return httpx.Response(200, json=release_for(signed) if release is None else release)
        if request.url.path.endswith('update-manifest.json'):
            return httpx.Response(200, content=signed.envelope() if manifest is None else manifest)
        return httpx.Response(200, content=binary)
    client = httpx.Client(transport=httpx.MockTransport(handler), trust_env=False)
    return client, calls


def prepare(signed, tmp_path, **kwargs):
    http, calls = client_for(signed, **kwargs)
    with http:
        result = core.prepare_update('delivery', '0.1.1', tmp_path / 'cache',
                                     http=http, public_key=signed.public, now=NOW)
    return result, calls


def test_version_pin_and_canonical():
    assert VERSION == '0.1.2'
    assert core.PINNED_PUBLIC_KEY == 'ahyjfdQIY100LuZktyPretfBo4FUEaUL4YYN8P1/pi0='
    assert core.canonical_payload({'z': 'é', 'a': 1}) == b'{"a":1,"z":"\xc3\xa9"}'


def test_valid_signature_and_staging(signed, tmp_path):
    assert core.verify_manifest(signed.envelope(), public_key=signed.public, now=NOW) == signed.payload
    result, calls = prepare(signed, tmp_path)
    assert isinstance(result, core.PreparedUpdate)
    assert result.component == 'delivery' and result.version == '0.1.2'
    assert result.artifact.read_bytes() == BINARY
    assert result.manifest_bytes == signed.envelope()
    assert (result.artifact.parent / 'update-manifest.json').read_bytes() == result.manifest_bytes
    assert len(calls) == 3
    core.verify_manifest(result.manifest_bytes, public_key=signed.public, now=NOW)
    core.verify_artifact(result.artifact, result.artifact_metadata)
    result.artifact.write_bytes(b'X' * len(BINARY))
    with pytest.raises(core.UpdateRejected):
        core.verify_artifact(result.artifact, result.artifact_metadata)


@pytest.mark.parametrize('change', ['signature', 'payload', 'key', 'duplicate', 'oversize', 'invalid-json'])
def test_untrusted_manifest(signed, change):
    envelope = json.loads(signed.envelope())
    public = signed.public
    if change == 'signature':
        envelope['signature'] = base64.b64encode(b'0' * 64).decode()
    elif change == 'payload':
        envelope['payload']['version'] = '0.1.3'
    elif change == 'key':
        public = base64.b64encode(b'1' * 32).decode()
    data = json.dumps(envelope).encode()
    if change == 'duplicate':
        data = data.replace(b'"schema": 1', b'"schema": 1, "schema": 1')
    elif change == 'oversize':
        data = b' ' * (core.MAX_MANIFEST_BYTES + 1)
    elif change == 'invalid-json':
        data = b'[]'
    with pytest.raises(core.UpdateRejected):
        core.verify_manifest(data, public_key=public, now=NOW)


@pytest.mark.parametrize(('field', 'value'), [
    ('expires_at', NOW.isoformat()), ('published_at', (NOW + timedelta(seconds=1)).isoformat()),
    ('published_at', '2026-11-10T09:00:00-03:00'), ('expires_at', 'not-a-date'),
    ('repository', 'attacker/repo'), ('channel', 'stable'), ('schema', True),
    ('version', '0.1.3rc1'), ('version', '00.1.3'), ('version', 'v0.1.3'),
    ('artifacts', {}), ('artifacts', {'unknown': {}}),
])
def test_signed_invalid_payload(signed, field, value):
    payload = deepcopy(signed.payload)
    payload[field] = value
    with pytest.raises(core.UpdateRejected):
        core.verify_manifest(signed.envelope(payload), public_key=signed.public, now=NOW)


@pytest.mark.parametrize(('field', 'value'), [
    ('name', '../TracerEntregador.exe'), ('name', 'TracerAtualizador.exe'),
    ('kind', 'exe-updater'), ('size', True), ('size', 0),
    ('size', core.MAX_ARTIFACT_BYTES + 1), ('sha256', 'a' * 63),
])
def test_artifact_component_binding(signed, field, value):
    payload = deepcopy(signed.payload)
    payload['artifacts']['delivery'][field] = value
    with pytest.raises(core.UpdateRejected):
        core.verify_manifest(signed.envelope(payload), public_key=signed.public, now=NOW)


@pytest.mark.parametrize('binary', [b'bad', b'x' * len(BINARY), BINARY + b'x'])
def test_artifact_tampering(signed, tmp_path, binary):
    with pytest.raises(core.UpdateRejected):
        prepare(signed, tmp_path, binary=binary)
    assert not list((tmp_path / 'cache').glob('update-*'))


@pytest.mark.parametrize('current', ['0.1.2', '0.1.3'])
def test_same_version_no_update_downgrade_rejected(signed, tmp_path, current):
    http, calls = client_for(signed)
    with http:
        if current == '0.1.2':
            assert core.prepare_update('delivery', current, tmp_path, http=http,
                                       public_key=signed.public, now=NOW) is None
        else:
            with pytest.raises(core.UpdateRejected):
                core.prepare_update('delivery', current, tmp_path, http=http,
                                    public_key=signed.public, now=NOW)
    assert len(calls) == 1


@pytest.mark.parametrize(('field', 'value'), [('draft', True), ('prerelease', True),
                                            ('tag_name', 'v0.1.2-rc1'), ('tag_name', '0.1.2')])
def test_release_rejections(signed, tmp_path, field, value):
    release = release_for(signed)
    release[field] = value
    with pytest.raises(core.UpdateRejected):
        prepare(signed, tmp_path, release=release)


@pytest.mark.parametrize('malicious', [
    'http://github.com/TheNordicBlizzard/TracerTransfer/releases/download/v0.1.2/update-manifest.json',
    'https://github.com/attacker/repo/releases/download/v0.1.2/update-manifest.json',
    'https://github.com/TheNordicBlizzard/TracerTransfer/releases/download/v0.1.3/update-manifest.json',
    'https://github.com.evil.test/x', 'https://127.0.0.1/x',
    'https://user:password@github.com/x', 'https://github.com:444/x',
    'https://github.com/x#fragment', 'https://github.com/../x',
])
def test_initial_url_rejection(signed, tmp_path, malicious):
    release = release_for(signed)
    release['assets'][0]['browser_download_url'] = malicious
    with pytest.raises(core.UpdateRejected):
        prepare(signed, tmp_path, release=release)


@pytest.mark.parametrize('location', ['https://evil.example/payload', 'https://127.0.0.1/x',
                                     'http://github.com/x', 'https://x:secret@github.com/x',
                                     'https://api.github.com/repos/other/repo',
                                     '//objects.githubusercontent.com:444/x'])
def test_malicious_redirect_never_contacted(signed, tmp_path, location):
    def intercept(req):
        if req.url.host != 'api.github.com':
            assert req.url.host == 'github.com'
            return httpx.Response(302, headers={'location': location})
    with pytest.raises(core.UpdateRejected):
        prepare(signed, tmp_path, intercept=intercept)


def test_allowed_redirect(signed, tmp_path):
    def intercept(req):
        if req.url.path.endswith('TracerEntregador.exe'):
            return httpx.Response(302, headers={'location': 'https://release-assets.githubusercontent.com/abc?sig=public'})
        if req.url.host == 'release-assets.githubusercontent.com':
            return httpx.Response(200, content=BINARY)
    result, calls = prepare(signed, tmp_path, intercept=intercept)
    assert result.artifact.read_bytes() == BINARY
    assert len(calls) == 4


def test_redirect_limit(signed, tmp_path):
    count = []
    def intercept(req):
        if req.url.host != 'api.github.com':
            count.append(1)
            return httpx.Response(302, headers={'location': 'https://github.com/loop'})
    with pytest.raises(core.UpdateRejected):
        prepare(signed, tmp_path, intercept=intercept)
    assert len(count) == core.MAX_REDIRECTS + 1


def test_api_redirect_rejected(signed, tmp_path):
    with pytest.raises(core.UpdateRejected):
        prepare(signed, tmp_path, intercept=lambda req: httpx.Response(
            302, headers={'location': core.LATEST_URL}))


@pytest.mark.parametrize('status', [404, 403, 429, 500])
def test_private_missing_rate_limited_unavailable(signed, tmp_path, status):
    with pytest.raises(core.UpdateUnavailable):
        prepare(signed, tmp_path, intercept=lambda req: httpx.Response(status))


def test_offline_unavailable(signed, tmp_path):
    def offline(req):
        raise httpx.ConnectError('offline', request=req)
    with pytest.raises(core.UpdateUnavailable):
        prepare(signed, tmp_path, intercept=offline)


class Chunks(httpx.SyncByteStream):
    def __init__(self, chunks):
        self.chunks = chunks
    def __iter__(self):
        yield from self.chunks


@pytest.mark.parametrize('mode', ['chunks', 'content-length', 'encoding'])
def test_stream_limits(signed, tmp_path, mode):
    def intercept(req):
        if req.url.path.endswith('TracerEntregador.exe'):
            if mode == 'content-length':
                return httpx.Response(200, headers={'content-length': str(core.MAX_ARTIFACT_BYTES + 1)})
            if mode == 'encoding':
                return httpx.Response(200, headers={'content-encoding': 'gzip'})
            return httpx.Response(200, stream=Chunks([BINARY, b'x' * 70000]))
    with pytest.raises(core.UpdateRejected):
        prepare(signed, tmp_path, intercept=intercept)


def test_manifest_chunk_limit(signed, tmp_path):
    def intercept(req):
        if req.url.path.endswith('update-manifest.json'):
            return httpx.Response(200, stream=Chunks([b' ' * 65536] * 3))
    with pytest.raises(core.UpdateRejected):
        prepare(signed, tmp_path, intercept=intercept)


@pytest.mark.parametrize('alter', ['missing', 'size', 'duplicate', 'signed-version', 'artifact-url'])
def test_asset_and_tag_binding(signed, tmp_path, alter):
    release = release_for(signed)
    manifest = None
    if alter == 'missing':
        release['assets'].pop()
    elif alter == 'size':
        release['assets'][-1]['size'] += 1
    elif alter == 'duplicate':
        release['assets'].append(release['assets'][-1])
    elif alter == 'signed-version':
        payload = deepcopy(signed.payload)
        payload['version'] = '0.1.3'
        manifest = signed.envelope(payload)
    else:
        release['assets'][-1]['browser_download_url'] = 'https://github.com/attacker/repo/x'
    with pytest.raises(core.UpdateRejected):
        prepare(signed, tmp_path, release=release, manifest=manifest)


def test_optional_component_not_required(signed, tmp_path):
    http, calls = client_for(signed)
    with http:
        assert core.prepare_update('client', '0.1.1', tmp_path, http=http,
                                   public_key=signed.public, now=NOW) is None
    assert len(calls) == 2


def test_missing_manifest_unavailable(signed, tmp_path):
    release = release_for(signed)
    release['assets'].pop(0)
    with pytest.raises(core.UpdateUnavailable):
        prepare(signed, tmp_path, release=release)


def test_hardlink_artifact_rejected(signed, tmp_path):
    source = tmp_path / 'source.exe'
    source.write_bytes(BINARY)
    link = tmp_path / 'link.exe'
    os.link(source, link)
    with pytest.raises(core.UpdateRejected):
        core.verify_artifact(link, signed.metadata)


def test_cache_reparse_ancestor(signed, tmp_path):
    target = tmp_path / 'target'
    target.mkdir()
    link = tmp_path / 'junction'
    if os.name == 'nt':
        command = subprocess.run(['cmd.exe', '/c', 'mklink', '/J', str(link), str(target)],
                                 capture_output=True)
        assert command.returncode == 0, command.stderr
    else:
        link.symlink_to(target, target_is_directory=True)
    try:
        with pytest.raises(core.UpdateRejected):
            prepare(signed, link)
        assert not list(target.iterdir())
    finally:
        if os.name == 'nt':
            link.rmdir()
        else:
            link.unlink()


def test_reparse_artifact_parent(signed, tmp_path):
    target = tmp_path / 'target'
    target.mkdir()
    (target / 'artifact.exe').write_bytes(BINARY)
    link = tmp_path / 'junction'
    if os.name == 'nt':
        result = subprocess.run(['cmd.exe', '/c', 'mklink', '/J', str(link), str(target)], capture_output=True)
        assert result.returncode == 0
    else:
        link.symlink_to(target, target_is_directory=True)
    try:
        with pytest.raises(core.UpdateRejected):
            core.verify_artifact(link / 'artifact.exe', signed.metadata)
    finally:
        link.rmdir() if os.name == 'nt' else link.unlink()


def test_production_http_security_options(signed, tmp_path, monkeypatch):
    real = httpx.Client
    observed = {}
    def factory(**kwargs):
        observed.update(kwargs)
        return real(transport=httpx.MockTransport(lambda req: httpx.Response(404)), **kwargs)
    monkeypatch.setattr(core.httpx, 'Client', factory)
    with pytest.raises(core.UpdateUnavailable):
        core.prepare_update('delivery', '0.1.1', tmp_path)
    assert observed['verify'] is True
    assert observed['trust_env'] is False and observed['follow_redirects'] is False
    assert observed['auth'] is None


def test_cli_signs_only_requested_components(signed, tmp_path, monkeypatch, capsys):
    private = signed.key.private_bytes(serialization.Encoding.Raw,
                                      serialization.PrivateFormat.Raw, serialization.NoEncryption())
    key_file = tmp_path / 'test-key.dpapi'
    key_file.write_text('test-only-protected-marker')
    artifact = tmp_path / 'input.exe'
    artifact.write_bytes(BINARY)
    monkeypatch.setattr(signing, 'PINNED_PUBLIC_KEY', signed.public)
    monkeypatch.setattr(signing, 'unprotect_token', lambda _: base64.b64encode(private).decode())
    # The production verifier's default pin is fixed; explicitly inject the test pin.
    monkeypatch.setattr(signing, 'verify_manifest', lambda data, **kw: core.verify_manifest(
        data, public_key=signed.public, **kw))
    output = tmp_path / 'update-manifest.json'
    assert signing.main(['--version', '0.1.2', '--delivery', str(artifact),
                         '--key-file', str(key_file), '--output', str(output), '--days-valid', '7']) == 0
    envelope = output.read_bytes()
    payload = core.verify_manifest(envelope, public_key=signed.public)
    assert set(payload['artifacts']) == {'delivery'}
    assert payload['artifacts']['delivery'] == signed.metadata
    assert base64.b64encode(private) not in envelope
    assert 'protected-marker' not in envelope.decode()
    assert signing.main(['--version', '0.1.2', '--delivery', str(artifact),
                         '--key-file', str(key_file), '--output', str(output)]) == 1
    captured = capsys.readouterr()
    assert base64.b64encode(private).decode() not in captured.out + captured.err


def test_cli_does_not_echo_secret_errors(tmp_path, monkeypatch, capsys):
    key = tmp_path / 'key.dpapi'
    key.write_text('marker')
    def bad_key(_):
        raise ValueError('NEVER-ECHO-SECRET')
    monkeypatch.setattr(signing, 'unprotect_token', bad_key)
    assert signing.main(['--version', '0.1.2', '--updater', str(tmp_path / 'absent.exe'),
                         '--key-file', str(key), '--output', str(tmp_path / 'manifest.json')]) == 1
    captured = capsys.readouterr()
    assert 'NEVER-ECHO-SECRET' not in captured.out + captured.err


@pytest.mark.parametrize('args', [[], ['--days-valid', '0'], ['--days-valid', '366']])
def test_cli_requires_artifacts_and_validity(tmp_path, args):
    assert signing.main(['--version', '0.1.2', '--output', str(tmp_path / 'm.json'), *args]) == 1


@pytest.mark.parametrize('component', ['client', 'delivery', 'updater'])
def test_each_component_can_be_only_signed_asset(signed, tmp_path, component):
    metadata = dict(signed.metadata, name=core.artifact_name(component, '0.1.2'),
                    kind=core.KINDS[component])
    signed.payload['artifacts'] = {component: metadata}
    http, calls = client_for(signed)
    with http:
        result = core.prepare_update(component, '0.1.1', tmp_path / 'cache', http=http,
                                     public_key=signed.public, now=NOW)
    assert result.component == component
    assert result.artifact.name == metadata['name']
    core.verify_artifact(result.artifact, metadata)


def test_preparation_inside_exception_handler_keeps_result(signed, tmp_path):
    try:
        raise RuntimeError('a previous unrelated operation failed')
    except RuntimeError:
        result, _ = prepare(signed, tmp_path)
    assert result.artifact.exists()


def test_expiration_rechecked_for_apply(signed, tmp_path):
    result, _ = prepare(signed, tmp_path)
    with pytest.raises(core.UpdateRejected):
        core.verify_manifest(result.manifest_bytes, public_key=signed.public,
                             now=NOW + timedelta(days=2))


def test_naive_clock_rejected(signed):
    with pytest.raises(core.UpdateRejected):
        core.verify_manifest(signed.envelope(), public_key=signed.public, now=NOW.replace(tzinfo=None))


def test_windows_cache_acl_is_protected_and_restricted(signed, tmp_path):
    result, _ = prepare(signed, tmp_path)
    if os.name != 'nt':
        assert result.artifact.parent.stat().st_mode & 0o777 == 0o700
        return
    import ctypes
    from ctypes import wintypes
    advapi = ctypes.WinDLL('advapi32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    get_security = advapi.GetFileSecurityW
    get_security.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p,
                            wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    get_security.restype = wintypes.BOOL
    needed = wintypes.DWORD()
    get_security(str(result.artifact.parent), 4, None, 0, ctypes.byref(needed))
    descriptor = ctypes.create_string_buffer(needed.value)
    assert get_security(str(result.artifact.parent), 4, descriptor, needed.value, ctypes.byref(needed))
    convert = advapi.ConvertSecurityDescriptorToStringSecurityDescriptorW
    convert.argtypes = [ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
                       ctypes.POINTER(wintypes.LPWSTR), ctypes.POINTER(wintypes.DWORD)]
    convert.restype = wintypes.BOOL
    text = wintypes.LPWSTR()
    assert convert(descriptor, 1, 4, ctypes.byref(text), None)
    try:
        sddl = text.value
        assert sddl.startswith('D:P')
        assert sddl.count('(') == 2
        assert ';;;SY)' in sddl and ';;;OW)' in sddl
        assert ';;;WD)' not in sddl and ';;;BU)' not in sddl and ';;;AU)' not in sddl
    finally:
        kernel.LocalFree.argtypes = [ctypes.c_void_p]
        kernel.LocalFree(ctypes.cast(text, ctypes.c_void_p))
