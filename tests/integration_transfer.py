"""Legacy DIRECT compatibility only. Current queued EXEs: tests.smoke_executables.

Local folder/pause controls do not require central administrator authorization.
"""
import hashlib
import json
import os
from pathlib import Path
import secrets
import ssl
import subprocess
import sys
import tempfile
import time

import httpx
from client.agent import Agent
from client.auth import Authorizer, AuthorizationError
from client.config_ui import ConfigurationService
from client.state import Config, read_config
from tests.support import SCRATCH, free_port, tls_proxy, wait_ready, stop_process

ROOT = Path(__file__).resolve().parents[1]


def run(server_command=None, client_command=None):
    with tempfile.TemporaryDirectory(prefix='tracer-transfer-', dir=SCRATCH) as tmp:
        directory=Path(tmp)
        source=directory/'source'
        source.mkdir()
        client_data=directory/'client'
        server_data=directory/'server'
        password=secrets.token_urlsafe(24)
        bootstrap=directory/'bootstrap.json'
        bootstrap.write_text(json.dumps({'username':'admin','password':password}),encoding='utf-8')
        port=free_port()
        command=server_command or [sys.executable,'-m','server']
        with (directory/'server-output.log').open('wb') as output:
            process=subprocess.Popen(command+['--headless','--host','127.0.0.1','--port',str(port),'--data-dir',str(server_data),'--bootstrap-file',str(bootstrap)],cwd=ROOT,stdout=output,stderr=subprocess.STDOUT)
            agent=None
            try:
                wait_ready(f'http://127.0.0.1:{port}',process)
                with tls_proxy(directory,port) as (url,cert):
                    auth=Authorizer(url,str(cert))
                    auth.login('admin',password)
                    assert auth.require_admin()['role']=='admin'
                    service=ConfigurationService(client_data)
                    service.apply(Config(server_url=url,source_dir=str(source),ca_file=str(cert),poll_seconds=0.1,stable_seconds=0.1),auth,enroll_name='integration-client')
                    cfg=read_config(client_data)
                    content=b'TRACER TEST OMA\r\n'+os.urandom(1024)
                    oma=source/'order.oma'
                    oma.write_bytes(content)
                    (source/'ignored.txt').write_bytes(b'never upload')
                    (source/'subfolder').mkdir()
                    (source/'subfolder'/'ignored.vca').write_bytes(b'never recurse')
                    if not client_command:
                        agent=Agent(client_data)
                    def round_once():
                        if client_command:
                            result=subprocess.run(client_command+['--headless','--once','--data-dir',str(client_data)],cwd=ROOT,capture_output=True,timeout=30)
                            assert result.returncode==0,repr(result.stderr)
                        else:
                            agent.run_once()
                    round_once()
                    time.sleep(0.25)
                    round_once()
                    digest=hashlib.sha256(content).hexdigest()
                    dest=server_data/'received'/cfg.device_id/digest/'order.oma'
                    assert dest.exists(), 'Arquivo nao chegou: '+(str(agent.status) if agent else 'verificar logs do exe')
                    assert dest.read_bytes()==content
                    assert oma.read_bytes()==content, 'Original foi modificado'
                    round_once()
                    # Public request repetition must confirm prior receipt, without duplicating.
                    from client.state import unprotect_token
                    headers={'Authorization':'Bearer '+unprotect_token(cfg.protected_token),'X-Content-SHA256':digest}
                    with httpx.Client(base_url=url,verify=ssl.create_default_context(cafile=str(cert)),timeout=10) as http:
                        result=http.put('/api/files/order.oma',headers=headers,content=content)
                        assert result.status_code==200,result.text
                        assert result.json()['duplicate'] is True
                        changed=content+b'changed'
                        oma.write_bytes(changed)
                        (source/'second.VCA').write_bytes(b'VCA DATA\r\n')
                        round_once()
                        time.sleep(0.25)
                        round_once()
                        changed_dest=server_data/'received'/cfg.device_id/hashlib.sha256(changed).hexdigest()/'order.oma'
                        assert changed_dest.read_bytes()==changed
                        auth_headers={'Authorization':'Bearer '+auth.session}
                        status=http.get('/api/status',headers=auth_headers).json()
                        assert len(status['files'])==3,status
                        assert status['devices'][0]['last_seen'] is not None
                        # Central operator role does not gate local Windows controls.
                        result=http.post('/api/users',headers=auth_headers,json={'username':'operator','password':password,'role':'operator'})
                        assert result.status_code==201,result.text
                        operator=Authorizer(url,str(cert))
                        operator.login('operator',password)
                        before=(client_data/'config.json').read_bytes()
                        service.pause()
                        assert read_config(client_data).paused
                        service.pause()
                        assert (client_data/'config.json').read_bytes()==before
                        operator.close()
                    auth.close()
                # Proxy unavailable: local controls still work without central login.
                offline=Authorizer(url,str(cert))
                before=(client_data/'config.json').read_bytes()
                service.pause()
                assert read_config(client_data).paused
                service.pause()
                assert (client_data/'config.json').read_bytes()==before
                offline.close()
                received=list((server_data/'received').rglob('*.*'))
                assert len([p for p in received if p.is_file()])==3
                print('PASS: compatibilidade DIRECT por HTTPS; DPAPI, filtros, hashes, idempotencia e controles locais inclusive offline.'+(' Processos EXE.' if client_command else ' Processos Python.'))
            finally:
                if agent:
                    agent.close()
                stop_process(process)


if __name__=='__main__':
    run()
