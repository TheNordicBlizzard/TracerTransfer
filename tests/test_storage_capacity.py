"""Simulated storage pressure: never fill a real filesystem or discard receipts."""
import asyncio
import errno
import hashlib
from types import SimpleNamespace

from fastapi import HTTPException
from fastapi.testclient import TestClient
import pytest

from server.app import create_app
from server.auth import fingerprint
from server.database import Database, utcnow
from server.storage import Storage


class Request:
    def __init__(self, chunks, declared=True):
        self.chunks = chunks
        self.reads = 0
        self.headers = {'x-content-sha256': hashlib.sha256(b''.join(chunks)).hexdigest()}
        if declared:
            self.headers['content-length'] = str(sum(map(len, chunks)))

    async def stream(self):
        for chunk in self.chunks:
            self.reads += 1
            yield chunk


def prepared(tmp_path):
    db = Database(tmp_path)
    with db.connect(write=True) as connection:
        connection.execute('INSERT INTO devices(id,name,token_hash,created_at) VALUES(?,?,?,?)',
                           ('device', 'Synthetic', fingerprint('test-only-token'), utcnow()))
    return Storage(db, 1024, min_free_bytes=16)


def empty(storage):
    assert not list(storage.staging.iterdir())
    for _ in range(4):
        assert storage.slots.acquire(blocking=False)
    for _ in range(4):
        storage.slots.release()
    with storage.db.connect() as connection:
        assert connection.execute('SELECT count(*) FROM files').fetchone()[0] == 0
        assert connection.execute('SELECT count(*) FROM deliveries').fetchone()[0] == 0


@pytest.mark.parametrize('declared', [True, False])
def test_low_space_rejects_before_reading_body(tmp_path, monkeypatch, declared):
    storage = prepared(tmp_path)
    request = Request([b'payload'], declared)
    monkeypatch.setattr('server.storage.shutil.disk_usage', lambda _: SimpleNamespace(free=16))
    with pytest.raises(HTTPException) as error:
        asyncio.run(storage.receive(request, {'id': 'device'}, 'job.oma'))
    assert error.value.status_code == 507
    assert request.reads == 0
    empty(storage)


def test_space_lost_during_stream_cleans_partial_and_accepts_retry(tmp_path, monkeypatch):
    storage = prepared(tmp_path)
    probes = iter([10000, 10000, 16])
    monkeypatch.setattr('server.storage.shutil.disk_usage', lambda _: SimpleNamespace(free=next(probes)))
    with pytest.raises(HTTPException) as error:
        asyncio.run(storage.receive(Request([b'first', b'second']), {'id': 'device'}, 'job.oma'))
    assert error.value.status_code == 507
    empty(storage)
    monkeypatch.setattr('server.storage.shutil.disk_usage', lambda _: SimpleNamespace(free=10000))
    result = asyncio.run(storage.receive(Request([b'retry']), {'id': 'device'}, 'job.oma'))
    assert result['name'] == 'job.oma'
    assert not list(storage.staging.iterdir())


def test_unverifiable_space_fails_closed(tmp_path, monkeypatch):
    storage = prepared(tmp_path)
    def unavailable(_):
        raise OSError('synthetic capacity probe failure')
    monkeypatch.setattr('server.storage.shutil.disk_usage', unavailable)
    with pytest.raises(HTTPException) as error:
        asyncio.run(storage.receive(Request([b'bytes']), {'id': 'device'}, 'job.oma'))
    assert error.value.status_code == 507
    empty(storage)


@pytest.mark.parametrize('code', [errno.ENOSPC, errno.EDQUOT])
def test_filesystem_full_is_explicit_507(tmp_path, monkeypatch, code):
    storage = prepared(tmp_path)
    def no_space(*args, **kwargs):
        raise OSError(code, 'synthetic write exhaustion')
    monkeypatch.setattr('server.storage.tempfile.mkstemp', no_space)
    with pytest.raises(HTTPException) as error:
        asyncio.run(storage.receive(Request([b'bytes']), {'id': 'device'}, 'job.oma'))
    assert error.value.status_code == 507
    empty(storage)


def test_http_low_space_does_not_mutate_existing_payload(tmp_path, monkeypatch):
    app = create_app(tmp_path)
    with app.state.db.connect(write=True) as connection:
        connection.execute('INSERT INTO devices(id,name,token_hash,created_at) VALUES(?,?,?,?)',
                           ('device', 'Synthetic', fingerprint('test-only-token'), utcnow()))
    sentinel = tmp_path / 'spool' / 'already-accepted'
    sentinel.write_bytes(b'preserve previously accepted payload')
    monkeypatch.setattr('server.storage.shutil.disk_usage', lambda _: SimpleNamespace(free=0))
    with TestClient(app) as client:
        response = client.put('/api/files/job.oma', content=b'bytes', headers={
            'Authorization': 'Bearer test-only-token',
            'X-Content-SHA256': hashlib.sha256(b'bytes').hexdigest()})
        assert response.status_code == 507
    assert sentinel.read_bytes() == b'preserve previously accepted payload'
    empty(app.state.storage)
