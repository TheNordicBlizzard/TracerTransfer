"""Isolated update contract tests: no systemctl, network or production writes."""
import io
from pathlib import Path
import tarfile
import pytest


def test_archive_paths_fail_closed():
    from server.update import archive_path
    for name in ('../escape', '/absolute', 'a/../b', './x', 'a//b', 'C:x', 'a\\b', 'server/data.db'):
        with pytest.raises(ValueError):
            archive_path(name)
    assert str(archive_path('server/app.py')) == 'server/app.py'


def test_unprivileged_platform_fails_closed():
    from server.update import require_root
    with pytest.raises(ValueError):
        require_root(platform='win32', uid=0)
    with pytest.raises(ValueError):
        require_root(platform='linux', uid=1000)
    require_root(platform='linux', uid=0)


def make_archive(path, entries):
    with tarfile.open(path, 'w:gz', format=tarfile.USTAR_FORMAT) as output:
        for name, raw, kind in entries:
            item = tarfile.TarInfo(name)
            item.type = kind
            item.size = len(raw)
            output.addfile(item, io.BytesIO(raw))
    return path


@pytest.mark.parametrize('kind', [tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.FIFOTYPE,
                                 tarfile.DIRTYPE, tarfile.XHDTYPE, tarfile.GNUTYPE_LONGNAME])
def test_archive_rejects_links_and_extended_headers(tmp_path, kind):
    from server.update import unpack
    target = tmp_path / 'out'
    target.mkdir()
    archive = make_archive(tmp_path / 'bad.tar.gz', [('server/app.py', b'', kind)])
    with pytest.raises(ValueError):
        unpack(archive, target)
    assert not list(target.iterdir())


def test_archive_duplicate_size_count_and_complete_payload(tmp_path, monkeypatch):
    from server import update
    entries = [(name, b'x', tarfile.REGTYPE) for name in
               ('server/app.py', 'server/__main__.py', 'updates/core.py',
                'requirements.txt', 'server-release.json')]
    for index, extra in enumerate((entries, entries + entries[:1])):
        target = tmp_path / str(index)
        target.mkdir()
        archive = make_archive(tmp_path / f'{index}.tar.gz', extra)
        if index:
            with pytest.raises(ValueError, match='Duplicate'):
                update.unpack(archive, target)
        else:
            update.unpack(archive, target)
            assert (target / 'server/app.py').read_bytes() == b'x'
    for attribute in ('MAX_FILE', 'MAX_EXPANDED', 'MAX_FILES'):
        with monkeypatch.context() as patch:
            patch.setattr(update, attribute, 0)
            target = tmp_path / attribute
            target.mkdir()
            with pytest.raises(ValueError):
                update.unpack(archive, target)


def test_large_pax_rejected_before_payload_read(tmp_path):
    import gzip
    from server.update import unpack
    item = tarfile.TarInfo('pax')
    item.type = tarfile.XHDTYPE
    item.size = 2 * 1024**3
    archive = tmp_path / 'pax.tar.gz'
    with gzip.open(archive, 'wb') as output:
        output.write(item.tobuf())
    with pytest.raises(ValueError, match='regular'):
        unpack(archive, tmp_path)


def test_release_contract_forbids_dependency_and_schema_changes(tmp_path):
    import hashlib
    import json
    from server.update import release_contract
    lock = tmp_path / 'requirements.txt'
    lock.write_bytes(b'pinned==1 --hash=sha256:fixture\n')
    expected = {'schema': 1, 'version': '0.1.4', 'requirements_sha256': hashlib.sha256(lock.read_bytes()).hexdigest(),
                'database_compatible': True, 'migrations': 'none'}
    descriptor = tmp_path / 'server-release.json'
    descriptor.write_text(json.dumps(expected))
    release_contract(tmp_path, '0.1.4', lock)
    for key, value in [('migrations', 'automatic'), ('database_compatible', False),
                       ('requirements_sha256', '0' * 64), ('version', '0.1.3')]:
        descriptor.write_text(json.dumps({**expected, key: value}))
        with pytest.raises(ValueError):
            release_contract(tmp_path, '0.1.4', lock)


