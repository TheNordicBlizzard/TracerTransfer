"""Isolated Windows client tests: real DPAPI, queue/GUI, and trusted loopback HTTPS."""
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import ssl
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from client.activation import ActivationService, ActivationError
from client.agent import Agent
from client.config_ui import ConfigurationService
from client.state import Config, COMPANY_SERVER, read_config, save_config, unprotect_token
from tests.support import certificate
from tests.test_client import tk_root

CODE = 'TT-' + 'A1' * 20


def enroll(tmp_path, handler=None):
    source = tmp_path / 'source'
    source.mkdir()
    save_config(tmp_path, Config(source_dir=str(source), stable_seconds=0, poll_seconds=0.1))
    def default(request):
        return httpx.Response(200, json={'device_id': 'sender-one', 'activation_id': 'activation-one', 'role': 'sender'})
    service = ActivationService(tmp_path, transport=httpx.MockTransport(handler or default))
    service.activate(CODE, 'Local test')
    return source


def test_activation_pre_persists_dpapi_and_retries_same_identity(tmp_path):
    requests = []
    def handle(request):
        data = json.loads(request.content)
        disk = read_config(tmp_path)
        assert disk.installation_id == data['installation_id']
        assert len(disk.installation_id) == 32
        assert unprotect_token(disk.protected_token) == data['installation_secret']
        assert len(data['installation_secret']) == 43
        persisted = (tmp_path / 'config.json').read_text()
        assert data['installation_secret'] not in persisted
        assert CODE not in persisted
        requests.append(data)
        if len(requests) == 1:
            raise httpx.ReadTimeout('response lost', request=request)
        return httpx.Response(200, json={'device_id': 'sender', 'activation_id': 'activation', 'role': 'sender'})
    service = ActivationService(tmp_path, transport=httpx.MockTransport(handle))
    with pytest.raises(ActivationError, match='mesmo código'):
        service.activate(CODE, 'Client')
    assert not read_config(tmp_path).activated
    restarted = ActivationService(tmp_path, transport=httpx.MockTransport(handle))
    assert restarted.activate(CODE, 'Client').activated
    assert requests[0] == requests[1]
    with pytest.raises(ActivationError, match='já está'):
        restarted.activate(CODE, 'Client')


@pytest.mark.parametrize('code', [CODE.lower(), CODE + ' ', ' ' + CODE, 'TT-' + 'G' * 40, 'TT-ABC', ''])
def test_invalid_activation_code_is_not_normalized(tmp_path, code):
    def handle(request):
        pytest.fail('Invalid code must not cause a request')
    with pytest.raises(ActivationError, match='Código inválido'):
        ActivationService(tmp_path, transport=httpx.MockTransport(handle)).activate(code, 'Client')
    assert not (tmp_path / 'config.json').exists()


@pytest.mark.parametrize('status', [401, 403, 409, 410, 429, 503])
def test_activation_errors_do_not_expose_server_body(tmp_path, status):
    marker = 'sensitive-response-body'
    def handle(request):
        return httpx.Response(status, text=marker)
    with pytest.raises(ActivationError) as caught:
        ActivationService(tmp_path, transport=httpx.MockTransport(handle)).activate(CODE, 'Client')
    assert marker not in str(caught.value) and CODE not in str(caught.value)
    assert read_config(tmp_path).installation_id
    assert not read_config(tmp_path).activated


def test_delivery_role_cannot_activate_sender(tmp_path):
    def handle(request):
        return httpx.Response(200, json={'device_id': 'delivery', 'activation_id': 'a', 'role': 'delivery'})
    with pytest.raises(ActivationError, match='remetente'):
        enroll(tmp_path, handle)
    assert not read_config(tmp_path).activated


