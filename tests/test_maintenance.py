import json
from pathlib import Path
import sqlite3
import pytest
from server.maintenance import backup,restore,MaintenanceError


def database(path):
    path.mkdir()
    db=sqlite3.connect(path/'tracer.sqlite3')
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('CREATE TABLE sample(value TEXT)')
    db.execute("INSERT INTO sample VALUES('durable')")
    db.commit()
    payload=path/'received'/'sample.oma'
    payload.parent.mkdir()
    payload.write_bytes(b'payload')
    return db


def test_online_backup_restore_with_wal_and_retention(tmp_path):
    data=tmp_path/'data';db=database(data)
    try:
        first=backup(data,tmp_path/'backups',keep=1,min_free_bytes=0)
        restored=restore(first,tmp_path/'restored')
        with sqlite3.connect(restored/'tracer.sqlite3') as copied:
            assert copied.execute('SELECT value FROM sample').fetchall()==[('durable',)]
        copied.close()
        assert (restored/'received/sample.oma').read_bytes()==b'payload'
        second=backup(data,tmp_path/'backups',keep=1,min_free_bytes=0)
        assert second.exists() and not first.exists()
        assert json.loads((data/'maintenance-status.json').read_text())['backup_ok']
    finally:db.close()


def test_restore_never_overwrites_or_accepts_tampering(tmp_path):
    data=tmp_path/'data';db=database(data)
    try:
        copy=backup(data,tmp_path/'backups',min_free_bytes=0)
        with pytest.raises(MaintenanceError):restore(copy,data)
        (copy/'received/sample.oma').write_bytes(b'changed')
        with pytest.raises(MaintenanceError):restore(copy,tmp_path/'restored')
        assert not (tmp_path/'restored').exists()
    finally:db.close()


def test_invalid_target_space_and_unsafe_manifest(tmp_path):
    data=tmp_path/'data';db=database(data)
    try:
        with pytest.raises(MaintenanceError):backup(data,data/'recursive')
        with pytest.raises(MaintenanceError):backup(data,tmp_path/'full',min_free_bytes=10**30)
        copy=backup(data,tmp_path/'backups',min_free_bytes=0)
        manifest=json.loads((copy/'manifest.json').read_text())
        manifest['files'][0]['path']='../escape'
        (copy/'manifest.json').write_text(json.dumps(manifest))
        with pytest.raises(MaintenanceError):restore(copy,tmp_path/'restored')
    finally:db.close()


def test_backup_requires_every_pending_payload(tmp_path):
    import hashlib
    data=tmp_path/'data';db=database(data)
    try:
        identifier='1'*32;body=b'pending';digest=hashlib.sha256(body).hexdigest()
        db.execute('CREATE TABLE deliveries(id TEXT,sha256 TEXT,status TEXT)')
        db.execute('INSERT INTO deliveries VALUES(?,?,?)',(identifier,digest,'pending'));db.commit()
        with pytest.raises(MaintenanceError):backup(data,tmp_path/'backups',min_free_bytes=0)
        assert not list((tmp_path/'backups').glob('backup-*'))
        assert json.loads((data/'maintenance-status.json').read_text())['backup_ok'] is False
        payload=data/'spool'/identifier;payload.parent.mkdir(parents=True);payload.write_bytes(body)
        copy=backup(data,tmp_path/'backups',min_free_bytes=0)
        manifest=json.loads((copy/'manifest.json').read_text())
        assert manifest['required_payloads']==[payload.relative_to(data).as_posix()]
        assert (copy/payload.relative_to(data)).read_bytes()==body
    finally:db.close()
