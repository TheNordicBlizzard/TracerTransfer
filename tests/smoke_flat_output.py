"""Real EXEs: direct output, original names, idempotency and safe conflicts."""
import hashlib
import json
import os
from pathlib import Path
import secrets
import sqlite3
import ssl
import subprocess
import tempfile
import time
import httpx
from client.auth import Authorizer
from client.config_ui import ConfigurationService
from client.state import Config, read_config, unprotect_token
from tests.support import SCRATCH, free_port, tls_proxy, wait_ready, stop_process

ROOT=Path(__file__).resolve().parents[1]


def main():
    with tempfile.TemporaryDirectory(prefix='tracer-flat-exe-',dir=SCRATCH) as tmp:
        work=Path(tmp)
        output=work/'industrial';output.mkdir()
        source=work/'source';source.mkdir()
        private=work/'server'
        client_data=work/'client'
        original=output/'KEEP.OMA';original.write_bytes(b'INDUSTRIAL ORIGINAL')
        before=original.stat()
        password=secrets.token_urlsafe(24)
        bootstrap=work/'bootstrap.json'
        bootstrap.write_text(json.dumps({'username':'admin','password':password}),encoding='utf-8')
        port=free_port()
        server=ROOT/'dist/TracerServidor.exe'
        client=ROOT/'dist/TracerCliente.exe'
        with (work/'server.log').open('wb') as log:
            proc=subprocess.Popen([str(server),'--headless','--host','127.0.0.1','--port',str(port),'--data-dir',str(private),'--output-dir',str(output),'--bootstrap-file',str(bootstrap)],cwd=ROOT,stdout=log,stderr=subprocess.STDOUT)
            try:
                wait_ready(f'http://127.0.0.1:{port}',proc)
                with tls_proxy(work,port) as (url,cert):
                    auth=Authorizer(url,str(cert))
                    auth.login('admin',password)
                    try:
                        ConfigurationService(client_data).apply(Config(server_url=url,source_dir=str(source),ca_file=str(cert),poll_seconds=.1,stable_seconds=.1),auth,enroll_name='flat-exe')
                    finally:auth.close()
                    payloads={'first.oma':b'OMA TEST\r\n'+os.urandom(512),'second.vca':b'VCA TEST\r\n'+os.urandom(1024),'keep.oma':b'CONFLICTING CONTENT'}
                    for name,body in payloads.items():(source/name).write_bytes(body)
                    for _ in range(3):
                        r=subprocess.run([str(client),'--headless','--once','--data-dir',str(client_data)],cwd=ROOT,capture_output=True,timeout=40)
                        assert r.returncode==0
                        time.sleep(.3)
                    for name in ['first.oma','second.vca']:
                        assert (output/name).read_bytes()==payloads[name]
                    assert original.read_bytes()==b'INDUSTRIAL ORIGINAL'
                    assert original.stat().st_mtime_ns==before.st_mtime_ns
                    assert len(list(output.iterdir()))==3
                    assert not (private/'received').exists()
                    cfg=read_config(client_data)
                    headers={'Authorization':'Bearer '+unprotect_token(cfg.protected_token)}
                    with httpx.Client(base_url=url,verify=ssl.create_default_context(cafile=str(cert)),timeout=15,trust_env=False) as http:
                        body=payloads['first.oma']
                        replay=http.put('/api/files/FIRST.OMA',headers={**headers,'X-Content-SHA256':hashlib.sha256(body).hexdigest()},content=body)
                        assert replay.status_code==200 and replay.json()['duplicate']
                        bad=payloads['keep.oma']
                        conflict=http.put('/api/files/keep.oma',headers={**headers,'X-Content-SHA256':hashlib.sha256(bad).hexdigest()},content=bad)
                        assert conflict.status_code==409
                        body=original.read_bytes()
                        identical=http.put('/api/files/KEEP.OMA',headers={**headers,'X-Content-SHA256':hashlib.sha256(body).hexdigest()},content=body)
                        assert identical.status_code==200 and identical.json()['duplicate']
                    con=sqlite3.connect(client_data/'queue.sqlite3')
                    try:
                        rows=con.execute('SELECT name,state,error FROM queue ORDER BY name').fetchall()
                        assert sum(r[1]=='sent' for r in rows)==2
                        pending=[r for r in rows if r[1]=='pending']
                        assert len(pending)==1 and pending[0][0]=='keep.oma' and 'HTTPStatusError' in pending[0][2]
                    finally:con.close()
                    assert not list((private/'staging').iterdir())
                    print('PASS: EXEs reais, HTTPS validado, destino plano, nomes preservados, idempotencia/casing, conflito 409, original intacto e fila pendente com erro.')
            finally:stop_process(proc)


if __name__=='__main__':main()
