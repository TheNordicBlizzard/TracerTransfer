"""Queued API exercised with real SQLite/spool and concurrent HTTP requests."""
import hashlib
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from server.app import create_app
from tests.test_activation import queued, enroll


def put(c, sender, name='part.oma', data=b'hello'):
    return c.put('/api/files/' + name, content=data,
                 headers=sender | {'X-Content-SHA256': hashlib.sha256(data).hexdigest()})


def lease(c, delivery, limit=1):
    response = c.post('/api/delivery/lease', headers=delivery, json={'limit': limit})
    assert response.status_code == 200, response.text
    return response.json()['items']


def ack(c, delivery, item, result='delivered', error=None):
    return c.post('/api/delivery/ack/' + item['id'], headers=delivery,
                  json={'lease_token': item['lease_token'], 'sha256': item['sha256'], 'result': result, 'error': error})


def test_full_queue_receipt_download_ack_and_idempotency(queued):
    app, c, admin = queued
    _, sender = enroll(c, admin)
    _, delivery = enroll(c, admin, 'delivery')
    response = put(c, sender)
    assert response.status_code == 202
    receipt = response.json()
    assert set(receipt) == {'receipt_id', 'sha256', 'name', 'duplicate', 'status'}
    assert receipt['status'] == 'pending' and not receipt['duplicate']
    retry = put(c, sender, 'PART.OMA')
    assert retry.status_code == 202
    assert retry.json() == receipt | {'duplicate': True}
    status_path = '/api/files/status/' + receipt['receipt_id']
    assert c.get(status_path, headers=sender).json()['status'] == 'pending'
    item, = lease(c, delivery)
    assert item['id'] == receipt['receipt_id']
    assert 'lease_hash' not in c.get('/api/deliveries', headers=admin).text
    assert item['lease_token'] not in (app.state.db.data_dir / 'audit.log').read_text()
    download = c.get('/api/delivery/files/' + item['id'], headers=delivery | {'X-Delivery-Lease': item['lease_token']})
    assert download.status_code == 200
    assert download.content == b'hello'
    assert download.headers['x-content-sha256'] == hashlib.sha256(download.content).hexdigest()
    assert ack(c, delivery, item).status_code == 200
    assert ack(c, delivery, item).status_code == 200
    assert c.get(status_path, headers=sender).json()['status'] == 'delivered'
    assert put(c, sender).json() == receipt | {'duplicate': True, 'status': 'delivered'}
    assert lease(c, delivery) == []
    assert (app.state.storage.spool / item['id']).read_bytes() == b'hello'
    assert not (app.state.db.data_dir / 'received').exists()
    assert not list(app.state.storage.staging.iterdir())


def test_all_scopes_and_receipt_ownership(queued):
    _, c, admin = queued
    _, sender = enroll(c, admin)
    _, other = enroll(c, admin)
    _, delivery = enroll(c, admin, 'delivery')
    receipt = put(c, sender).json()
    item, = lease(c, delivery)
    for headers, status in ((other, 404), (delivery, 403), (admin, 401), ({}, 401)):
        assert c.get('/api/files/status/' + receipt['receipt_id'], headers=headers).status_code == status
    assert put(c, delivery).status_code == 403
    assert c.post('/api/delivery/lease', headers=sender, json={'limit': 1}).status_code == 403
    assert c.get('/api/delivery/files/' + item['id'], headers=sender | {'X-Delivery-Lease': item['lease_token']}).status_code == 403
    assert ack(c, sender, item).status_code == 403
    assert c.get('/api/deliveries', headers=sender).status_code == 401


