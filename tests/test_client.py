import os
from pathlib import Path
import pytest
import hashlib
import httpx


@pytest.fixture(scope='session')
def tk_root(request):
    """One interpreter per test process; isolated Toplevels per GUI exercise."""
    import tkinter as tk
    if not hasattr(request.session, '_tracer_client_tk_root'):
        root = tk.Tk()
        root.withdraw()
        request.session._tracer_client_tk_root = root
        request.addfinalizer(root.destroy)
    return request.session._tracer_client_tk_root


def configured(tmp_path):
    from client.state import Config, save_config, protect_token
    source = tmp_path / 'source'
    source.mkdir()
    save_config(tmp_path, Config(server_url='https://test', source_dir=str(source),
        protected_token=protect_token('secret'), device_id='d', stable_seconds=1, poll_seconds=1,
        delivery_mode='direct'))
    return source


def test_queue_stability_snapshot_retry_versions_heartbeat(tmp_path):
    from client.agent import Agent
    source = configured(tmp_path)
    f = source / 'part.OMA'
    f.write_bytes(b'abc')
    os.utime(f, (0, 0))
    (source / 'zero.vca').touch()
    (source / 'ignore.txt').write_text('x')
    (source / 'nested').mkdir()
    (source / 'nested' / 'ignored.oma').write_bytes(b'no')
    calls, uploaded = [], []
    fail = [True]
    def handler(req):
        calls.append(req.url.path)
        if req.method == 'PUT':
            body = req.read()
            assert hashlib.sha256(body).hexdigest() == req.headers['X-Content-SHA256']
            uploaded.append(body)
            if fail[0]:
                return httpx.Response(503)
            return httpx.Response(200, json={'sha256': hashlib.sha256(body).hexdigest(), 'duplicate': False, 'name': 'part.OMA'})
        return httpx.Response(200, json={'ok': True})
    clock = [100.]
    a = Agent(tmp_path, transport=httpx.MockTransport(handler), clock=lambda: clock[0])
    assert a.run_once().pending == 0
    clock[0] += 2
    assert a.run_once().pending == 1
    assert calls.count('/api/heartbeat') == 2
    assert uploaded == [b'abc']
    f.write_bytes(b'changed')
    os.utime(f, (0, 0))
    fail[0] = False
    a.close()
    clock[0] += 4
    a = Agent(tmp_path, transport=httpx.MockTransport(handler), clock=lambda: clock[0])
    a.run_once()
    assert uploaded == [b'abc', b'abc']  # retries immutable snapshot, not changed source
    clock[0] += 2
    assert a.run_once().pending == 0
    assert uploaded[-1] == b'changed'
    a.run_once()
    assert len(uploaded) == 3
    assert f.read_bytes() == b'changed'
    a.close()


def test_authorization_revalidated_and_offline_denied():
    from client.auth import Authorizer, AuthorizationError
    role = ['admin']
    online = [True]
    calls = []
    def handler(req):
        calls.append(req.url.path)
        if not online[0]:
            raise httpx.ConnectError('offline')
        if req.url.path.endswith('login'):
            return httpx.Response(200, json={'token': 'session', 'role': role[0], 'username': 'u'})
        return httpx.Response(200, json={'role': role[0], 'username': 'u'})
    auth = Authorizer('https://test', transport=httpx.MockTransport(handler))
    auth.login('u', 'password')
    auth.require_admin()
    role[0] = 'operator'
    with pytest.raises(AuthorizationError):
        auth.require_admin()
    online[0] = False
    with pytest.raises(AuthorizationError):
        auth.require_admin()
    assert calls.count('/api/me') == 4
    assert auth.session is None


def test_configuration_changes_require_old_and_new_admin(tmp_path):
    from client.config_ui import ConfigurationService
    from client.auth import Authorizer, AuthorizationError
    from client.state import Config, save_config, read_config
    checks = []
    def handler(req):
        checks.append((req.url.host, req.url.path))
        if req.url.path == '/api/devices':
            return httpx.Response(200, json={'id': 'new-device', 'token': 'device-token'})
        return httpx.Response(200, json={'token': 'session', 'role': 'admin', 'username': 'u'})
    save_config(tmp_path, Config(server_url='https://old'))
    old = Authorizer('https://old', transport=httpx.MockTransport(handler))
    new = Authorizer('https://new', transport=httpx.MockTransport(handler))
    new.login('u', 'p')
    service = ConfigurationService(tmp_path)
    with pytest.raises(AuthorizationError):
        service.apply(Config(server_url='https://new'), new, old_auth=old, enroll_name='name')
    assert read_config(tmp_path).server_url == 'https://old'
    old.login('u', 'p')
    service.apply(Config(server_url='https://new'), new, old_auth=old, enroll_name='name')
    assert read_config(tmp_path).server_url == 'https://new'
    assert read_config(tmp_path).device_id == 'new-device'
    assert ('old', '/api/me') in checks and ('new', '/api/devices') in checks


def test_single_instance_and_headless_cli(tmp_path, capsys):
    from client.instance import SingleInstance
    from client.__main__ import main
    with SingleInstance(tmp_path):
        with pytest.raises(RuntimeError):
            SingleInstance(tmp_path)
    assert main(['--headless', '--once', '--data-dir', str(tmp_path)]) == 0
    import json
    assert json.loads(capsys.readouterr().out)['configured'] is False


