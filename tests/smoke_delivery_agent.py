"""Real HTTPS subprocess smoke, isolated Windows scratch; never installs a service.

uv run python -m tests.smoke_delivery_agent [--exe C:/.../TracerEntregador.exe]
Exercises a synthetic local API (not production/backend integration), explicit CA,
DPAPI identity, publication, lost ACK, restart after consumption, and TLS rejection.
"""
import argparse
from datetime import datetime, timedelta, timezone
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import os
from pathlib import Path
import socket
import ssl
import subprocess
import sys
import tempfile
import threading

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from delivery.agent import activate


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--exe')
    args = parser.parse_args(argv)
    scratch = os.environ.get('TRACER_TEST_TMP') or os.environ.get('TMPDIR')
    if not scratch:
        scratch = str(Path.home() / 'AppData/Local/hermes/profiles/code-architect/cache/scratch')
    Path(scratch).mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='delivery-https-', dir=scratch) as temporary:
        root = Path(temporary)
        data, output = root / 'data', root / 'output'
        data.mkdir(); output.mkdir()
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        now = datetime.now(timezone.utc)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'Tracer delivery isolated test CA')])
        cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
                .public_key(key.public_key()).serial_number(x509.random_serial_number())
                .not_valid_before(now-timedelta(minutes=5)).not_valid_after(now+timedelta(days=1))
                .add_extension(x509.BasicConstraints(ca=True, path_length=None), True)
                .add_extension(x509.SubjectAlternativeName([x509.DNSName('localhost'),
                    x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]), False)
                .sign(key, hashes.SHA256()))
        ca, key_path = root / 'ca.pem', root / 'key.pem'
        ca.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM,
                             serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        body = b'SYNTHETIC TRACER DELIVERY TEST\r\n'
        sha = hashlib.sha256(body).hexdigest()
        state = {'secret':None, 'leased':False, 'drop':True, 'downloads':0, 'acks':0}
        item = {'id':'e'*32,'name':'smoke.oma','sha256':sha,'size':len(body),'lease_token':'local-smoke-lease',
                'lease_until':(now+timedelta(minutes=10)).isoformat()}

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def respond(self, code, value):
                payload = json.dumps(value).encode()
                self.send_response(code)
                self.send_header('Content-Type','application/json')
                self.send_header('Content-Length',str(len(payload)))
                self.end_headers(); self.wfile.write(payload)

            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers.get('Content-Length','0'))))
                if self.path == '/api/activate':
                    assert payload['code'] == 'TT-'+'B'*40
                    state['secret'] = payload['installation_secret']
                    return self.respond(200, {'device_id':'smoke-device','activation_id':'smoke-activation','role':'delivery'})
                if self.headers.get('Authorization') != 'Bearer '+state['secret']:
                    return self.respond(401, {'error':'auth'})
                if self.path == '/api/delivery/lease':
                    assert payload == {'limit':1}
                    offered = [] if state['leased'] else [item]
                    state['leased'] = True
                    return self.respond(200, {'items':offered})
                if self.path == '/api/delivery/ack/'+item['id']:
                    assert payload == {'lease_token':item['lease_token'],'sha256':sha,'result':'delivered','error':None}
                    state['acks'] += 1
                    if state['drop']:
                        state['drop'] = False
                        self.connection.shutdown(socket.SHUT_RDWR)
                        self.connection.close()
                        return
                    return self.respond(200, {'ok':True})
                return self.respond(403, {'error':'role'})

            def do_GET(self):
                if self.path != '/api/delivery/files/'+item['id'] or self.headers.get('X-Delivery-Lease') != item['lease_token'] or self.headers.get('Authorization') != 'Bearer '+state['secret']:
                    return self.respond(403, {'error':'role'})
                state['downloads'] += 1
                self.send_response(200); self.send_header('Content-Length',str(len(body))); self.end_headers()
                self.wfile.write(body)

        server = ThreadingHTTPServer(('127.0.0.1',0),Handler)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(str(ca), str(key_path))
        server.socket = context.wrap_socket(server.socket,server_side=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            endpoint = 'https://127.0.0.1:'+str(server.server_port)
            activate(data, {'code':'TT-'+'B'*40,'server_url':endpoint,'ca_file':str(ca)})
            prefix = [args.exe] if args.exe else [sys.executable,str(Path(__file__).resolve().parents[1] / 'launch_delivery.py')]
            command = prefix + ['--data-dir',str(data),'--output-dir',str(output),'--once']
            first = subprocess.run(command,capture_output=True,text=True,timeout=45)
            assert first.returncode == 1, first.returncode
            assert (output / 'smoke.oma').read_bytes() == body
            assert hashlib.sha256((output / 'smoke.oma').read_bytes()).hexdigest() == sha
            (output / 'smoke.oma').unlink()  # Consumer completed its work before ACK retry.
            second = subprocess.run(command,capture_output=True,text=True,timeout=45)
            assert second.returncode == 0, second.returncode
            assert not list(output.iterdir())
            assert state['downloads'] == 1 and state['acks'] == 2, state
            identity_path = data / 'identity.json'
            identity = json.loads(identity_path.read_text())
            assert state['secret'] not in identity_path.read_text()
            identity['ca_file'] = None
            identity_path.write_text(json.dumps(identity), encoding='utf-8')
            third = subprocess.run(command,capture_output=True,text=True,timeout=45)
            assert third.returncode == 1, 'Untrusted HTTPS must fail closed'
            for completed in (first,second,third):
                assert state['secret'] not in completed.stdout+completed.stderr
                assert item['lease_token'] not in completed.stdout+completed.stderr
            print(json.dumps({'result':'PASS','transport':'real HTTPS with explicit temporary CA',
                              'runner':'exe' if args.exe else 'source subprocess',
                              'sha256':sha,'downloads':state['downloads'],'ack_requests':state['acks'],
                              'consumed_file_not_recreated':True,'untrusted_tls_rejected':True}))
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=5)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