def test_leases_are_exclusive_global_case_insensitive_and_expire(queued):
    app, c, admin = queued
    _, sender = enroll(c, admin)
    _, delivery = enroll(c, admin, 'delivery')
    _, other_delivery = enroll(c, admin, 'delivery')
    assert put(c, sender, 'PART.OMA', b'one').status_code == 202
    assert put(c, sender, 'part.oma', b'two').status_code == 202
    assert put(c, sender, 'unique.vca').status_code == 202
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda headers: lease(c, headers, 10), [delivery, other_delivery]))
    items = [item for batch in results for item in batch]
    assert len(items) == 2
    assert len({i['name'].casefold() for i in items}) == 2
    first = items[0]
    holder = delivery if first in results[0] else other_delivery
    stranger = other_delivery if holder == delivery else delivery
    path = '/api/delivery/files/' + first['id']
    assert c.get(path, headers=stranger | {'X-Delivery-Lease': first['lease_token']}).status_code == 409
    assert ack(c, stranger, first).status_code == 409
    assert c.get(path, headers=holder).status_code == 409
    with app.state.db.connect(write=True) as db:
        db.execute("UPDATE deliveries SET lease_until='2000-01-01T00:00:00+00:00' WHERE id=?", (first['id'],))
    assert c.get(path, headers=holder | {'X-Delivery-Lease': first['lease_token']}).status_code == 409
    assert ack(c, holder, first).status_code == 409
    refreshed, = lease(c, stranger)
    assert refreshed['id'] == first['id']
    assert refreshed['lease_token'] != first['lease_token']
    assert ack(c, holder, first).status_code == 409
    assert ack(c, stranger, refreshed).status_code == 200


@pytest.mark.parametrize('result', ['conflict', 'uncertain'])
def test_terminal_states_not_retried_and_ack_must_match(queued, result):
    _, c, admin = queued
    _, sender = enroll(c, admin)
    _, delivery = enroll(c, admin, 'delivery')
    receipt = put(c, sender).json()
    item, = lease(c, delivery)
    assert ack(c, delivery, item | {'sha256': '0' * 64}).status_code == 409
    assert ack(c, delivery, item, result, 'unsafe error with spaces').status_code == 422
    assert ack(c, delivery, item, result, 'destination_exists').status_code == 200
    assert ack(c, delivery, item, result, 'destination_exists').status_code == 200
    assert ack(c, delivery, item, 'delivered').status_code == 409
    assert ack(c, delivery, item, result, 'changed').status_code == 409
    assert lease(c, delivery) == []
    assert put(c, sender).json()['receipt_id'] == receipt['receipt_id']
    status = c.get('/api/files/status/' + item['id'], headers=sender).json()
    assert status['status'] == result and status['error'] == 'destination_exists'


def test_limits_slots_checksum_and_revoked_lease(queued):
    app, c, admin = queued
    _, sender = enroll(c, admin)
    identity, delivery = enroll(c, admin, 'delivery')
    for invalid in (0, 11, True):
        assert c.post('/api/delivery/lease', headers=delivery, json={'limit': invalid}).status_code == 422
    assert put(c, sender, data=b'x' * 129).status_code == 413
    assert put(c, sender, 'CON.oma').status_code == 400
    assert c.put('/api/files/x.oma', headers=sender | {'X-Content-SHA256': '0' * 64}, content=b'x').status_code == 422
    for _ in range(4):
        app.state.storage.slots.acquire()
    try:
        assert put(c, sender).status_code == 429
    finally:
        for _ in range(4):
            app.state.storage.slots.release()
    assert put(c, sender).status_code == 202
    item, = lease(c, delivery)
    download_path = '/api/delivery/files/' + item['id']
    download_headers = delivery | {'X-Delivery-Lease': item['lease_token']}
    for _ in range(4):
        app.state.delivery.download_slots.acquire()
    try:
        assert c.get(download_path, headers=download_headers).status_code == 429
    finally:
        for _ in range(4):
            app.state.delivery.download_slots.release()
    assert c.get(download_path, headers=download_headers).status_code == 200
    assert c.delete('/api/devices/' + identity['device_id'], headers=admin).status_code == 200
    assert c.get(download_path, headers=download_headers).status_code == 401
    assert ack(c, delivery, item).status_code == 401


def test_parallel_duplicate_persistence_and_no_overwrite(queued):
    app, c, admin = queued
    _, sender = enroll(c, admin)
    _, delivery = enroll(c, admin, 'delivery')
    with ThreadPoolExecutor(max_workers=4) as executor:
        responses = list(executor.map(lambda _: put(c, sender), range(4)))
    assert all(r.status_code == 202 for r in responses)
    assert len({r.json()['receipt_id'] for r in responses}) == 1
    assert sum(not r.json()['duplicate'] for r in responses) == 1
    assert put(c, sender, data=b'changed').status_code == 202
    paths = list(app.state.storage.spool.iterdir())
    assert len(paths) == 2
    assert {p.read_bytes() for p in paths} == {b'hello', b'changed'}
    new_app = create_app(app.state.db.data_dir, delivery_mode='queued')
    with TestClient(new_app) as restarted:
        assert put(restarted, sender).json()['receipt_id'] == responses[0].json()['receipt_id']
        assert len(lease(restarted, delivery, 10)) == 1  # same operational name