def test_cli_help_is_nonprivileged_and_does_not_start_server():
    import subprocess
    import sys
    result = subprocess.run([sys.executable, '-m', 'server', 'update', '--help'],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert '--install-dir' in result.stdout


@pytest.mark.skipif(__import__('os').name != 'posix', reason='Real Linux root ownership, symlinks and flock required')
def test_posix_permissions_and_lock(tmp_path):
    import os
    from server import update
    if __import__('sys').platform != 'linux' or os.geteuid() != 0:
        pytest.skip('Linux root fixture unavailable')
    base = os.environ.get('TRACER_ROOT_TEST_TMP')
    if not base:
        pytest.skip('Set TRACER_ROOT_TEST_TMP to dedicated root-owned protected fixture parent')
    update.protected(base)
    import tempfile
    with tempfile.TemporaryDirectory(dir=base) as folder:
        root = Path(folder)
        update.protected(root)
        with update.update_lock(root):
            with pytest.raises(OSError):
                with update.update_lock(root):
                    pytest.fail('Second lock acquired')
        (root / 'cache').mkdir(mode=0o777)
        (root / 'cache').chmod(0o777)
        with pytest.raises(ValueError, match='root-owned'):
            update.protected(root / 'cache')


@pytest.mark.skipif(__import__('sys').platform != 'linux', reason='Linux root transaction fixture required; Windows does not emulate guards')
def test_real_posix_atomic_apply_health_failure_and_data_preservation():
    import os
    import tempfile
    import hashlib
    import json
    from server import update
    from updates.core import PreparedUpdate
    if os.geteuid() != 0 or not os.environ.get('TRACER_ROOT_TEST_TMP'):
        pytest.skip('Provide root-owned TRACER_ROOT_TEST_TMP in an isolated Linux laboratory')
    base = update.protected(os.environ['TRACER_ROOT_TEST_TMP'])
    with tempfile.TemporaryDirectory(dir=base) as folder:
        root = Path(folder) / 'install'
        root.mkdir()
        for name in ('releases', 'venv', 'cache'):
            (root / name).mkdir()
        external = Path(folder) / 'external-data'
        external.mkdir()
        sentinel = external / 'identity-db-spool'
        sentinel.write_bytes(b'preserve-all-new-receipts')
        lock = root / 'requirements.txt'
        lock.write_bytes(b'prepared-pinned-dependencies')
        def descriptor(version):
            return json.dumps({'schema': 1, 'version': version, 'requirements_sha256': hashlib.sha256(lock.read_bytes()).hexdigest(),
                               'database_compatible': True, 'migrations': 'none'}).encode()
        old = root / 'releases/0.1.3'
        old.mkdir()
        (old / 'requirements.txt').write_bytes(lock.read_bytes())
        (old / 'server-release.json').write_bytes(descriptor('0.1.3'))
        (root / 'current').symlink_to('releases/0.1.3')
        for version, fail in [('0.1.4', False), ('0.1.5', True)]:
            artifact = make_archive(root / 'cache' / f'{version}.tar.gz', [
                ('server/app.py', b'pass', tarfile.REGTYPE), ('server/__main__.py', b'pass', tarfile.REGTYPE),
                ('updates/core.py', b'pass', tarfile.REGTYPE), ('requirements.txt', lock.read_bytes(), tarfile.REGTYPE),
                ('server-release.json', descriptor(version), tarfile.REGTYPE)])
            meta = {'kind': 'tar-server'}
            candidate = PreparedUpdate(artifact, version, 'server', b'fixture', meta)
            class Services:
                failed = False
                def snapshot(self):
                    return {update.CHAIN[0]: True, update.CHAIN[1]: True, update.CHAIN[2]: False}
                def stop(self, states):
                    assert states == self.snapshot()
                def restore(self, states):
                    assert states == self.snapshot()
                def ready(self, states):
                    if fail and not self.failed:
                        self.failed = True
                        raise ValueError('fixture-health')
            kwargs = dict(prepare=lambda *args: candidate,
                          manifest_verifier=lambda _: {'version': version, 'artifacts': {'server': meta}},
                          artifact_verifier=lambda *args: None, services=Services())
            if fail:
                with pytest.raises(ValueError, match='fixture-health'):
                    update.apply_update(root, **kwargs)
            else:
                assert update.apply_update(root, **kwargs)['phase'] == 'complete'
            assert os.readlink(root / 'current') == 'releases/0.1.4'
            assert sentinel.read_bytes() == b'preserve-all-new-receipts'
            assert (root / 'releases/0.1.4').stat().st_mode & 0o777 == 0o755
        (root / 'update-state.json').write_text('{"phase":"switching"}')
        with pytest.raises(ValueError, match='Interrupted'):
            update.apply_update(root, services=Services())


def test_systemd_restores_only_previously_active_chain():
    from server.update import CHAIN, Systemd
    states = {CHAIN[0]: True, CHAIN[1]: True, CHAIN[2]: False}
    services = Systemd()
    calls = []
    services.command = lambda *args: calls.append(args)
    services.snapshot = lambda: dict(states)
    services.stop(states)
    services.restore(states)
    assert calls == [('stop', CHAIN[1]), ('stop', CHAIN[0]), ('start', CHAIN[0]),
                     ('start', CHAIN[1]), ('stop', CHAIN[2])]


def test_active_proxy_without_explicit_health_probe_is_rejected():
    from types import SimpleNamespace
    from server.update import Systemd
    services = Systemd()
    services.command = lambda *args: SimpleNamespace(stdout='LoadState=loaded\nActiveState=active\nSubState=running\n')
    with pytest.raises(ValueError, match='update-health'):
        services.snapshot()
