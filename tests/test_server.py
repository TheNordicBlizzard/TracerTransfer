import hashlib
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import pytest

PASSWORD = 'test-pass-1234'


@pytest.fixture
def service(tmp_path):
    from server.app import create_app
    from server.users import bootstrap_admin
    app = create_app(tmp_path, max_size=128)
    bootstrap_admin(app.state.db, 'admin', PASSWORD)
    with TestClient(app) as c:
        token = c.post('/api/login', json={'username':'admin','password':PASSWORD}).json()['token']
        yield app, c, {'Authorization':'Bearer '+token}


def device(c, headers):
    r = c.post('/api/devices', headers=headers, json={'name':'Tracer 1'})
    assert r.status_code == 201
    return r.json()


def upload(c, token, name='sample.oma', body=b'hello', sha=None, **kw):
    return c.put('/api/files/'+name, content=body, headers={
        'Authorization':'Bearer '+token,
        'X-Content-SHA256':sha or hashlib.sha256(body).hexdigest(), **kw})


def test_permissions_sessions_and_last_admin(service):
    app, c, admin = service
    assert c.post('/api/users', headers=admin, json={'username':'operator','password':PASSWORD,'role':'operator'}).status_code == 201
    assert c.post('/api/users', headers=admin, json={'username':'operator','password':PASSWORD,'role':'operator'}).status_code == 409
    operator_token = c.post('/api/login', json={'username':'operator','password':PASSWORD}).json()['token']
    operator = {'Authorization':'Bearer '+operator_token}
    assert c.get('/api/status', headers=operator).status_code == 200
    assert c.post('/api/devices', headers=operator, json={'name':'x'}).status_code == 403
    assert c.patch('/api/users/admin', headers=admin, json={'enabled':False}).status_code == 409
    assert c.patch('/api/users/admin', headers=admin, json={'role':'operator'}).status_code == 409
    assert c.patch('/api/users/operator', headers=admin, json={'password':'a-new-password-long'}).status_code == 200
    assert c.get('/api/me', headers=operator).status_code == 401
    fresh = c.post('/api/login', json={'username':'operator','password':'a-new-password-long'}).json()['token']
    assert c.patch('/api/users/operator', headers=admin, json={'enabled':False}).status_code == 200
    assert c.get('/api/me', headers={'Authorization':'Bearer '+fresh}).status_code == 401
    for session in app.state.auth.sessions.values():
        session['expires_at'] = '2000-01-01T00:00:00+00:00'
    assert c.get('/api/me', headers=admin).status_code == 401


def test_login_rate_limit_and_secret_hashing(service):
    app,c,admin = service
    for i in range(9):
        assert c.post('/api/login', json={'username':'admin','password':'wrong'}).status_code == 401
    assert c.post('/api/login', json={'username':'admin','password':PASSWORD}, headers={'X-Forwarded-For':'somewhere-else'}).status_code == 429
    with app.state.db.connect() as db:
        row = db.execute('SELECT * FROM users').fetchone()
        assert row['password_hash'].startswith('$argon2id$')
        raw_token = admin['Authorization'].split()[1]
        assert db.execute('SELECT count(*) FROM sessions').fetchone()[0] == 0
        assert raw_token not in app.state.auth.sessions
    assert PASSWORD not in (app.state.db.data_dir/'audit.log').read_text()
    assert raw_token not in (app.state.db.data_dir/'audit.log').read_text()


def test_upload_hash_idempotency_revoke_and_heartbeat(service):
    app,c,admin = service
    d = device(c,admin)
    headers = {'Authorization':'Bearer '+d['token']}
    assert c.get('/api/status',headers=headers).status_code == 401
    assert c.post('/api/heartbeat',headers=headers,json={'pending':3,'error':None}).status_code == 200
    assert c.post('/api/heartbeat',headers=headers,json={'pending':-1}).status_code == 422
    first = upload(c,d['token'])
    assert first.status_code == 200
    assert not first.json()['duplicate']
    assert upload(c,d['token']).json()['duplicate']
    assert upload(c,d['token'],body=b'wrong',sha=first.json()['sha256']).status_code == 422
    assert upload(c,d['token'],body=b'changed').status_code == 200
    target = app.state.db.data_dir/'received'/d['id']/first.json()['sha256']/'sample.oma'
    assert target.read_bytes() == b'hello'
    status = c.get('/api/status',headers=admin).json()
    assert len(status['files']) == 2
    assert status['devices'][0]['pending'] == 3
    assert 'token' not in status['devices'][0]
    assert 'token_hash' not in status['devices'][0]
    assert c.delete('/api/devices/'+d['id'],headers=admin).status_code == 200
    assert upload(c,d['token']).status_code == 401
    assert c.post('/api/heartbeat',headers=headers,json={'pending':0}).status_code == 401
    assert not list((app.state.db.data_dir/'staging').iterdir())


