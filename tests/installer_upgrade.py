"""Explicit opt-in real current-user Setup upgrade with isolated queued identity.

Never a clean-Windows claim. Refuses any preexisting installation, does not set
startup or launch the default profile, and compares existing user data afterward.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import ssl
import subprocess
import tempfile
import threading
import time
import winreg

import httpx
import uvicorn
from client.activation import ActivationService
from client.state import Config, save_config, read_config, unprotect_token
from delivery.agent import DeliveryAgent, activate
from server.app import create_app
from server.users import bootstrap_admin
from tests.support import SCRATCH, free_port, tls_proxy

ROOT=Path(__file__).resolve().parents[1]
INSTALL=Path(os.environ['LOCALAPPDATA'])/'Programs/TracerTransfer/Client'
DEFAULT=Path(os.environ['LOCALAPPDATA'])/'TracerTransfer/client'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inventory(directory):
    if not directory.exists():return {}
    return {str(p.relative_to(directory)):digest(p) for p in directory.rglob('*') if p.is_file()}


def product():
    keypath=r'Software\Microsoft\Windows\CurrentVersion\Uninstall'
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER,keypath) as base:
        for index in range(winreg.QueryInfoKey(base)[0]):
            with winreg.OpenKey(base,winreg.EnumKey(base,index)) as key:
                try:
                    if winreg.QueryValueEx(key,'DisplayName')[0]!='TracerTransfer Cliente':continue
                    return {name:winreg.QueryValueEx(key,name)[0] for name in ['DisplayVersion','UninstallString']}
                except FileNotFoundError:continue
    return None


def startup_snapshot():
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER,r'Software\Microsoft\Windows\CurrentVersion\Run') as key:
        return {name:(value,kind) for name,value,kind in (winreg.EnumValue(key,i) for i in range(winreg.QueryInfoKey(key)[1])) if name.startswith('TracerTransfer-')}


def invoke(path,args):
    result=subprocess.run([str(path),*args],cwd=ROOT,capture_output=True,timeout=90)
    if result.returncode:raise RuntimeError(f'{path.name} returned {result.returncode}; inspect isolated test state, no output with credentials emitted')


def setup(path,log):
    invoke(path,['/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART','/SP-','/TASKS=',f'/LOG={log}'])


def uninstall():
    record=product()
    if not record:return
    path=Path(record['UninstallString'].strip('"'))
    if path.parent.resolve()!=INSTALL.resolve() or not path.name.startswith('unins') or path.suffix.lower()!='.exe':
        raise RuntimeError('Unexpected uninstaller path; refusing execution')
    invoke(path,['/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART'])
    deadline=time.monotonic()+15
    while product() and time.monotonic()<deadline:time.sleep(.1)
    assert product() is None


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--allow-current-user-install',action='store_true',required=True)
    args=parser.parse_args()
    if product() or (INSTALL/'TracerCliente.exe').exists():raise RuntimeError('Existing client installation; nothing changed')
    old=ROOT/'dist/installers/TracerTransfer-Cliente-0.1.0-windows-x64-pilot-unsigned.exe'
    new=ROOT/'dist/installers/TracerTransfer-Cliente-0.1.1-windows-x64-pilot-unsigned.exe'
    assert old.is_file() and new.is_file()
    # Do not coexist with a user-owned instance of the installation under test.
    query="Get-CimInstance Win32_Process -Filter \"Name='TracerCliente.exe'\" | Select-Object -ExpandProperty ExecutablePath"
    running=subprocess.run(['powershell.exe','-NoProfile','-Command',query],capture_output=True,text=True,errors='replace',check=True)
    assert str(INSTALL).casefold() not in running.stdout.casefold(),'Installed user client is active; no changes'
    baseline=inventory(DEFAULT);startup=startup_snapshot()
    work=Path(tempfile.mkdtemp(prefix='tracer-upgrade-011-',dir=SCRATCH))
    app=create_app(work/'server',delivery_mode='queued')
    password=secrets.token_urlsafe(32);bootstrap_admin(app.state.db,'upgrade-lab',password)
    port=free_port()
    server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=port,log_level='critical',access_log=False))
    thread=threading.Thread(target=server.run,daemon=True);thread.start()
    deadline=time.monotonic()+15
    while not server.started and time.monotonic()<deadline:time.sleep(.02)
    delivery=None;installed=False;report={}
    try:
        assert server.started
        with tls_proxy(work,port) as (url,ca),httpx.Client(base_url=url,verify=ssl.create_default_context(cafile=str(ca)),trust_env=False,timeout=10) as http:
            response=http.post('/api/login',json={'username':'upgrade-lab','password':password});response.raise_for_status()
            admin={'Authorization':'Bearer '+response.json()['token']}
            def code(role):
                r=http.post('/api/activations',headers=admin,json={'name':'UPGRADE-LAB synthetic '+role,'role':role,'expires_hours':1});r.raise_for_status();return r.json()['code']
            source=work/'source';source.mkdir();data=work/'client'
            save_config(data,Config(source_dir=str(source),stable_seconds=0,poll_seconds=.1))
            ActivationService(data,server_url=url,ca_file=str(ca)).activate(code('sender'),'UPGRADE-LAB synthetic client')
            setup(old,work/'install-010.log');installed=True
            assert product()['DisplayVersion']=='0.1.0'
            old_hash=digest(INSTALL/'TracerCliente.exe')
            body=b'SYNTHETIC UPGRADE PENDING FILE - NEVER INDUSTRIAL\r\n'
            (source/'upgrade-pending.oma').write_bytes(body)
            for _ in range(4):
                invoke(INSTALL/'TracerCliente.exe',['--headless','--once','--data-dir',str(data)])
                rows=http.get('/api/status',headers=admin).json()['deliveries']
                if rows:break
            assert len(rows)==1 and rows[0]['status']=='pending'
            identity=read_config(data)
            profile_before=inventory(data)
            setup(new,work/'upgrade-011.log')
            assert product()['DisplayVersion']=='0.1.1'
            assert inventory(data)==profile_before,'Pending profile changed during Setup'
            new_hash=digest(INSTALL/'TracerCliente.exe');assert new_hash!=old_hash
            assert read_config(data).device_id==identity.device_id
            invoke(INSTALL/'TracerCliente.exe',['--headless','--once','--data-dir',str(data)])
            current=http.get('/api/status',headers=admin).json()['deliveries'];assert len(current)==1 and current[0]['receipt_id']==rows[0]['receipt_id']
            output=work/'output';output.mkdir();dd=work/'delivery';dd.mkdir()
            activate(dd,{'code':code('delivery'),'server_url':url,'ca_file':str(ca)})
            delivery=DeliveryAgent(dd,output);delivery.run_once()
            assert (output/'upgrade-pending.oma').read_bytes()==body
            invoke(INSTALL/'TracerCliente.exe',['--headless','--once','--data-dir',str(data)])
            assert http.get('/api/status',headers=admin).json()['deliveries'][0]['status']=='delivered'
            assert read_config(data).device_id==identity.device_id
            private=unprotect_token(read_config(data).protected_token) not in (data/'config.json').read_text()
            assert private,'Plaintext identity persisted'
            uninstall();installed=False
            assert inventory(DEFAULT)==baseline,'Preexisting default data changed; do not overwrite it'
            assert startup_snapshot()==startup,'User startup preference changed'
            assert not (INSTALL/'TracerCliente.exe').exists()
            report={'result':'PASS','from_version':'0.1.0','to_version':'0.1.1','old_client_sha256':old_hash,'new_client_sha256':new_hash,
                    'queued_receipt_preserved':True,'same_activated_identity':True,'pending_profile_byte_for_byte_after_setup':True,
                    'delivered_after_upgrade':True,'synthetic_sha256':hashlib.sha256(body).hexdigest(),'default_files_preserved':len(baseline),
                    'startup_unchanged':True,'installation_removed':True,'clean_windows':False,'work_dir':str(work)}
    finally:
        if delivery:delivery.close()
        if installed or product():uninstall()
        server.should_exit=True;thread.join(10)
    (work/'result.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report,indent=2))


if __name__=='__main__':main()
