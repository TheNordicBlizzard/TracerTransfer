"""Contract-level enrollment, provenance, isolation and restart tests."""
import re
import secrets
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from server.app import create_app
from server.auth import fingerprint
from server.users import bootstrap_admin

PASSWORD = 'test-pass-1234'


@pytest.fixture
def queued(tmp_path):
    app = create_app(tmp_path, max_size=128, delivery_mode='queued')
    bootstrap_admin(app.state.db, 'admin', PASSWORD)
    with TestClient(app) as client:
        token = client.post('/api/login', json={'username': 'admin', 'password': PASSWORD}).json()['token']
        yield app, client, {'Authorization': 'Bearer ' + token}


def code(client, admin, role='sender'):
    response = client.post('/api/activations', headers=admin, json={'name': 'Tracer', 'role': role})
    assert response.status_code == 201, response.text
    return response.json()


def enrollment(activation):
    return {'code': activation['code'], 'installation_id': uuid.uuid4().hex,
            'installation_secret': secrets.token_urlsafe(32), 'name': 'Desk'}


def enroll(client, admin, role='sender'):
    activation = code(client, admin, role)
    payload = enrollment(activation)
    response = client.post('/api/activate', json=payload)
    assert response.status_code == 200, response.text
    return response.json(), {'Authorization': 'Bearer ' + payload['installation_secret']}


def test_code_single_display_hash_only_and_role(queued):
    app, c, admin = queued
    activation = code(c, admin, 'delivery')
    assert re.fullmatch(r'TT-[A-F0-9]{40}', activation['code'])
    payload = enrollment(activation)
    result = c.post('/api/activate', json=payload)
    assert result.status_code == 200
    assert result.json()['role'] == 'delivery'
    assert set(result.json()) == {'device_id', 'activation_id', 'role'}
    for path in ('/api/activations', '/api/devices', '/api/status'):
        response = c.get(path, headers=admin)
        assert 'code_hash' not in response.text and 'token_hash' not in response.text
        assert activation['code'] not in response.text and payload['installation_secret'] not in response.text
    with app.state.db.connect() as db:
        assert db.execute('SELECT code_hash FROM activations').fetchone()[0] == fingerprint(activation['code'])
        assert db.execute('SELECT token_hash FROM devices').fetchone()[0] == fingerprint(payload['installation_secret'])
    raw = app.state.db.path.read_bytes() + (app.state.db.data_dir / 'audit.log').read_bytes()
    assert payload['installation_secret'].encode() not in raw
    assert activation['code'].encode() not in raw


def test_atomic_retry_and_binding(queued):
    app, c, admin = queued
    activation = code(c, admin)
    payload = enrollment(activation)
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(lambda _: c.post('/api/activate', json=payload), range(4)))
    assert all(r.status_code == 200 for r in results)
    assert len({r.json()['device_id'] for r in results}) == 1
    assert c.post('/api/activate', json=payload | {'installation_secret': secrets.token_urlsafe(32)}).status_code == 401
    assert c.post('/api/activate', json=payload | {'installation_id': uuid.uuid4().hex}).status_code == 401
    # Expiry affects first use, not a response lost after successful redemption.
    with app.state.db.connect(write=True) as db:
        db.execute("UPDATE activations SET expires_at='2000-01-01T00:00:00+00:00'")
    assert c.post('/api/activate', json=payload).json() == results[0].json()
    assert c.delete('/api/activations/' + activation['id'], headers=admin).status_code == 409
    assert c.delete('/api/devices/' + results[0].json()['device_id'], headers=admin).status_code == 200
    assert c.post('/api/activate', json=payload).status_code == 401


def test_one_winner_under_concurrent_redemption(queued):
    app, c, admin = queued
    activation = code(c, admin)
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(lambda _: c.post('/api/activate', json=enrollment(activation)), range(4)))
    assert sorted(r.status_code for r in results) == [200, 401, 401, 401]
    with app.state.db.connect() as db:
        assert db.execute('SELECT count(*) FROM devices').fetchone()[0] == 1


