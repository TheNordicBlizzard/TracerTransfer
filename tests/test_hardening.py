"""Regression coverage for proxy trust, session revocation and bounded uploads.

Review corrections: Login.password already had Pydantic max_length=1024;
HTTP unbounded passwords were not a real finding. The missing guard affected
Auth.login direct callers. utcnow() and session expiry already use compatible
UTC-aware ISO strings; no date migration or timestamp rewrite is required.
"""
import asyncio
import hashlib
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

from server.app import create_app
from server.auth import Auth
from server.database import Database
from server.users import bootstrap_admin

PASSWORD = 'test-pass-1234'


class UploadRequest:
    def __init__(self, chunks, delay=0):
        self.chunks, self.delay = chunks, delay
        self.headers = {'x-content-sha256': hashlib.sha256(b''.join(chunks)).hexdigest()}

    async def stream(self):
        for chunk in self.chunks:
            await asyncio.sleep(self.delay)
            yield chunk


def make_storage(tmp_path, **kwargs):
    from server.storage import Storage
    from server.database import utcnow
    db = Database(tmp_path)
    with db.connect(write=True) as connection:
        connection.execute('INSERT INTO devices(id,name,token_hash,created_at) VALUES(?,?,?,?)',
                           ('device', 'Tracer', 'hash', utcnow()))
    return Storage(db, 1024 * 1024, **kwargs)


def assert_slots_released(storage):
    for _ in range(4):
        assert storage.slots.acquire(blocking=False)
    assert not storage.slots.acquire(blocking=False)
    for _ in range(4):
        storage.slots.release()
    assert not list(storage.staging.iterdir())


@pytest.mark.parametrize('chunk_timeout,total_timeout,delay,chunks', [
    (.02, 1, .1, [b'hello']),
    (.2, .05, .02, [b'a'] * 10),
])
def test_upload_timeout_releases_slots(tmp_path, chunk_timeout, total_timeout, delay, chunks):
    storage = make_storage(tmp_path, chunk_timeout=chunk_timeout, total_timeout=total_timeout)

    async def run():
        with pytest.raises(HTTPException) as error:
            await storage.receive(UploadRequest(chunks, delay), {'id': 'device'}, 'slow.oma')
        assert error.value.status_code == 408
        assert_slots_released(storage)
        assert (await storage.receive(UploadRequest([b'fast']), {'id': 'device'}, 'fast.oma'))['name'] == 'fast.oma'

    asyncio.run(run())


