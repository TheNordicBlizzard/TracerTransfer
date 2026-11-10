"""Autenticação HTTP real em servidor isolado. Execute como módulo."""
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile

import httpx
from tests.support import SCRATCH, free_port, tls_proxy, wait_ready, stop_process

ROOT = Path(__file__).resolve().parents[1]


def run(server_command=None):
    with tempfile.TemporaryDirectory(prefix='tracer-auth-', dir=SCRATCH) as tmp:
        directory = Path(tmp)
        password = secrets.token_urlsafe(24)
        bootstrap = directory / 'bootstrap.json'
        bootstrap.write_text(json.dumps({'username': 'admin', 'password': password}), encoding='utf-8')
        port = free_port()
        command = server_command or [sys.executable, '-m', 'server']
        with (directory / 'server-output.log').open('wb') as output:
            process = subprocess.Popen(command + ['--headless', '--data-dir', str(directory / 'server'), '--host', '127.0.0.1', '--port', str(port), '--bootstrap-file', str(bootstrap)], cwd=ROOT, stdout=output, stderr=subprocess.STDOUT)
            try:
                wait_ready(f'http://127.0.0.1:{port}', process)
                with tls_proxy(directory, port) as (url, cert):
                    import ssl
                    with httpx.Client(base_url=url, verify=ssl.create_default_context(cafile=str(cert)), timeout=15) as client:
                        assert client.get('/api/status').status_code == 401
                        assert client.post('/api/login', json={'username':'admin','password':'invalid'}).status_code == 401
                        result = client.post('/api/login', json={'username':'admin','password':password})
                        assert result.status_code == 200, result.text
                        admin = {'Authorization': 'Bearer ' + result.json()['token']}
                        operator_password = secrets.token_urlsafe(24)
                        result = client.post('/api/users', headers=admin, json={'username':'operator','password':operator_password,'role':'operator'})
                        assert result.status_code in (200,201), result.text
                        result = client.post('/api/login', json={'username':'operator','password':operator_password})
                        assert result.status_code == 200, result.text
                        operator = {'Authorization':'Bearer '+result.json()['token']}
                        assert client.get('/api/status',headers=operator).status_code == 200
                        assert client.post('/api/devices',headers=operator,json={'name':'forbidden'}).status_code == 403
                        result = client.patch('/api/users/operator',headers=admin,json={'enabled':False})
                        assert result.status_code in (200,204),result.text
                        assert client.get('/api/me',headers=operator).status_code == 401
                        result = client.post('/api/devices',headers=admin,json={'name':'auth-test'})
                        assert result.status_code in (200,201),result.text
                        device=result.json()
                        auth_device={'Authorization':'Bearer '+device['token']}
                        assert client.post('/api/heartbeat',headers=auth_device,json={'pending':0,'error':None}).status_code == 200
                        result=client.delete('/api/devices/'+device['id'],headers=admin)
                        assert result.status_code in (200,204),result.text
                        assert client.post('/api/heartbeat',headers=auth_device,json={'pending':0,'error':None}).status_code == 401
                        assert not bootstrap.exists(), 'Arquivo temporário de bootstrap não consumido'
                        print('PASS: HTTPS confiavel; login; operador; acesso proibido; desativacao invalida sessao; dispositivo revogado.')
            finally:
                stop_process(process)


if __name__ == '__main__':
    run()
