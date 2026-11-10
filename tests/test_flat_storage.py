"""Direct delivery preserves names and never overwrites existing industrial files."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import sqlite3
import subprocess

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from server.app import create_app
from server.storage import Storage
from tests.test_hardening import UploadRequest, assert_slots_released, make_storage
from tests.test_server import PASSWORD, device, upload
from server.users import bootstrap_admin


def flat(tmp_path):
    output = tmp_path / 'industrial'
    output.mkdir()
    return make_storage(tmp_path / 'private', output_dir=output), output


def receive(storage, name, body, device_id='device'):
    return asyncio.run(storage.receive(UploadRequest([body]), {'id': device_id}, name))


def test_flat_names_and_both_extensions(tmp_path):
    storage, output = flat(tmp_path)
    assert not receive(storage, '123.OMA', b'oma')['duplicate']
    assert not receive(storage, '124.vca', b'vca')['duplicate']
    assert sorted(p.name for p in output.iterdir()) == ['123.OMA', '124.vca']
    assert (output / '123.OMA').read_bytes() == b'oma'
    assert not (storage.db.data_dir / 'received').exists()
    assert receive(storage, '123.OMA', b'oma')['duplicate']
    assert_slots_released(storage)


@pytest.mark.parametrize('existing_name,incoming_name', [('same.oma','same.oma'), ('Same.OMA','same.oma')])
def test_conflict_preserves_original_and_database(tmp_path, existing_name, incoming_name):
    storage, output = flat(tmp_path)
    original = output / existing_name
    original.write_bytes(b'industrial-original')
    with pytest.raises(HTTPException) as error:
        receive(storage, incoming_name, b'changed-content')
    assert error.value.status_code == 409
    assert 'original preserved' in error.value.detail
    assert original.read_bytes() == b'industrial-original'
    assert len(list(output.iterdir())) == 1
    with storage.db.connect() as c:
        assert c.execute('SELECT COUNT(*) FROM files').fetchone()[0] == 0
    assert_slots_released(storage)


def test_existing_identical_is_adopted_without_touching_file(tmp_path):
    storage, output = flat(tmp_path)
    original = output / 'Sample.OMA'
    original.write_bytes(b'original')
    before = original.stat()
    result = receive(storage, 'sample.oma', b'original')
    assert result['duplicate'] is True
    assert original.stat().st_ino == before.st_ino
    assert original.stat().st_mtime_ns == before.st_mtime_ns
    assert len(list(output.iterdir())) == 1
    assert_slots_released(storage)


@pytest.mark.parametrize('preexisting', [False,True])
def test_flat_sql_failure_compensates_only_own_file(tmp_path, preexisting):
    storage, output = flat(tmp_path)
    target = output / 'sql.oma'
    if preexisting:
        target.write_bytes(b'hello')
    with storage.db.connect(write=True) as c:
        c.execute("CREATE TRIGGER reject_file BEFORE INSERT ON files BEGIN SELECT RAISE(ABORT, 'test failure'); END")
    with pytest.raises(sqlite3.IntegrityError):
        receive(storage, 'sql.oma', b'hello')
    assert target.exists() == preexisting
    if preexisting:
        assert target.read_bytes() == b'hello'
    assert_slots_released(storage)


def test_flat_commit_failure_compensates_before_rollback(tmp_path, monkeypatch):
    storage, output = flat(tmp_path)
    original_connect = storage.db.connect
    observed = []
    @contextmanager
    def denied(write=False):
        with original_connect(write=write) as c:
            def auth(op, action, *_):
                if op == sqlite3.SQLITE_TRANSACTION and action == 'COMMIT':
                    return sqlite3.SQLITE_DENY
                if op == sqlite3.SQLITE_TRANSACTION and action == 'ROLLBACK':
                    observed.append(not list(output.iterdir()))
                return sqlite3.SQLITE_OK
            c.set_authorizer(auth)
            yield c
    monkeypatch.setattr(storage.db, 'connect', denied)
    with pytest.raises(sqlite3.DatabaseError):
        receive(storage, 'commit.oma', b'hello')
    assert observed == [True]
    assert_slots_released(storage)


def test_http_cross_device_dedup_and_conflict(tmp_path):
    output = tmp_path / 'output'
    output.mkdir()
    app = create_app(tmp_path/'private', output_dir=output)
    bootstrap_admin(app.state.db, 'admin', PASSWORD)
    with TestClient(app) as c:
        admin = {'Authorization':'Bearer '+c.post('/api/login',json={'username':'admin','password':PASSWORD}).json()['token']}
        first, second = device(c,admin), device(c,admin)
        assert upload(c,first['token'],name='test.oma').status_code == 200
        assert upload(c,second['token'],name='TEST.OMA').json()['duplicate']
        result = upload(c,second['token'],name='TEST.OMA',body=b'other')
        assert result.status_code == 409
        audit = (app.state.db.data_dir/'audit.log').read_text(encoding='utf-8')
        assert f"upload_conflict device={second['id']} name=TEST.OMA" in audit
        assert hashlib.sha256(b'other').hexdigest() in audit
        assert first['token'] not in audit and second['token'] not in audit
        assert (output/'test.oma').read_bytes() == b'hello'
        with app.state.db.connect() as db:
            assert db.execute('SELECT COUNT(*) FROM files').fetchone()[0] == 2
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: upload(c,first['token'],name='parallel.vca'), range(4)))
        assert all(r.status_code == 200 for r in results)
        assert sum(not r.json()['duplicate'] for r in results) == 1


def test_missing_output_and_administrative_directory_rejected(tmp_path):
    with pytest.raises(FileNotFoundError):
        make_storage(tmp_path/'private', output_dir=tmp_path/'absent')
    with pytest.raises(ValueError):
        make_storage(tmp_path/'other', output_dir=tmp_path/'other')


def directory_link(link, target):
    if os.name == 'nt':
        result = subprocess.run(['cmd.exe','/d','/c','mklink','/J',str(link),str(target)],capture_output=True)
        if result.returncode:
            pytest.skip('Windows junction creation unavailable')
    else:
        link.symlink_to(target, target_is_directory=True)


def test_junction_output_or_ancestor_rejected(tmp_path):
    target = tmp_path/'actual'
    target.mkdir()
    (target/'nested').mkdir()
    link = tmp_path/'link'
    directory_link(link,target)
    try:
        with pytest.raises(ValueError):
            make_storage(tmp_path/'private', output_dir=link/'nested')
    finally:
        if os.name == 'nt':link.rmdir()
        else:link.unlink()


def test_directory_replaced_by_junction_fails_closed(tmp_path):
    storage, output = flat(tmp_path)
    output.rmdir()
    elsewhere = tmp_path/'elsewhere'
    elsewhere.mkdir()
    directory_link(output,elsewhere)
    try:
        with pytest.raises(ValueError):
            receive(storage, 'unsafe.oma', b'hello')
        assert not list(elsewhere.iterdir())
        assert_slots_released(storage)
    finally:
        if os.name == 'nt':output.rmdir()
        else:output.unlink()


def test_existing_file_symlink_is_not_followed(tmp_path):
    storage, output = flat(tmp_path)
    target=tmp_path/'outside'
    target.write_bytes(b'hello')
    link=output/'link.oma'
    try:link.symlink_to(target)
    except OSError:pytest.skip('File symlink privilege unavailable')
    with pytest.raises(HTTPException) as error:
        receive(storage,'link.oma',b'hello')
    assert error.value.status_code == 409
    assert target.read_bytes()==b'hello'
