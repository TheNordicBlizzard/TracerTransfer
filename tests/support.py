"""Infraestrutura de teste: TLS real em loopback, nunca usada em produção."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import http.client
import ipaddress
import socket
import ssl
import threading
import time

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
import httpx


import subprocess
import os
import tempfile

SCRATCH = Path(os.environ.get('TRACER_TEST_TMP') or os.environ.get('TMPDIR') or tempfile.gettempdir())
SCRATCH.mkdir(parents=True, exist_ok=True)


def stop_process(process):
    if os.name == 'nt':
        subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], capture_output=True, timeout=15)
    else:
        process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)
    # Windows may briefly retain image/log mappings after process exit.
    time.sleep(0.25)


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def certificate(directory: Path):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'localhost')])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=5)).not_valid_after(now + timedelta(days=1))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName('localhost'), x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]), critical=False)
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .sign(key, hashes.SHA256()))
    cert_path, key_path = directory / 'test-ca.pem', directory / 'test-key.pem'
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    return cert_path, key_path


@contextmanager
def tls_proxy(directory, upstream_port):
    cert, key = certificate(Path(directory))
    class Handler(BaseHTTPRequestHandler):
        def forward(self):
            size = int(self.headers.get('Content-Length', '0'))
            if size > 21 * 1024 * 1024:
                self.send_error(413)
                return
            if self.headers.get('Transfer-Encoding', '').lower() == 'chunked':
                chunks = []
                total = 0
                while True:
                    line = self.rfile.readline(128)
                    chunk_size = int(line.strip().split(b';')[0], 16)
                    if chunk_size == 0:
                        while self.rfile.readline(8192) not in (b'\r\n', b''):
                            pass
                        break
                    total += chunk_size
                    if total > 21 * 1024 * 1024:
                        self.send_error(413)
                        return
                    chunks.append(self.rfile.read(chunk_size))
                    if self.rfile.read(2) != b'\r\n':
                        raise ValueError('Chunk malformado')
                payload = b''.join(chunks)
            else:
                payload = self.rfile.read(size)
            conn = http.client.HTTPConnection('127.0.0.1', upstream_port, timeout=30)
            try:
                headers = {k: v for k, v in self.headers.items() if k.lower() not in ('host', 'connection', 'transfer-encoding', 'content-length')}
                headers['Content-Length'] = str(len(payload))
                conn.request(self.command, self.path, body=payload, headers=headers)
                response = conn.getresponse()
                data = response.read()
                self.send_response(response.status)
                for k, v in response.getheaders():
                    if k.lower() not in ('connection', 'transfer-encoding', 'content-length', 'server', 'date'):
                        self.send_header(k, v)
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            finally:
                conn.close()
        do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = forward
        def log_message(self, *_args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'https://127.0.0.1:{server.server_port}', cert
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def wait_ready(url, process, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f'Servidor terminou antes de ficar pronto: {process.returncode}')
        try:
            if httpx.get(url + '/health', timeout=1).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.15)
    raise TimeoutError('Servidor não ficou pronto')