def test_queued_cannot_point_to_operational_output(tmp_path):
    with pytest.raises(ValueError, match='Queued'):
        create_app(tmp_path / 'state', delivery_mode='queued', output_dir=tmp_path)
    with pytest.raises(ValueError, match='mode'):
        create_app(tmp_path / 'state', delivery_mode='invalid')


def test_spool_corruption_does_not_leak_or_overwrite(queued):
    app, c, admin = queued
    _, sender = enroll(c, admin)
    _, delivery = enroll(c, admin, 'delivery')
    assert put(c, sender).status_code == 202
    item, = lease(c, delivery)
    (app.state.storage.spool / item['id']).write_bytes(b'wrong')
    assert c.get('/api/delivery/files/' + item['id'], headers=delivery | {'X-Delivery-Lease': item['lease_token']}).status_code == 409
    assert (app.state.storage.spool / item['id']).read_bytes() == b'wrong'
    assert put(c, sender).json()['receipt_id'] == item['id']


def test_queue_sql_failure_compensates_link(queued):
    import sqlite3
    app, c, admin = queued
    _, sender = enroll(c, admin)
    with app.state.db.connect(write=True) as db:
        db.execute("CREATE TRIGGER reject_delivery BEFORE INSERT ON deliveries BEGIN SELECT RAISE(ABORT,'test failure'); END")
    with pytest.raises(sqlite3.IntegrityError):
        put(c, sender)
    assert not list(app.state.storage.spool.iterdir())
    assert not list(app.state.storage.staging.iterdir())


def test_real_cli_queued_http(tmp_path):
    import json
    import socket
    import subprocess
    import sys
    import time
    from pathlib import Path
    import httpx
    from tests.test_activation import PASSWORD
    bootstrap = tmp_path / 'bootstrap.json'
    bootstrap.write_text(json.dumps({'username': 'admin', 'password': PASSWORD}), encoding='utf-8')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    process = subprocess.Popen([sys.executable, '-m', 'server', '--headless', '--delivery-mode', 'queued',
                                '--port', str(port), '--data-dir', str(tmp_path), '--bootstrap-file', str(bootstrap)],
                               cwd=Path(__file__).resolve().parents[1], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 15
        with httpx.Client(base_url=f'http://127.0.0.1:{port}', trust_env=False, timeout=2) as c:
            while True:
                try:
                    if c.get('/health').status_code == 200:
                        break
                except httpx.TransportError:
                    pass
                if process.poll() is not None:
                    pytest.fail(process.communicate()[1].decode(errors='replace'))
                if time.monotonic() > deadline:
                    pytest.fail('Queued CLI startup timed out')
                time.sleep(.1)
            token = c.post('/api/login', json={'username': 'admin', 'password': PASSWORD}).json()['token']
            admin = {'Authorization': 'Bearer ' + token}
            assert c.post('/api/devices', headers=admin, json={'name': 'bypass'}).status_code == 403
            _, sender = enroll(c, admin)
            _, delivery = enroll(c, admin, 'delivery')
            receipt = put(c, sender)
            assert receipt.status_code == 202
            item, = lease(c, delivery)
            downloaded = c.get('/api/delivery/files/' + item['id'], headers=delivery | {'X-Delivery-Lease': item['lease_token']})
            assert downloaded.content == b'hello'
            assert hashlib.sha256(downloaded.content).hexdigest() == item['sha256']
            assert ack(c, delivery, item).status_code == 200
            assert c.get('/api/files/status/' + item['id'], headers=sender).json()['status'] == 'delivered'
            assert c.get('/api/status', headers=admin).json()['delivery_mode'] == 'queued'
            assert not bootstrap.exists()
    finally:
        process.terminate()
        process.communicate(timeout=10)