def test_cancelled_upload_releases_slots(tmp_path):
    storage = make_storage(tmp_path)

    async def run():
        task = asyncio.create_task(storage.receive(UploadRequest([b'hello'], 10), {'id': 'device'}, 'cancel.oma'))
        await asyncio.sleep(.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert_slots_released(storage)

    asyncio.run(run())


def test_case_insensitive_duplicate_preserves_saved_name(tmp_path):
    storage = make_storage(tmp_path)

    async def run():
        first = await storage.receive(UploadRequest([b'hello']), {'id': 'device'}, 'Sample.OMA')
        second = await storage.receive(UploadRequest([b'hello']), {'id': 'device'}, 'sample.oma')
        assert not first['duplicate']
        assert second['duplicate']
        assert second['name'] == 'Sample.OMA'
        with storage.db.connect() as connection:
            assert connection.execute('SELECT COUNT(*) FROM files').fetchone()[0] == 1
        # The database constraint itself, not only the lookup, is case-insensitive.
        import sqlite3
        with pytest.raises(sqlite3.IntegrityError):
            with storage.db.connect(write=True) as connection:
                connection.execute('INSERT INTO files SELECT device_id,lower(name),sha256,size,received_at FROM files')
        assert len(list((storage.db.data_dir / 'received').rglob('*.OMA'))) == 1

    asyncio.run(run())


def test_existing_integrity_hash_streams_off_event_loop(tmp_path, monkeypatch):
    import threading
    import time
    storage = make_storage(tmp_path)
    body = b'a' * (512 * 1024)
    asyncio.run(storage.receive(UploadRequest([body]), {'id': 'device'}, 'large.oma'))
    owner = threading.get_ident()
    opened = []
    real_open = Path.open

    def tracked_open(path, *args, **kwargs):
        if path.name == 'large.oma':
            opened.append(threading.get_ident())
            time.sleep(.05)
        return real_open(path, *args, **kwargs)

    def no_read_bytes(_path):
        pytest.fail('Integrity checks must stream, never read_bytes')

    monkeypatch.setattr(Path, 'open', tracked_open)
    monkeypatch.setattr(Path, 'read_bytes', no_read_bytes)

    async def run():
        task = asyncio.create_task(storage.receive(UploadRequest([body]), {'id': 'device'}, 'large.oma'))
        ticks = 0
        while not task.done():
            await asyncio.sleep(.005)
            ticks += 1
        assert (await task)['duplicate']
        assert ticks >= 3
        assert opened and all(thread != owner for thread in opened)

    asyncio.run(run())


@pytest.mark.parametrize('preexisting', [False, True])
def test_sql_failure_removes_only_new_link(tmp_path, preexisting):
    import sqlite3
    storage = make_storage(tmp_path)
    body = b'hello'
    digest = hashlib.sha256(body).hexdigest()
    destination = storage.db.data_dir / 'received' / 'device' / digest / 'safe.oma'
    if preexisting:
        destination.parent.mkdir(parents=True)
        destination.write_bytes(body)
    with storage.db.connect(write=True) as connection:
        connection.execute("CREATE TRIGGER reject_file BEFORE INSERT ON files BEGIN SELECT RAISE(ABORT, 'test failure'); END")
    with pytest.raises(sqlite3.IntegrityError, match='test failure'):
        asyncio.run(storage.receive(UploadRequest([body]), {'id': 'device'}, 'safe.oma'))
    assert destination.exists() == preexisting
    if preexisting:
        assert destination.read_bytes() == body
    assert_slots_released(storage)
    with storage.db.connect(write=True) as connection:
        assert connection.execute('SELECT COUNT(*) FROM files').fetchone()[0] == 0
        connection.execute('DROP TRIGGER reject_file')
    assert not asyncio.run(storage.receive(UploadRequest([body]), {'id': 'device'}, 'safe.oma'))['duplicate']


def test_commit_failure_compensates_before_rollback(tmp_path, monkeypatch):
    import sqlite3
    from contextlib import contextmanager
    storage = make_storage(tmp_path)
    connect = storage.db.connect
    rollback_observed = []

    @contextmanager
    def deny_commit(write=False):
        with connect(write=write) as connection:
            def authorize(operation, action, _arg, _db, _source):
                if operation == sqlite3.SQLITE_TRANSACTION and action == 'COMMIT':
                    return sqlite3.SQLITE_DENY
                if operation == sqlite3.SQLITE_TRANSACTION and action == 'ROLLBACK':
                    rollback_observed.append(not list((storage.db.data_dir / 'received').rglob('*.oma')))
                return sqlite3.SQLITE_OK
            connection.set_authorizer(authorize)
            yield connection

    monkeypatch.setattr(storage.db, 'connect', deny_commit)
    with pytest.raises(sqlite3.DatabaseError, match='not authorized'):
        asyncio.run(storage.receive(UploadRequest([b'hello']), {'id': 'device'}, 'commit.oma'))
    assert rollback_observed == [True]
    assert_slots_released(storage)
    with connect() as connection:
        assert connection.execute('SELECT COUNT(*) FROM files').fetchone()[0] == 0


def test_cancellation_during_publication_waits_for_worker(tmp_path, monkeypatch):
    import threading
    storage = make_storage(tmp_path)
    asyncio.run(storage.receive(UploadRequest([b'hello']), {'id': 'device'}, 'existing.oma'))
    entered, release = threading.Event(), threading.Event()
    original = storage._integrity_matches

    def blocked_hash(*args):
        entered.set()
        assert release.wait(2)
        return original(*args)

    monkeypatch.setattr(storage, '_integrity_matches', blocked_hash)

    async def run():
        task = asyncio.create_task(storage.receive(UploadRequest([b'hello']), {'id': 'device'}, 'existing.oma'))
        try:
            assert await asyncio.to_thread(entered.wait, 1)
            task.cancel()
            await asyncio.sleep(.01)
            task.cancel()
            await asyncio.sleep(.01)
            assert not task.done()
            assert list(storage.staging.iterdir())
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert_slots_released(storage)
        with storage.db.connect() as connection:
            assert connection.execute('SELECT COUNT(*) FROM files').fetchone()[0] == 1

    asyncio.run(run())


def test_corrupt_preexisting_file_is_never_overwritten(tmp_path):
    storage = make_storage(tmp_path)
    body = b'hello'
    digest = hashlib.sha256(body).hexdigest()
    destination = storage.db.data_dir / 'received' / 'device' / digest / 'bad.oma'
    destination.parent.mkdir(parents=True)
    destination.write_bytes(b'wrong')
    with pytest.raises(HTTPException) as error:
        asyncio.run(storage.receive(UploadRequest([body]), {'id': 'device'}, 'bad.oma'))
    assert error.value.status_code == 409
    assert destination.read_bytes() == b'wrong'
    assert_slots_released(storage)


def test_http_password_limit_and_utc_timestamps_were_already_correct(tmp_path):
    from datetime import datetime, timezone
    from pydantic import ValidationError
    from server.app import Login
    from server.database import utcnow
    with pytest.raises(ValidationError):
        Login(username='admin', password='x' * 1025)
    assert datetime.fromisoformat(utcnow()).tzinfo == timezone.utc
    db = Database(tmp_path)
    bootstrap_admin(db, 'admin', PASSWORD)
    auth = Auth(db)
    auth.login('admin', PASSWORD, '127.0.0.1')
    with db.connect() as connection:
        assert connection.execute('SELECT count(*) FROM sessions').fetchone()[0] == 0
    expiry = next(iter(auth.sessions.values()))['expires_at']
    assert datetime.fromisoformat(expiry).tzinfo == timezone.utc
    assert expiry > utcnow()


def test_duplicate_proxy_header_is_rejected():
    from server.auth import trusted_client_peer
    request = peer_request('127.0.0.1', '198.51.100.1')
    request.scope['headers'].append((b'x-tracer-client-ip', b'198.51.100.2'))
    assert trusted_client_peer(request) == '127.0.0.1'


def test_untrusted_peer_cannot_rotate_rate_limit_with_header(tmp_path):
    app = create_app(tmp_path)
    with TestClient(app, client=('198.51.100.3', 1234)) as client:
        for index in range(11):
            response = client.post('/api/login', json={'username': 'missing', 'password': 'wrong'},
                                   headers={'X-Tracer-Client-IP': f'192.0.2.{index + 1}'})
            assert response.status_code == (401 if index < 10 else 429)


def peer_request(peer, forwarded):
    return Request({'type': 'http', 'client': (peer, 1234), 'headers': [
        (b'x-tracer-client-ip', forwarded.encode()),
        (b'x-forwarded-for', b'198.51.100.99')], 'method': 'POST', 'path': '/'})


@pytest.mark.parametrize('peer,header,expected', [
    ('127.0.0.1', '198.51.100.1', '198.51.100.1'),
    ('::1', '2001:db8::1', '2001:db8::1'),
    ('198.51.100.2', '198.51.100.1', '198.51.100.2'),
    ('127.0.0.1', '198.51.100.1, 198.51.100.2', '127.0.0.1'),
    ('127.0.0.1', 'example.com', '127.0.0.1'),
    ('127.0.0.1', 'fe80::1%eth0', '127.0.0.1'),
    ('127.0.0.1', '', '127.0.0.1'),
])
def test_proxy_peer_trust(peer, header, expected):
    from server.auth import trusted_client_peer
    assert trusted_client_peer(peer_request(peer, header)) == expected


def test_proxy_rate_limits_are_isolated(tmp_path):
    app = create_app(tmp_path)
    # Caddy must overwrite X-Tracer-Client-IP with its socket remote_host.
    with TestClient(app, client=('127.0.0.1', 1234)) as client:
        for _ in range(10):
            assert client.post('/api/login', json={'username': 'missing', 'password': 'wrong'},
                               headers={'X-Tracer-Client-IP': '198.51.100.1'}).status_code == 401
        assert client.post('/api/login', json={'username': 'missing', 'password': 'wrong'},
                           headers={'X-Tracer-Client-IP': '198.51.100.1'}).status_code == 429
        assert client.post('/api/login', json={'username': 'missing', 'password': 'wrong'},
                           headers={'X-Tracer-Client-IP': '198.51.100.2'}).status_code == 401


@pytest.mark.parametrize('username,password', [('a', 'x' * 1025), ('a', None),
                                                ([], 'password'), ('a' * 65, 'password'),
                                                ('a', b'password'), ('a', '')])
def test_direct_login_input_guard(tmp_path, username, password):
    auth = Auth(Database(tmp_path))
    with pytest.raises(HTTPException) as error:
        auth.login(username, password, '198.51.100.1')
    assert error.value.status_code == 422
    assert not auth.attempts


def test_logout_revokes_only_its_session_and_is_idempotent(tmp_path):
    app = create_app(tmp_path)
    bootstrap_admin(app.state.db, 'admin', PASSWORD)
    with TestClient(app) as client:
        first = client.post('/api/login', json={'username': 'admin', 'password': PASSWORD}).json()['token']
        second = client.post('/api/login', json={'username': 'admin', 'password': PASSWORD}).json()['token']
        headers = {'Authorization': 'Bearer ' + first}
        assert client.get('/api/me', headers=headers).status_code == 200
        assert client.post('/api/logout', headers=headers).status_code == 200
        assert client.get('/api/me', headers=headers).status_code == 401
        assert client.post('/api/logout', headers=headers).status_code == 200
        assert client.post('/api/logout', headers={'Authorization': 'Bearer unknown'}).status_code == 200
        assert client.post('/api/logout').status_code == 401
        assert client.get('/api/me', headers={'Authorization': 'Bearer ' + second}).status_code == 200
        from server.auth import fingerprint
        with app.state.db.connect() as connection:
            assert not connection.execute('SELECT 1 FROM sessions WHERE token_hash=?',
                                          (fingerprint(first),)).fetchone()
