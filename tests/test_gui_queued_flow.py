"""Real Tk button flow, real queued backend/TLS and delivery; isolated synthetic files.

The transport adapter routes the fixed company origin to a TLS loopback backend.
It does not fabricate responses or weaken the production client origin check.
"""
import hashlib
import secrets
import ssl
import threading
import time
from urllib.parse import urlsplit

import httpx
import uvicorn
import pytest

from client.agent import Agent
from client.config_ui import ClientUI
from client.state import COMPANY_SERVER, Config, read_config, save_config, unprotect_token
from delivery.agent import DeliveryAgent, activate
from server.app import create_app
from server.users import bootstrap_admin
from tests.support import free_port, tls_proxy
from tests.test_client import tk_root


class LoopbackTLS(httpx.BaseTransport):
    def __init__(self,url,certificate):
        self.url=url
        self.certificate=certificate
        self.offline=False
        self.origins=[]

    def handle_request(self,request):
        assert request.url.host==urlsplit(COMPANY_SERVER).hostname
        self.origins.append(request.url.host)
        if self.offline:
            raise httpx.ConnectError('Isolated test offline',request=request)
        headers={k:v for k,v in request.headers.items() if k.lower() not in {'host','content-length','transfer-encoding'}}
        with httpx.Client(verify=ssl.create_default_context(cafile=str(self.certificate)),trust_env=False,timeout=10) as client:
            result=client.request(request.method,self.url+request.url.raw_path.decode(),headers=headers,content=request.read())
        return httpx.Response(result.status_code,headers=result.headers,content=result.content,request=request)


def pump(root,predicate,timeout=15):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        root.update()
        if predicate():
            return
        time.sleep(.01)
    raise AssertionError('GUI state did not converge within bounded timeout')


def test_graphical_activation_pause_delivery_and_conflict(tmp_path,monkeypatch,tk_root):
    import tkinter as tk
    app=create_app(tmp_path/'server',delivery_mode='queued')
    password=secrets.token_urlsafe(32)
    bootstrap_admin(app.state.db,'gui-lab',password)
    port=free_port()
    backend=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=port,log_level='critical',access_log=False))
    thread=threading.Thread(target=backend.run,daemon=True);thread.start()
    deadline=time.monotonic()+10
    while not backend.started and time.monotonic()<deadline:time.sleep(.02)
    assert backend.started
    try:
        with tls_proxy(tmp_path,port) as (url,ca),httpx.Client(base_url=url,verify=ssl.create_default_context(cafile=str(ca)),trust_env=False,timeout=10) as http:
            auth=http.post('/api/login',json={'username':'gui-lab','password':password});auth.raise_for_status()
            headers={'Authorization':'Bearer '+auth.json()['token']}
            def code(role):
                response=http.post('/api/activations',headers=headers,json={'name':'GUI-LAB synthetic '+role,'role':role,'expires_hours':1})
                response.raise_for_status();return response.json()['code']
            source=tmp_path/'source';source.mkdir()
            baseline=source/'before-activation.oma';baseline.write_bytes(b'SYNTHETIC PREEXISTING BACKLOG')
            data=tmp_path/'client'
            save_config(data,Config(source_dir=str(source),stable_seconds=0,poll_seconds=.1))
            bridge=LoopbackTLS(url,ca)
            agent=Agent(data,transport=bridge)
            monkeypatch.setattr(ClientUI,'_start_tray',lambda self:None)
            ui=ClientUI(agent,root=tk.Toplevel(tk_root))
            delivery=None
            try:
                ui.code.set(code('sender'))
                ui.name.set('GUI-LAB synthetic client')
                ui.local_buttons[2].invoke()
                pump(ui.root,lambda:read_config(data).activated and not ui.busy)
                assert ui.code.get()==''
                assert ui.activation_card.winfo_manager()==''
                assert read_config(data).server_url==COMPANY_SERVER
                assert unprotect_token(read_config(data).protected_token) not in (data/'config.json').read_text()
                assert http.get('/api/status',headers=headers).json()['deliveries']==[]
                content=b'SYNTHETIC GUI FLOW - NO INDUSTRIAL DATA\r\n'
                (source/'gui-flow.oma').write_bytes(content)
                pump(ui.root,lambda:ui.metrics['awaiting_delivery'].get()=='1')
                assert ui.metrics['sent'].get()=='0'
                bridge.offline=True
                ui.local_buttons[3].invoke()
                pump(ui.root,lambda:read_config(data).paused and not ui.busy)
                assert ui.local_buttons[3].cget('text')=='Retomar envios'
                bridge.offline=False
                output=tmp_path/'output';output.mkdir()
                delivery_data=tmp_path/'delivery';delivery_data.mkdir()
                activate(delivery_data,{'code':code('delivery'),'server_url':url,'ca_file':str(ca)})
                delivery=DeliveryAgent(delivery_data,output)
                delivery.run_once()
                assert (output/'gui-flow.oma').read_bytes()==content
                assert hashlib.sha256((output/'gui-flow.oma').read_bytes()).hexdigest()==hashlib.sha256(content).hexdigest()
                ui.local_buttons[3].invoke()
                pump(ui.root,lambda:not read_config(data).paused and not ui.busy and ui.metrics['sent'].get()=='1')
                assert ui.metrics['awaiting_delivery'].get()=='0'
                assert baseline.read_bytes()==b'SYNTHETIC PREEXISTING BACKLOG'
                assert not (output/baseline.name).exists()
                (output/'conflict.vca').write_bytes(b'EXISTING SYNTHETIC DESTINATION')
                (source/'conflict.vca').write_bytes(b'DIFFERENT SYNTHETIC CONTENT')
                pump(ui.root,lambda:ui.metrics['awaiting_delivery'].get()=='1')
                delivery.run_once()
                pump(ui.root,lambda:ui.metrics['review'].get()=='1')
                assert (output/'conflict.vca').read_bytes()==b'EXISTING SYNTHETIC DESTINATION'
                assert 'conflito' in ui.alert_text.get().casefold()
                assert str(ui.local_buttons[4]['state'])=='normal'
                assert not read_config(data).startup_enabled
                assert bridge.origins
            finally:
                ui.shutdown();ui.worker.join(10);agent.close()
                if delivery:delivery.close()
    finally:
        backend.should_exit=True;thread.join(10)
        assert not thread.is_alive()
