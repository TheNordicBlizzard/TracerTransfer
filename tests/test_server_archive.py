"""Real Git fixtures and CLI archives; no network, production or systemctl."""
import gzip
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tarfile

import pytest

from scripts import build_server_archive as pack
from server.update import unpack, release_contract


@pytest.fixture
def source(tmp_path):
    root = tmp_path / 'repo'
    root.mkdir()
    entries = {
        'pyproject.toml': b'[project]\nversion="0.1.3"\n',
        'uv.lock': b'version = 1\n',
        'configuration.py': b'VALUE = "generic"\n',
        pack.REQUIREMENTS: b'example==1.0 --hash=sha256:' + b'a' * 64 + b'\n',
        'server/app.py': b'# app\n', 'server/__main__.py': b'# main\n',
        'server/update.py': b'# updater\n', 'updates/core.py': b'# core\n',
        'server/__init__.py': b'', 'updates/__init__.py': b'',
        pack.RECIPE: Path(pack.__file__).read_bytes(),
    }
    for name, raw in entries.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    def git(*args):
        subprocess.run(['git', '-c', 'user.name=QA', '-c', 'user.email=qa@example.invalid',
                        *args], cwd=root, check=True, capture_output=True)
    git('init')
    git('config', 'core.autocrlf', 'false')
    git('add', '.')
    git('commit', '-m', 'isolated fixture')
    return root


def test_deterministic_ustar_and_exact_updater_contract(source, tmp_path):
    first, record = pack.build(source, '0.1.3', tmp_path / 'one')
    second, other = pack.build(source, '0.1.3', tmp_path / 'two')
    assert first.read_bytes() == second.read_bytes()
    assert record == other
    assert record['release_eligible'] is True
    assert record['publication_approved'] is False
    raw = gzip.decompress(first.read_bytes())
    assert first.read_bytes()[4:8] == bytes(4)
    with tarfile.open(first) as archive:
        assert archive.getnames() == sorted(record['files'])
        for member in archive:
            assert member.type == tarfile.REGTYPE
            assert member.mode == 0o644
            assert (member.uid, member.gid, member.mtime) == (0, 0, 0)
            assert member.uname == member.gname == ''
            assert not member.pax_headers
            value = archive.extractfile(member).read()
            assert hashlib.sha256(value).hexdigest() == record['files'][member.name]['sha256']
    assert raw[257:263] == b'ustar\x00'
    extracted = tmp_path / 'extracted'
    extracted.mkdir()
    unpack(first, extracted)
    release_contract(extracted, '0.1.3', source / pack.REQUIREMENTS)
    assert (extracted / 'requirements.txt').read_bytes() == (source / pack.REQUIREMENTS).read_bytes()
    assert record['artifact']['sha256'] == hashlib.sha256(first.read_bytes()).hexdigest()
    assert json.loads(first.with_name(first.name + '.build.json').read_bytes()) == record


@pytest.mark.parametrize('version', ['01.2.3', 'v0.1.3', '0.1', '0.1.3-qa', '../0.1.3', '0.1.3\n', '0.1.4'])
def test_versions_rejected(source, tmp_path, version):
    with pytest.raises(ValueError, match='Version'):
        pack.build(source, version, tmp_path / 'out')


def test_dirty_is_explicit_and_never_release(source, tmp_path):
    (source / '.env').write_text('DO_NOT_PACKAGE=private')
    (source / 'server/data.db').write_bytes(b'private db')
    (source / 'server/nested').mkdir()
    (source / 'server/nested/ignored.py').write_text('# nested')
    with pytest.raises(ValueError, match='Dirty'):
        pack.build(source, '0.1.3', tmp_path / 'out')
    with pytest.raises(ValueError, match='requires --qa'):
        pack.build(source, '0.1.3', tmp_path / 'out', allow_dirty=True)
    archive, record = pack.build(source, '0.1.3', tmp_path / 'out', qa=True, allow_dirty=True)
    assert 'QA-ONLY' in archive.name
    assert record['channel'] == 'qa-only'
    assert record['dirty'] and not record['release_eligible']
    with tarfile.open(archive) as stream:
        assert not any(name.endswith(('.env', '.db')) or '/nested/' in name for name in stream.getnames())