@pytest.mark.parametrize('kind', ['expired', 'revoked'])
def test_expired_or_revoked_code(queued, kind):
    app, c, admin = queued
    activation = code(c, admin)
    if kind == 'expired':
        with app.state.db.connect(write=True) as db:
            db.execute("UPDATE activations SET expires_at='2000-01-01T00:00:00+00:00'")
    else:
        assert c.delete('/api/activations/' + activation['id'], headers=admin).status_code == 200
    assert c.post('/api/activate', json=enrollment(activation)).status_code == 401


@pytest.mark.parametrize('mutation', ['lowercase', 'space', 'bad-id', 'bad-secret', 'role'])
def test_literal_validation_no_secret_echo(queued, mutation):
    _, c, admin = queued
    payload = enrollment(code(c, admin))
    if mutation == 'lowercase':
        payload['code'] = payload['code'].lower()
    elif mutation == 'space':
        payload['code'] += ' '
    elif mutation == 'bad-id':
        payload['installation_id'] = str(uuid.uuid4())
    elif mutation == 'bad-secret':
        payload['installation_secret'] = 'tiny-secret'
    else:
        payload['role'] = 'admin'
    response = c.post('/api/activate', json=payload)
    assert response.status_code == 422
    assert payload['code'] not in response.text
    assert payload['installation_secret'] not in response.text


def test_installation_cannot_enroll_twice_or_bypass(queued):
    app, c, admin = queued
    activation = code(c, admin)
    payload = enrollment(activation)
    assert c.post('/api/activate', json=payload).status_code == 200
    new_code = code(c, admin)
    assert c.post('/api/activate', json=payload | {'code': new_code['code']}).status_code == 409
    assert c.post('/api/devices', headers=admin, json={'name': 'bypass'}).status_code == 403
    with app.state.db.connect(write=True) as db:
        db.execute("INSERT INTO devices(id,name,token_hash,created_at) VALUES('legacy','legacy',?,'now')", (fingerprint('legacy'),))
    assert c.post('/api/heartbeat', headers={'Authorization': 'Bearer legacy'}, json={'pending': 0}).status_code == 403


def test_scopes_and_memory_sessions_survive_no_restart(queued):
    app, c, admin = queued
    _, sender = enroll(c, admin)
    _, delivery = enroll(c, admin, 'delivery')
    for headers in (sender, delivery, {}):
        for path in ('/api/activations', '/api/devices', '/api/status'):
            assert c.get(path, headers=headers).status_code == 401
        assert c.post('/api/activations', headers=headers, json={'name': 'x', 'role': 'sender'}).status_code == 401
    with app.state.db.connect() as db:
        assert db.execute('SELECT count(*) FROM sessions').fetchone()[0] == 0
    restarted = create_app(app.state.db.data_dir, delivery_mode='queued')
    with TestClient(restarted) as other:
        assert other.get('/api/me', headers=admin).status_code == 401
        assert other.post('/api/heartbeat', headers=sender, json={'pending': 0}).status_code == 200


def test_activation_rate_limit_and_expiry_bounds(queued):
    _, c, admin = queued
    for hours in (0, 721, True):
        assert c.post('/api/activations', headers=admin, json={'name': 'x', 'role': 'sender', 'expires_hours': hours}).status_code == 422
    payload = enrollment({'code': 'TT-' + '0' * 40})
    for _ in range(30):
        assert c.post('/api/activate', json=payload).status_code == 401
    assert c.post('/api/activate', json=payload).status_code == 429


def test_legacy_schema_is_migrated_without_changing_device_credentials(tmp_path):
    import sqlite3
    from server.database import Database
    connection = sqlite3.connect(tmp_path / 'tracer.sqlite3')
    connection.executescript('''
        CREATE TABLE devices(id TEXT PRIMARY KEY,name TEXT NOT NULL,token_hash TEXT NOT NULL UNIQUE,
            enabled INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL,last_seen TEXT,
            pending INTEGER NOT NULL DEFAULT 0,error TEXT);
        INSERT INTO devices(id,name,token_hash,created_at) VALUES('old','Old','fingerprint','now');
    ''')
    connection.close()
    db = Database(tmp_path)
    with db.connect() as connection:
        device = dict(connection.execute('SELECT * FROM devices').fetchone())
    assert device['token_hash'] == 'fingerprint'
    assert device['role'] == 'sender' and device['activation_id'] is None
    Database(tmp_path)  # idempotent migration
