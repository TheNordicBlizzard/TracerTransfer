"""Pilot endpoint checks using isolated, pre-provisioned DPAPI configuration.

Run from the project with its development Python. Never accepts credentials on
its command line and never logs bearer values. Uses synthetic files only.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import time
import ssl
import httpx
from client.state import read_config, unprotect_token


def run(data_dir, executable):
    cfg = read_config(data_dir)
    token = unprotect_token(cfg.protected_token)
    checks = []
    with httpx.Client(base_url=cfg.server_url, verify=ssl.create_default_context(), timeout=30, trust_env=False) as http:
        health = http.get('/health')
        assert health.status_code == 200 and health.json() == {'ok': True}, 'Health must return actual backend JSON'
        checks.append({'method': 'GET', 'path': '/health', 'status': 200})
        for method, path, expected in [
            ('GET', '/', 404), ('POST', '/api/login', 404),
            ('GET', '/api/users', 404), ('GET', '/api/status', 404),
            ('POST', '/api/devices', 404), ('GET', '/api/me', 404),
            ('PUT', '/api/files/unauthorized.oma', 401),
            ('POST', '/api/heartbeat', 401),
        ]:
            payload = b'{"pending":0,"error":null}' if path == '/api/heartbeat' else b'{}'
            response = http.request(method, path, content=payload, headers={'Content-Type': 'application/json'})
            assert response.status_code == expected, f'{method} {path}: {response.status_code}, expected {expected}'
            checks.append({'method': method, 'path': path, 'status': response.status_code})
        for path in ['/api/users', '/api/status', '/api/login']:
            response = http.get(path, headers={'X-Tracer-Client-IP': '8.8.8.8'})
            assert response.status_code == 404, 'Spoofed headers bypassed proxy restrictions'
            edge = http.get(path, headers={'CF-Connecting-IP': '8.8.8.8'})
            assert edge.status_code in (403, 404), 'Reserved proxy header was not blocked'
        source = Path(cfg.source_dir)
        contents = {'pilot.oma': b'SYNTHETIC OMA TEST\r\nNO PATIENT DATA\r\n',
                    'pilot.vca': b'SYNTHETIC VCA TEST\r\nNO PATIENT DATA\r\n'}
        source.mkdir(parents=True, exist_ok=True)
        for name, payload in contents.items():
            (source / name).write_bytes(payload)
        (source / 'ignored.txt').write_bytes(b'IGNORE')
        for _ in range(3):
            p = subprocess.run([str(executable), '--headless', '--once', '--data-dir', str(data_dir)], capture_output=True, timeout=60)
            assert p.returncode == 0, 'Client EXE exited with error'
            time.sleep(0.5)
        headers = {'Authorization': 'Bearer ' + token}
        response = http.post('/api/heartbeat', headers=headers, json={'pending': 0, 'error': None})
        assert response.status_code == 200, 'Authenticated heartbeat failed'
        files = []
        for name, payload in contents.items():
            assert (source / name).read_bytes() == payload, 'Original changed'
            digest = hashlib.sha256(payload).hexdigest()
            response = http.put('/api/files/' + name, headers={**headers, 'X-Content-SHA256': digest}, content=payload)
            assert response.status_code == 200 and response.json().get('duplicate') is True, 'EXE upload missing or duplicate not recognized'
            files.append({'name': name, 'sha256': digest, 'size': len(payload)})
        # A valid device still cannot send other extensions.
        response = http.put('/api/files/reject.txt', headers={**headers, 'X-Content-SHA256': hashlib.sha256(b'X').hexdigest()}, content=b'X')
        assert response.status_code == 400 and response.json().get('detail') == 'Only .oma and .vca files are accepted', f'Extension filter: {response.status_code}'
    return {'url': cfg.server_url, 'checks': checks, 'files': files, 'client_exe': 'PASS',
            'originals': 'preserved', 'idempotency': 'PASS', 'heartbeat': 'PASS', 'spoofed_headers': 'blocked',
            'origin': 'public HTTPS hostname from the local workstation; not an independently confirmed external network'}


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--client-data', type=Path, required=True)
    p.add_argument('--executable', type=Path, default=Path('dist/TracerCliente.exe'))
    p.add_argument('--report', type=Path, required=True)
    args = p.parse_args()
    report = run(args.client_data, args.executable.resolve())
    args.report.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2))