def test_cli_real_archive_and_no_overwrite(source, tmp_path):
    command = [sys.executable, str(Path(pack.__file__)), '--root', str(source),
               '--version', '0.1.3', '--output', str(tmp_path / 'cli'), '--qa']
    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    metadata = json.loads(result.stdout)
    assert Path(metadata['package']).is_file()
    again = subprocess.run(command, capture_output=True, text=True)
    assert again.returncode == 1
    assert 'already exists' in again.stderr


def test_missing_provenance_and_missing_sources(source, tmp_path):
    (source / 'server/app.py').unlink()
    with pytest.raises(ValueError, match='Incomplete'):
        pack.build(source, '0.1.3', tmp_path / 'out', qa=True, allow_dirty=True)
    (source / 'server/app.py').write_bytes(b'# restored')
    (source / '.git').rename(tmp_path / 'detached-git')
    with pytest.raises(ValueError, match='Git provenance'):
        pack.build(source, '0.1.3', tmp_path / 'out', qa=True, allow_dirty=True)


@pytest.mark.parametrize('mutation', ['source', 'head', 'new-module'])
def test_mutation_during_build_is_rejected(source, tmp_path, monkeypatch, mutation):
    original = pack.write_archive
    def mutate(path, payload):
        original(path, payload)
        if mutation == 'head':
            subprocess.run(['git', '-c', 'user.name=QA', '-c', 'user.email=qa@example.invalid',
                            'commit', '--allow-empty', '-m', 'mutation'], cwd=source, check=True, capture_output=True)
        else:
            (source / ('server/new.py' if mutation == 'new-module' else 'configuration.py')).write_bytes(b'# changed')
    monkeypatch.setattr(pack, 'write_archive', mutate)
    with pytest.raises(ValueError, match='changed during build'):
        pack.build(source, '0.1.3', tmp_path / 'out')
    assert not list((tmp_path / 'out').glob('*.tar.gz'))


def test_links_and_output_inside_repository_rejected(source, tmp_path):
    with pytest.raises(ValueError, match='outside'):
        pack.build(source, '0.1.3', source / 'out')
    target = source / 'server/app.py'
    target.unlink()
    try:
        target.symlink_to(source / 'configuration.py')
    except OSError:
        pytest.skip('File symlink privilege unavailable')
    with pytest.raises(ValueError, match='Linked'):
        pack.build(source, '0.1.3', tmp_path / 'out', qa=True, allow_dirty=True)


def test_hardlinks_rejected(source, tmp_path):
    (source / 'server/app.py').unlink()
    (source / 'server/app.py').hardlink_to(source / 'configuration.py')
    with pytest.raises(ValueError, match='hardlinks'):
        pack.build(source, '0.1.3', tmp_path / 'out', qa=True, allow_dirty=True)


@pytest.mark.parametrize('secret', [b'-----BEGIN ' + b'PRIVATE KEY-----', b'https://user:' + b'pass@example.invalid', b'ghp_' + b'A' * 36])
def test_secret_tripwires_suppress_content(source, tmp_path, secret):
    (source / 'configuration.py').write_bytes(secret)
    with pytest.raises(ValueError, match='Potential embedded secret') as caught:
        pack.build(source, '0.1.3', tmp_path / 'out', qa=True, allow_dirty=True)
    assert secret.decode() not in str(caught.value)


def test_size_and_count_limits(source, tmp_path, monkeypatch):
    monkeypatch.setattr(pack, 'MAX_FILE', 2)
    with pytest.raises(ValueError, match='bounded'):
        pack.build(source, '0.1.3', tmp_path / 'out')
    monkeypatch.setattr(pack, 'MAX_FILE', 16 * 1024 * 1024)
    monkeypatch.setattr(pack, 'MAX_FILES', 1)
    with pytest.raises(ValueError, match='Too many'):
        pack.build(source, '0.1.3', tmp_path / 'out')


def test_clean_status_without_matching_commit_bytes_rejected(source, tmp_path, monkeypatch):
    (source / 'configuration.py').write_bytes(b'# not in commit')
    real = pack.git
    def hide_status(root, *args):
        return b'' if args[0] == 'status' else real(root, *args)
    monkeypatch.setattr(pack, 'git', hide_status)
    with pytest.raises(ValueError, match='differ from commit'):
        pack.build(source, '0.1.3', tmp_path / 'out')