@pytest.mark.parametrize('name',['CON.oma','aux.VCA','LPT1.oma','COM0.oma','a.exe','a.oma.','bad%5Cname.oma','..%5Cevil.oma','%2Fetc%2Fpasswd.oma','a%3Aads.oma','a%00.oma','a'*121+'.oma'])
def test_bad_filenames(service,name):
    _,c,admin = service
    d = device(c,admin)
    assert upload(c,d['token'],name=name).status_code in (400,404)


def test_limits_streaming_and_checksum(service):
    app,c,admin = service
    d=device(c,admin)
    assert upload(c,d['token'],body=b'').status_code == 400
    assert upload(c,d['token'],body=b'x'*129).status_code == 413
    assert upload(c,d['token'],sha='not-a-sha').status_code == 400
    assert upload(c,d['token'],**{'Content-Length':'no'}).status_code == 400
    assert upload(c,d['token'],**{'Content-Length':'8'}).status_code == 400
    r=c.put('/api/files/stream.VCA',content=iter([b'x'*70,b'y'*70]),headers={'Authorization':'Bearer '+d['token'],'X-Content-SHA256':'0'*64})
    assert r.status_code == 413
    assert not list((app.state.db.data_dir/'staging').iterdir())
    assert upload(c,d['token'],body=b'x'*128).status_code == 200
    assert upload(c,admin['Authorization'].split()[1]).status_code == 401


def test_parallel_duplicate_uploads(service):
    _,c,admin=service
    d=device(c,admin)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results=list(pool.map(lambda _:upload(c,d['token']),range(4)))
    assert all(r.status_code==200 for r in results)
    assert sum(not r.json()['duplicate'] for r in results)==1


def test_bootstrap_file_cli_and_lock(tmp_path):
    from server.__main__ import consume_bootstrap, InstanceLock
    from server.database import Database
    db=Database(tmp_path)
    secret=tmp_path/'bootstrap.json'
    secret.write_text(json.dumps({'username':'admin','password':PASSWORD}))
    consume_bootstrap(db,secret)
    assert not secret.exists()
    with db.connect() as c:
        assert c.execute('SELECT role FROM users').fetchone()[0]=='admin'
    with InstanceLock(tmp_path):
        with pytest.raises(RuntimeError):
            with InstanceLock(tmp_path):
                pass

from fastapi.testclient import TestClient


def test_upload_capacity(service):
    app,c,admin=service
    d=device(c,admin)
    for _ in range(4):
        assert app.state.storage.slots.acquire(blocking=False)
    try:
        assert upload(c,d['token']).status_code==429
    finally:
        for _ in range(4):
            app.state.storage.slots.release()
    assert upload(c,d['token']).status_code==200


def test_real_cli_http(tmp_path):
    import os
    import socket
    import time
    import httpx
    from pathlib import Path
    secret=tmp_path/'bootstrap.json'
    secret.write_text(json.dumps({'username':'admin','password':PASSWORD}))
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0))
        port=sock.getsockname()[1]
    proc=subprocess.Popen([sys.executable,'-m','server','--headless','--host','127.0.0.1',
                           '--port',str(port),'--data-dir',str(tmp_path),'--bootstrap-file',str(secret)],
                          cwd=Path(__file__).resolve().parents[1],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    try:
        deadline=time.monotonic()+15
        with httpx.Client(base_url=f'http://127.0.0.1:{port}',trust_env=False,timeout=2) as c:
            while True:
                try:
                    if c.get('/health').status_code==200:
                        break
                except httpx.TransportError:
                    pass
                if proc.poll() is not None:
                    pytest.fail(proc.communicate()[1].decode(errors='replace'))
                if time.monotonic()>deadline:
                    pytest.fail('Server startup timed out')
                time.sleep(.1)
            assert not secret.exists()
            login=c.post('/api/login',json={'username':'admin','password':PASSWORD})
            assert login.status_code==200
            d=device(c,{'Authorization':'Bearer '+login.json()['token']})
            assert upload(c,d['token']).status_code==200
            assert c.get('/').status_code==200
    finally:
        proc.terminate()
        proc.communicate(timeout=10)


def test_cli_refuses_nonloopback(tmp_path):
    result=subprocess.run([sys.executable,'-m','server','--headless','--host','0.0.0.0',
                           '--data-dir',str(tmp_path)],capture_output=True,text=True)
    assert result.returncode!=0
    assert 'loopback' in result.stderr


def test_bootstrap_login(tmp_path):
    from server.app import create_app
    from server.users import bootstrap_admin
    app = create_app(tmp_path)
    bootstrap_admin(app.state.db, 'admin', 'correct-horse-battery')
    with TestClient(app) as c:
        assert c.get('/health').json() == {'ok': True}
        assert c.get('/api/me').status_code == 401
        r = c.post('/api/login', json={'username':'admin','password':'correct-horse-battery'})
        assert r.status_code == 200
        assert r.json()['role'] == 'admin'
        assert c.get('/api/me', headers={'Authorization':'Bearer '+r.json()['token']}).json() == {'username':'admin','role':'admin'}
