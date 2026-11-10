"""Offline fixtures only; no network or production data accessed."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sqlite3
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if os.name == 'posix':
    spec = importlib.util.spec_from_file_location('backup_receive', ROOT / 'deploy/linux/backup_receive.py')
    receiver = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(receiver)
    export_spec = importlib.util.spec_from_file_location('backup_export', ROOT / 'deploy/linux/backup_export.py')
    exporter = importlib.util.module_from_spec(export_spec)
    export_spec.loader.exec_module(exporter)
    from server import maintenance
    sys.modules['tracer_backup_maintenance'] = maintenance


@unittest.skipUnless(os.name == 'posix', 'Linux receiver requires POSIX permissions and flock')
class OffVMBackupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = self.root / 'data'
        self.data.mkdir(mode=0o700)
        with contextlib.closing(sqlite3.connect(self.data / 'tracer.sqlite3')) as db:
            db.execute('CREATE TABLE fixture (id INTEGER)')
            db.commit()
        self.snapshot = maintenance.backup(self.data, self.root / 'backups')

    def archive(self, extra=None, bad_manifest=False):
        target = self.root / 'test.tar.gz'
        with tarfile.open(target, 'w:gz', format=tarfile.USTAR_FORMAT) as tar:
            for file in sorted(self.snapshot.iterdir()):
                if bad_manifest and file.name == 'manifest.json':
                    payload = json.loads(file.read_text())
                    payload['files'][0]['sha256'] = '0' * 64
                    raw = json.dumps(payload).encode()
                    info = tarfile.TarInfo('manifest.json')
                    info.size = len(raw)
                    tar.addfile(info, io.BytesIO(raw))
                else:
                    tar.add(file, arcname=file.name)
            if extra:
                tar.addfile(extra, io.BytesIO(b'x' * extra.size) if extra.isfile() else None)
        return target

    def validate(self, archive):
        work = self.root / 'work'
        work.mkdir()
        return receiver.validate_restore(archive, work)

    def test_export_preserves_local_maintenance_status(self):
        status = self.data / 'maintenance-status.json'
        status.write_text('{"local_daily_backup":"preserve"}')
        writer = maintenance._json_atomic
        destination = self.root / 'export-snapshots'
        snapshot = exporter.consistent_checkpoint(self.data, destination)
        self.assertTrue((snapshot / 'manifest.json').exists())
        self.assertEqual(status.read_text(), '{"local_daily_backup":"preserve"}')
        self.assertIs(maintenance._json_atomic, writer)
        self.assertTrue((self.root / 'snapshot-status.json').exists())

    def test_export_failure_preserves_local_maintenance_status(self):
        status = self.data / 'maintenance-status.json'
        status.write_text('{"local_daily_backup":"preserve"}')
        writer = maintenance._json_atomic
        with patch.object(maintenance, '_snapshot_database', side_effect=RuntimeError('fixture')), self.assertRaises(RuntimeError):
            exporter.consistent_checkpoint(self.data, self.root / 'export-snapshots')
        self.assertEqual(status.read_text(), '{"local_daily_backup":"preserve"}')
        self.assertIs(maintenance._json_atomic, writer)

    def test_restore_manifest_sqlite_and_hashes(self):
        proof = self.validate(self.archive())
        self.assertEqual(proof['sqlite_quick_check'], 'ok')
        self.assertEqual(proof['restored_database_sha256'], receiver.sha(self.snapshot / 'tracer.sqlite3'))

    def test_reject_bad_hash(self):
        with self.assertRaises(ValueError):
            self.validate(self.archive(bad_manifest=True))

    def test_reject_traversal_absolute_backslash_and_colon(self):
        for name in ('../x', '/x', 'a/../x', 'a\\x', 'C:x', './x'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                receiver.valid_path(name)

    def test_reject_symlink_hardlink_fifo_directory(self):
        for kind in (tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.FIFOTYPE, tarfile.DIRTYPE):
            with self.subTest(kind=kind):
                item = tarfile.TarInfo('unsafe')
                item.type = kind
                archive = self.archive(item)
                work = self.root / ('work-' + str(kind))
                work.mkdir()
                with self.assertRaises(ValueError):
                    receiver.validate_restore(archive, work)

    def test_reject_duplicate_and_unmanifested_file(self):
        for name in ('tracer.sqlite3', 'unexpected'):
            item = tarfile.TarInfo(name)
            item.size = 1
            archive = self.archive(item)
            work = self.root / ('work-' + name)
            work.mkdir()
            with self.assertRaises(ValueError):
                receiver.validate_restore(archive, work)

    def test_expansion_limit(self):
        with patch.object(receiver, 'MAX_EXPANDED', 1), self.assertRaises(ValueError):
            self.validate(self.archive())

    def test_space_reserve(self):
        usage = type('Usage', (), {'free': 0})()
        with patch.object(receiver.shutil, 'disk_usage', return_value=usage), self.assertRaises(ValueError):
            self.validate(self.archive())

    def test_reject_extended_headers_before_allocation(self):
        import gzip
        target = self.root / 'extension.tar.gz'
        item = tarfile.TarInfo('pax')
        item.type = tarfile.XHDTYPE
        item.size = 2 * 1024**3
        with gzip.open(target, 'wb') as stream:
            stream.write(item.tobuf())
        with self.assertRaises(ValueError):
            self.validate(target)

    def test_archive_upload_limit_and_cleanup(self):
        destination = self.root / 'receive'
        destination.mkdir(mode=0o700)
        with self.archive().open('rb') as stream, patch.object(receiver, 'MAX_ARCHIVE', 1), self.assertRaises(ValueError):
            receiver.receive(stream.fileno(), destination)
        self.assertFalse(list(destination.glob('.incoming-*')))

    def test_upload_timeout_and_cleanup(self):
        destination = self.root / 'receive'
        destination.mkdir(mode=0o700)
        with self.archive().open('rb') as stream, patch.object(receiver.select, 'select', return_value=([], [], [])), self.assertRaises(ValueError):
            receiver.receive(stream.fileno(), destination)
        self.assertFalse(list(destination.glob('.incoming-*')))

    def test_retention_never_removes_manual_or_link(self):
        root = self.root / 'retention'
        root.mkdir()
        manual = root / 'manual.tar.gz'
        manual.write_bytes(b'preserve')
        link = root / ('auto-20261110T120000000000Z-' + 'f' * 32 + '.tar.gz')
        link.symlink_to(manual)
        for number in range(10):
            (root / ('auto-20261110T120000000000Z-' + f'{number:032x}' + '.tar.gz')).write_bytes(b'123')
        result = receiver.retention(root, keep=7, budget=15)
        self.assertEqual(result, {'retained_count': 5, 'retained_bytes': 15})
        self.assertEqual(manual.read_bytes(), b'preserve')
        self.assertTrue(link.is_symlink())
        self.assertTrue((root / ('auto-20261110T120000000000Z-' + f'{9:032x}' + '.tar.gz')).exists())

    def test_retention_count_limit(self):
        root = self.root / 'retention'
        root.mkdir()
        for number in range(10):
            (root / ('auto-20261110T120000000000Z-' + f'{number:032x}' + '.tar.gz')).write_bytes(b'x')
        self.assertEqual(receiver.retention(root)['retained_count'], 7)

    def test_permission_policy(self):
        private = self.root / 'private'
        private.mkdir(mode=0o700)
        # A test TMPDIR must itself have safe ancestry, as on the installed service.
        receiver.private_directory(private)
        private.chmod(0o750)
        with self.assertRaises(ValueError):
            receiver.private_directory(private)
        private.chmod(0o700)
        link = self.root / 'link'
        link.symlink_to(private, target_is_directory=True)
        with self.assertRaises(ValueError):
            receiver.private_directory(link)

    def test_receive_atomic_status_and_cleanup(self):
        destination = self.root / 'receive'
        destination.mkdir(mode=0o700)
        with self.archive().open('rb') as stream:
            report = receiver.receive(stream.fileno(), destination)
        self.assertTrue(report['ok'])
        self.assertEqual(receiver.sha(report['archive']), report['sha256'])
        self.assertEqual(json.loads((destination / 'last-status.json').read_text()), report)
        self.assertFalse(list(destination.glob('.incoming-*')))
        self.assertEqual(Path(report['archive']).stat().st_mode & 0o777, 0o600)

    def test_receive_rejection_cleans_temporary(self):
        destination = self.root / 'receive'
        destination.mkdir(mode=0o700)
        with self.archive(bad_manifest=True).open('rb') as stream, self.assertRaises(ValueError):
            receiver.receive(stream.fileno(), destination)
        self.assertFalse(list(destination.glob('auto-*')))
        self.assertFalse(list(destination.glob('.incoming-*')))


if __name__ == '__main__':
    unittest.main(verbosity=2)