def test_pending_activation_never_sends_heartbeat_or_backlog(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    old = source / 'old.oma'
    old.write_bytes(b'old backlog')
    service = ConfigurationService(tmp_path)
    service.folder(str(source))
    calls = []
    def handle(request):
        calls.append(request.url.path)
        if request.url.path == '/api/activate':
            return httpx.Response(503)
        pytest.fail('No heartbeat/upload before activation')
    with pytest.raises(ActivationError):
        ActivationService(tmp_path, transport=httpx.MockTransport(handle)).activate(CODE, 'Client')
    with_agent = Agent(tmp_path, transport=httpx.MockTransport(handle))
    try:
        assert not with_agent.run_once().configured
        assert list(with_agent.spool.iterdir()) == []
    finally:
        with_agent.close()
    assert calls == ['/api/activate']


def test_receipt_persists_restart_spool_until_delivered_and_backlog_ignored(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'old.oma').write_bytes(b'backlog')
    save_config(tmp_path, Config(source_dir=str(source), stable_seconds=0, poll_seconds=0.1))
    ActivationService(tmp_path, transport=httpx.MockTransport(lambda r: httpx.Response(200,
        json={'device_id': 'd', 'activation_id': 'a', 'role': 'sender'}))).activate(CODE, 'Client')
    new = source / 'new.vca'
    new.write_bytes(b'queued bytes')
    sha = hashlib.sha256(new.read_bytes()).hexdigest()
    clock = [time.time()]
    calls = []
    delivered = [False]
    def handle(request):
        calls.append((request.method, request.url.path))
        assert request.headers['authorization'] == 'Bearer ' + unprotect_token(read_config(tmp_path).protected_token)
        if request.method == 'PUT':
            assert request.read() == b'queued bytes'
            return httpx.Response(202, json={'receipt_id': 'r-one', 'sha256': sha,
                'name': 'new.vca', 'status': 'pending', 'duplicate': False})
        if request.method == 'GET':
            assert request.url.path == '/api/files/status/r-one'
            return httpx.Response(200, json={'receipt_id': 'r-one', 'sha256': sha,
                'name': 'new.vca', 'status': 'delivered' if delivered[0] else 'pending', 'error': None})
        return httpx.Response(200, json={'ok': True})
    a = Agent(tmp_path, transport=httpx.MockTransport(handle), clock=lambda: clock[0])
    a.run_once()
    clock[0] += 1
    result = a.run_once()
    assert result.sent == 0 and result.awaiting_delivery == 1 and result.pending == 1
    record = a.db.execute('SELECT state,receipt_id,snapshot FROM queue').fetchone()
    assert record[:2] == ('awaiting', 'r-one') and Path(record[2]).exists()
    a.close()
    a = Agent(tmp_path, transport=httpx.MockTransport(handle), clock=lambda: clock[0])
    try:
        assert Path(record[2]).exists()
        clock[0] += 1
        assert a.run_once().sent == 0
        delivered[0] = True
        clock[0] += 1
        result = a.run_once()
        assert result.sent == 1 and result.pending == 0
        assert not Path(record[2]).exists()
        assert sum(method == 'PUT' for method, _ in calls) == 1
        assert (source / 'old.oma').read_bytes() == b'backlog'
        assert new.read_bytes() == b'queued bytes'
    finally:
        a.close()


@pytest.mark.parametrize('terminal', ['conflict', 'uncertain'])
def test_terminal_receipts_are_visible_preserved_and_not_reuploaded(tmp_path, terminal):
    source = enroll(tmp_path)
    (source / 'part.oma').write_bytes(b'data')
    sha = hashlib.sha256(b'data').hexdigest()
    uploads = []
    def handle(request):
        if request.method == 'PUT':
            uploads.append(request.read())
            return httpx.Response(202, json={'receipt_id': 'r', 'sha256': sha,
                'name': 'part.oma', 'status': terminal, 'duplicate': True})
        return httpx.Response(200, json={})
    clock = [time.time()]
    a = Agent(tmp_path, transport=httpx.MockTransport(handle), clock=lambda: clock[0])
    a.run_once()
    clock[0] += 1
    a.run_once()
    a.close()
    a = Agent(tmp_path, transport=httpx.MockTransport(handle), clock=lambda: clock[0])
    try:
        for _ in range(3):
            clock[0] += 10
            result = a.run_once()
        assert result.sent == 0 and result.pending == 1
        assert getattr(result, 'conflicts' if terminal == 'conflict' else 'uncertain') == 1
        assert len(list(a.spool.iterdir())) == 1
        assert uploads == [b'data']
        assert result.error
    finally:
        a.close()


@pytest.mark.parametrize('response_status', [200, 401, 409])
def test_no_silent_downgrade_or_secret_errors(tmp_path, response_status):
    source = enroll(tmp_path)
    (source / 'part.oma').write_bytes(b'data')
    def handle(request):
        if request.method == 'PUT':
            return httpx.Response(response_status, json={'sha256': hashlib.sha256(b'data').hexdigest(),
                'detail': 'DO NOT DISPLAY BODY'})
        return httpx.Response(200, json={})
    clock = [time.time()]
    a = Agent(tmp_path, transport=httpx.MockTransport(handle), clock=lambda: clock[0])
    try:
        a.run_once()
        clock[0] += 1
        result = a.run_once()
        assert result.pending == 1 and result.sent == 0
        assert len(list(a.spool.iterdir())) == 1
        assert 'DO NOT DISPLAY BODY' not in result.error
        assert unprotect_token(read_config(tmp_path).protected_token) not in result.error
        if response_status == 409:
            assert result.conflicts == 1 and 'Conflito' in result.error
        if response_status == 401:
            assert 'revogada' in result.error
    finally:
        a.close()


def test_local_controls_offline_and_startup_dependency_only(tmp_path, monkeypatch):
    import client.config_ui as module
    source = enroll(tmp_path)
    before = read_config(tmp_path)
    service = ConfigurationService(tmp_path)
    service.pause()
    assert read_config(tmp_path).paused
    another = tmp_path / 'another'
    another.mkdir()
    service.folder(str(another))
    calls = []
    monkeypatch.setattr(module, 'set_startup', lambda data_dir, enabled: calls.append((data_dir, enabled)))
    service.startup(True)
    after = read_config(tmp_path)
    assert after.startup_enabled and after.source_dir == str(another.resolve())
    assert after.protected_token == before.protected_token and after.device_id == before.device_id
    assert after.server_url == COMPANY_SERVER
    assert calls == [(tmp_path, True)]  # No real HKCU Run value is written.


def test_gui_io_does_not_block_tk_and_has_no_admin_or_server_inputs(tmp_path, monkeypatch, tk_root):
    from client.config_ui import ClientUI
    monkeypatch.setattr(ClientUI, '_start_tray', lambda self: None)
    agent = Agent(tmp_path)
    import tkinter as tk
    ui = ClientUI(agent, root=tk.Toplevel(tk_root))
    entered, release = threading.Event(), threading.Event()
    try:
        ui.root.update()
        assert ui.admin_buttons == []
        assert not hasattr(ui, 'password') and not hasattr(ui, 'url') and not hasattr(ui, 'ca')
        assert str(ui.local_buttons[3]['state']) == 'normal'
        def blocking_io():
            entered.set()
            release.wait(3)
        ui._submit(blocking_io)
        assert entered.wait(2)
        assert str(ui.local_buttons[4]['state']) == 'normal'
        tick = []
        ui.root.after(1, lambda: tick.append(True))
        deadline = time.monotonic() + 1
        while not tick and time.monotonic() < deadline:
            ui.root.update()
            time.sleep(0.005)
        assert tick == [True]
    finally:
        release.set()
        ui.shutdown()
        ui.worker.join(3)
        agent.close()


def test_real_tls_activation_and_receipt_polling(tmp_path):
    cert, key = certificate(tmp_path)
    server_state = {'identity': None, 'uploads': [], 'delivered': False, 'status_reads': 0}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def respond(self, status, value):
            body = json.dumps(value).encode()
            self.send_response(status)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def do_POST(self):
            body = self.rfile.read(int(self.headers.get('Content-Length', '0')))
            if self.path == '/api/activate':
                data = json.loads(body)
                assert data['code'] == CODE
                assert read_config(tmp_path).installation_id == data['installation_id']
                server_state['identity'] = data['installation_secret']
                self.respond(200, {'device_id': 'tls-sender', 'activation_id': 'tls-activation', 'role': 'sender'})
            else:
                assert self.headers['Authorization'] == 'Bearer ' + server_state['identity']
                self.respond(200, {'ok': True})
        def do_PUT(self):
            assert self.headers['Authorization'] == 'Bearer ' + server_state['identity']
            body = self.rfile.read(int(self.headers['Content-Length']))
            server_state['uploads'].append(body)
            digest = hashlib.sha256(body).hexdigest()
            assert self.headers['X-Content-SHA256'] == digest
            server_state['sha'] = digest
            self.respond(202, {'receipt_id': 'tls-r', 'sha256': digest, 'name': 'tls.oma',
                                'duplicate': False, 'status': 'pending'})
        def do_GET(self):
            assert self.path == '/api/files/status/tls-r'
            assert self.headers['Authorization'] == 'Bearer ' + server_state['identity']
            server_state['status_reads'] += 1
            self.respond(200, {'receipt_id': 'tls-r', 'sha256': server_state['sha'], 'name': 'tls.oma',
                                'status': 'delivered' if server_state['delivered'] else 'pending', 'error': None})
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    source = tmp_path / 'source'
    source.mkdir()
    save_config(tmp_path, Config(source_dir=str(source), poll_seconds=0.1, stable_seconds=0))
    url = f'https://127.0.0.1:{server.server_port}'
    agent = None
    try:
        with pytest.raises(ActivationError):
            ActivationService(tmp_path / 'untrusted', server_url=url).activate(CODE, 'TLS client')
        # Changing CA is not allowed for the same provenance; use an independent
        # installation for the intentional failed-trust probe.
        cfg = ActivationService(tmp_path, server_url=url, ca_file=str(cert)).activate(CODE, 'TLS client')
        assert cfg.activated
        payload = b'real trusted TLS queued transfer'
        (source / 'tls.oma').write_bytes(payload)
        clock = [time.time()]
        agent = Agent(tmp_path, clock=lambda: clock[0])
        agent.run_once()
        clock[0] += 1
        assert agent.run_once().awaiting_delivery == 1
        clock[0] += 1
        assert agent.run_once().sent == 0
        server_state['delivered'] = True
        clock[0] += 1
        result = agent.run_once()
        assert result.sent == 1 and result.pending == 0
        assert server_state['uploads'] == [payload]
        assert server_state['status_reads'] == 2
        assert (source / 'tls.oma').read_bytes() == payload
    finally:
        if agent:
            agent.close()
        server.shutdown()
        server.server_close()
        thread.join(5)


def test_client_against_real_queued_backend_over_https(tmp_path):
    """Exercise actual sender API, durable receipt and delivery acknowledgement."""
    import secrets
    import uuid
    import uvicorn
    from server.app import create_app
    from server.users import bootstrap_admin
    from client.auth import http_client
    from tests.support import free_port
    server_dir = tmp_path / 'backend'
    app = create_app(server_dir, delivery_mode='queued')
    password = 'test-pass-1234'
    bootstrap_admin(app.state.db, 'testadmin', password)
    cert, key = certificate(tmp_path)
    port = free_port()
    server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port,
        ssl_certfile=str(cert), ssl_keyfile=str(key), log_level='error', access_log=False))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    agent = None
    try:
        deadline = time.monotonic() + 10
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert server.started
        url = f'https://127.0.0.1:{port}'
        client_dir = tmp_path / 'sender'
        source = tmp_path / 'source'
        source.mkdir()
        (source / 'backlog.oma').write_bytes(b'must not send')
        save_config(client_dir, Config(source_dir=str(source), poll_seconds=0.1, stable_seconds=0))
        with http_client(url, str(cert)) as http:
            response = http.post('/api/login', json={'username': 'testadmin', 'password': password})
            assert response.status_code == 200
            admin = {'Authorization': 'Bearer ' + response.json()['token']}
            response = http.post('/api/activations', headers=admin,
                                 json={'name': 'TLS sender', 'role': 'sender', 'expires_hours': 24})
            assert response.status_code == 201
            cfg = ActivationService(client_dir, server_url=url, ca_file=str(cert)).activate(response.json()['code'], 'TLS sender')
            assert cfg.activated
            assert http.post('/api/devices', headers=admin, json={'name': 'Legacy blocked'}).status_code == 403
            response = http.post('/api/activations', headers=admin,
                                 json={'name': 'TLS delivery', 'role': 'delivery', 'expires_hours': 24})
            assert response.status_code == 201
            secret = secrets.token_urlsafe(32)
            response = http.post('/api/activate', json={'code': response.json()['code'],
                'installation_id': uuid.uuid4().hex, 'installation_secret': secret, 'name': 'TLS delivery'})
            assert response.status_code == 200
            delivery_auth = {'Authorization': 'Bearer ' + secret}
            payload = b'actual backend actual HTTPS actual delivery acknowledgement'
            (source / 'actual.oma').write_bytes(payload)
            clock = [time.time()]
            agent = Agent(client_dir, clock=lambda: clock[0])
            agent.run_once()
            clock[0] += 1
            state = agent.run_once()
            assert state.pending == 1 and state.sent == 0 and state.awaiting_delivery == 1
            receipt = agent.db.execute('SELECT receipt_id FROM queue').fetchone()[0]
            clock[0] += 1
            assert agent.run_once().sent == 0
            response = http.post('/api/delivery/lease', headers=delivery_auth, json={'limit': 1})
            assert response.status_code == 200
            item = response.json()['items'][0]
            assert item['id'] == receipt and item['name'] == 'actual.oma'
            response = http.get('/api/delivery/files/' + receipt,
                                headers=delivery_auth | {'X-Delivery-Lease': item['lease_token']})
            assert response.status_code == 200 and response.content == payload
            assert hashlib.sha256(response.content).hexdigest() == item['sha256']
            response = http.post('/api/delivery/ack/' + receipt, headers=delivery_auth,
                json={'lease_token': item['lease_token'], 'sha256': item['sha256'], 'result': 'delivered', 'error': None})
            assert response.status_code == 200
            clock[0] += 1
            state = agent.run_once()
            assert state.sent == 1 and state.pending == 0
            assert list(agent.spool.iterdir()) == []
            # Revocation is visible without losing the local identity/settings.
            assert http.delete('/api/devices/' + cfg.device_id, headers=admin).status_code == 200
            agent.heartbeat()
            assert 'revogada' in agent.status.error
            assert read_config(client_dir).protected_token == cfg.protected_token
    finally:
        if agent:
            agent.close()
        server.should_exit = True
        thread.join(10)
        assert not thread.is_alive()