def test_real_https_trust_and_transfer(tmp_path):
    import datetime
    import ipaddress
    import ssl
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    from client.agent import Agent
    from client.state import read_config, save_config
    from client.auth import http_client
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'localhost')])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
        .serial_number(x509.random_serial_number()).not_valid_before(now-datetime.timedelta(minutes=1))
        .not_valid_after(now+datetime.timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]), False)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), True).sign(key, hashes.SHA256()))
    pem = tmp_path / 'cert.pem'
    priv = tmp_path / 'key.pem'
    pem.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    priv.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    bodies = []
    hearts = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_POST(self):
            self.reply(False)
        def do_PUT(self):
            self.reply(True)
        def reply(self, upload):
            import json
            body = self.rfile.read(int(self.headers.get('Content-Length', 0)))
            assert self.headers['Authorization'] == 'Bearer secret'
            if upload:
                bodies.append(body)
                digest = hashlib.sha256(body).hexdigest()
                assert digest == self.headers['X-Content-SHA256']
                value = {'sha256': digest, 'name': 'test.vca', 'duplicate': False}
            else:
                hearts.append(body)
                value = {'ok': True}
            data = json.dumps(value).encode()
            self.send_response(200)
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(pem, priv)
    server.socket = ctx.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        source = configured(tmp_path)
        cfg = read_config(tmp_path)
        cfg.server_url = f'https://127.0.0.1:{server.server_port}'
        cfg.ca_file = str(pem)
        save_config(tmp_path, cfg)
        with http_client(cfg.server_url) as untrusted:
            with pytest.raises(httpx.ConnectError):
                untrusted.get('/api/me')
        f = source / 'test.vca'
        f.write_bytes(b'real TLS bytes')
        os.utime(f, (0, 0))
        clock = [100.]
        a = Agent(tmp_path, clock=lambda: clock[0])
        a.run_once()
        clock[0] += 2
        result = a.run_once()
        assert result.sent == 1 and result.pending == 0
        assert bodies == [b'real TLS bytes']
        assert len(hearts) == 2
        a.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_heartbeat_runs_while_upload_blocked(tmp_path, monkeypatch):
    import threading
    import client.agent as module
    from client.agent import Agent
    from client.state import read_config, save_config
    source = configured(tmp_path)
    cfg = read_config(tmp_path)
    cfg.poll_seconds = 0.1
    save_config(tmp_path, cfg)
    f = source / 'blocked.oma'
    f.write_bytes(b'body')
    os.utime(f, (0, 0))
    entered, release, heartbeat_during_upload = threading.Event(), threading.Event(), threading.Event()
    def handler(req):
        if req.method == 'PUT':
            entered.set()
            release.wait(3)
            return httpx.Response(503)
        if entered.is_set():
            heartbeat_during_upload.set()
        return httpx.Response(200, json={})
    monkeypatch.setattr(module, 'HEARTBEAT_SECONDS', 0.03)
    clock = [100.]
    a = Agent(tmp_path, transport=httpx.MockTransport(handler), clock=lambda: clock[0])
    a.run_once()
    clock[0] += 2
    thread = threading.Thread(target=a.run)
    thread.start()
    try:
        assert entered.wait(2)
        assert heartbeat_during_upload.wait(2)
    finally:
        a.stop_event.set()
        release.set()
        thread.join(4)
        a.close()
    assert not thread.is_alive()


def test_gui_window_tray_and_operator_controls(tmp_path, monkeypatch, tk_root):
    from client.agent import Agent
    from client.config_ui import ClientUI
    monkeypatch.setattr(ClientUI, '_start_tray', lambda self: None)
    a = Agent(tmp_path)
    import tkinter as tk
    ui = ClientUI(a, root=tk.Toplevel(tk_root))
    try:
        ui.root.update()
        assert ui.admin_buttons == []
        assert str(ui.local_buttons[3]['state']) == 'normal'
        assert str(ui.local_buttons[4]['state']) == 'normal'
        ui.hide()
        ui.root.update()
        assert ui.root.state() == 'iconic'
        ui.root.deiconify()
        ui.root.update()
    finally:
        ui.shutdown()
        ui.worker.join(3)
        a.close()


def test_paused_heartbeat_and_size_limit(tmp_path, monkeypatch):
    from client.agent import Agent
    import client.agent as module
    from client.state import read_config, save_config
    source = configured(tmp_path)
    file = source / 'too-large.oma'
    file.write_bytes(b'1234')
    os.utime(file, (0, 0))
    cfg = read_config(tmp_path)
    cfg.paused = True
    save_config(tmp_path, cfg)
    paths = []
    def handler(req):
        paths.append(req.url.path)
        return httpx.Response(200, json={})
    monkeypatch.setattr(module, 'MAX_FILE_BYTES', 3)
    a = Agent(tmp_path, transport=httpx.MockTransport(handler))
    try:
        assert a.run_once().paused is True
        assert paths == ['/api/heartbeat']
        cfg.paused = False
        save_config(tmp_path, cfg)
        result = a.run_once()
        assert 'acima' in result.error and result.pending == 0
        assert file.exists()
        assert paths == ['/api/heartbeat', '/api/heartbeat']
    finally:
        a.close()


def test_invalid_timing_is_rejected(tmp_path):
    from client.state import Config, save_config
    for value in [float('inf'), float('nan'), -1, 0]:
        with pytest.raises(ValueError):
            save_config(tmp_path, Config(poll_seconds=value))


def test_config_dpapi_and_https(tmp_path):
    from client.state import Config, save_config, read_config, protect_token, unprotect_token
    token = protect_token('device-secret')
    assert token != 'device-secret'
    assert unprotect_token(token) == 'device-secret'
    c = Config(server_url='https://localhost:8443', protected_token=token)
    save_config(tmp_path, c)
    assert read_config(tmp_path) == c
    assert 'device-secret' not in (tmp_path / 'config.json').read_text()
    with pytest.raises(ValueError):
        save_config(tmp_path, Config(server_url='http://localhost'))