def test_lost_upload_response_retries_same_snapshot_then_only_polls(tmp_path):
    source = enroll(tmp_path)
    file = source / 'retry.oma'
    file.write_bytes(b'original immutable bytes')
    digest = hashlib.sha256(file.read_bytes()).hexdigest()
    calls = []
    clock = [time.time()]
    def handle(request):
        if request.method == 'PUT':
            calls.append(request.read())
            assert calls[-1] == b'original immutable bytes'
            if len(calls) == 1:
                raise httpx.ReadTimeout('lost after server commit', request=request)
            return httpx.Response(202, json={'receipt_id': 'same-receipt', 'sha256': digest,
                'name': 'retry.oma', 'status': 'pending', 'duplicate': True})
        if request.method == 'GET':
            return httpx.Response(200, json={'receipt_id': 'same-receipt', 'sha256': digest,
                'name': 'retry.oma', 'status': 'delivered', 'error': None})
        return httpx.Response(200, json={})
    a = Agent(tmp_path, transport=httpx.MockTransport(handle), clock=lambda: clock[0])
    try:
        a.run_once()
        clock[0] += 1
        assert a.run_once().pending == 1
        assert len(list(a.spool.iterdir())) == 1
        clock[0] += 4
        result = a.run_once()
        assert result.awaiting_delivery == 1 and result.sent == 0
        clock[0] += 1
        assert a.run_once().sent == 1
        clock[0] += 1
        a.run_once()
        assert calls == [b'original immutable bytes'] * 2
        assert len(list(a.spool.iterdir())) == 0
    finally:
        a.close()


@pytest.mark.parametrize('field,value', [('receipt_id', 'other'), ('sha256', '0' * 64), ('name', 'other.oma')])
def test_mismatched_status_never_releases_spool(tmp_path, field, value):
    source = enroll(tmp_path)
    (source / 'part.oma').write_bytes(b'data')
    digest = hashlib.sha256(b'data').hexdigest()
    def handle(request):
        result = {'receipt_id': 'receipt', 'sha256': digest, 'name': 'part.oma', 'status': 'pending'}
        if request.method == 'PUT':
            return httpx.Response(202, json=result)
        if request.method == 'GET':
            return httpx.Response(200, json=result | {'status': 'delivered', field: value})
        return httpx.Response(200, json={})
    clock = [time.time()]
    a = Agent(tmp_path, transport=httpx.MockTransport(handle), clock=lambda: clock[0])
    try:
        a.run_once()
        clock[0] += 1
        assert a.run_once().awaiting_delivery == 1
        clock[0] += 1
        result = a.run_once()
        assert result.pending == 1 and result.sent == 0 and result.error
        assert len(list(a.spool.iterdir())) == 1
        assert a.db.execute('SELECT receipt_id,state FROM queue').fetchone() == ('receipt', 'awaiting')
    finally:
        a.close()


def test_final_gui_mode_rejects_explicit_legacy_configuration(tmp_path):
    from client.state import protect_token
    source = tmp_path / 'source'
    source.mkdir()
    save_config(tmp_path, Config(server_url=COMPANY_SERVER, source_dir=str(source),
        protected_token=protect_token('legacy'), device_id='old', delivery_mode='direct'))
    def handle(request):
        pytest.fail('Final GUI must not use legacy enrollment')
    a = Agent(tmp_path, transport=httpx.MockTransport(handle))
    a.user_mode = True
    try:
        assert not a.run_once().configured
    finally:
        a.close()


def test_unchanged_file_does_not_create_repeated_snapshots(tmp_path, monkeypatch):
    source = enroll(tmp_path)
    file = source / 'part.oma'
    file.write_bytes(b'data')
    digest = hashlib.sha256(b'data').hexdigest()
    def handle(request):
        if request.method == 'PUT':
            return httpx.Response(202, json={'receipt_id': 'r', 'sha256': digest,
                'name': 'part.oma', 'status': 'conflict'})
        return httpx.Response(200, json={})
    clock = [time.time()]
    a = Agent(tmp_path, transport=httpx.MockTransport(handle), clock=lambda: clock[0])
    original = a._snapshot
    snapshots = []
    def snapshot(*args):
        snapshots.append(True)
        return original(*args)
    monkeypatch.setattr(a, '_snapshot', snapshot)
    try:
        a.run_once()
        clock[0] += 1
        a.run_once()
        for _ in range(5):
            clock[0] += 1
            a.run_once()
        assert snapshots == [True]
        assert a.status.conflicts == 1
    finally:
        a.close()

