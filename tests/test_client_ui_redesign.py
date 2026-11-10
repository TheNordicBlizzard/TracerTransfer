"""UI-only tests with an idle synthetic agent; no server, registry or credentials."""
from dataclasses import replace
from types import SimpleNamespace
import threading
import time

import pytest
from client.config_ui import ClientUI, describe_status
from client.state import Config, read_config, save_config
from client.status import Status
from tests.test_client import tk_root


@pytest.mark.parametrize('heartbeat', [None, 0, 954, float('nan'), float('inf'), 1001])
def test_missing_stale_or_future_contact_does_not_claim_ready(heartbeat):
    view=describe_status(Status(last_heartbeat=heartbeat),activated=True,now=1000)
    assert view['tone']=='muted'
    assert 'Aguardando contato' in view['title']


@pytest.mark.parametrize('values,title', [
    ({'paused':True},'Envios pausados'),
    ({'error':'Servidor indisponível'},'Atenção no envio'),
    ({'conflicts':1},'Há arquivos para revisar'),
    ({'uncertain':1},'Há arquivos para revisar'),
    ({'awaiting_delivery':1},'Aguardando entrega no destino'),
    ({'pending':1},'Arquivos na fila de envio'),
    ({},'Acompanhando a pasta'),
])
def test_status_hierarchy_and_delivery_distinction(values,title):
    assert describe_status(Status(last_heartbeat=990,**values),activated=True,now=1000)['title']==title


def test_activation_and_review_remain_explicit():
    assert describe_status(Status(last_heartbeat=990),activated=False,now=1000)['title']=='Ative este computador'
    view=describe_status(Status(conflicts=2,uncertain=1),activated=True,now=1000)
    assert '2 conflito(s)' in view['detail'] and '1 entrega(s) incerta(s)' in view['detail']
    assert 'não renomeie' in view['detail']


def test_gui_progressive_activation_metrics_and_nonblocking_controls(tmp_path,monkeypatch,tk_root):
    import tkinter as tk
    monkeypatch.setattr(ClientUI,'_start_tray',lambda self:None)
    stop=threading.Event()
    agent=SimpleNamespace(data_dir=tmp_path,status=Status(),stop_event=stop,lock=threading.RLock(),run=lambda:stop.wait(5))
    ui=ClientUI(agent,root=tk.Toplevel(tk_root))
    try:
        ui.root.update()
        assert ui.activation_card.winfo_manager()=='pack'
        assert ui.entries[2].cget('show')=='*'
        assert ui.admin_buttons==[]
        assert not hasattr(ui,'url') and not hasattr(ui,'password')
        ui.root.geometry('760x640');ui.root.update()
        for widget in [ui.local_buttons[3],ui.local_buttons[4]]:
            assert widget.winfo_ismapped()
            assert widget.winfo_rooty()+widget.winfo_height()<=ui.root.winfo_rooty()+ui.root.winfo_height()
        ui.body_viewport.yview_moveto(1);ui.root.update()
        assert ui.local_buttons[2].winfo_rooty()+ui.local_buttons[2].winfo_height()<=ui.body_viewport.winfo_rooty()+ui.body_viewport.winfo_height()
        ui.body_viewport.yview_moveto(0)
        cfg=Config(activation_id='SYNTHETIC-ACTIVATION',installation_id='SYNTHETIC-INSTALLATION',
                   device_id='SYNTHETIC-DEVICE',protected_token='SYNTHETIC-NEVER-DECRYPTED',role='sender')
        save_config(tmp_path,cfg)
        ui.update_permissions()
        assert ui.activation_card.winfo_manager()==''
        assert str(ui.local_buttons[2]['state'])=='disabled'
        agent.status=Status(pending=2,awaiting_delivery=3,sent=4,conflicts=1,uncertain=2,last_heartbeat=time.time())
        ui.refresh()
        assert {k:v.get() for k,v in ui.metrics.items()}=={'pending':'2','awaiting_delivery':'3','sent':'4','review':'3'}
        assert 'conflito' in ui.alert_text.get()
        ui.busy=True;ui.update_permissions()
        assert str(ui.entries[0]['state'])=='disabled'
        assert str(ui.local_buttons[4]['state'])=='normal'
        assert not read_config(tmp_path).startup_enabled
        save_config(tmp_path,replace(cfg,paused=True))
        ui.busy=False;ui.update_permissions()
        assert ui.local_buttons[3].cget('text')=='Retomar envios'
        # At the supported minimum size, keyboard controls must not be clipped.
        ui.root.geometry('760x640');ui.root.update()
        for widget in [ui.local_buttons[0],ui.local_buttons[1],ui.local_buttons[3],ui.local_buttons[4]]:
            assert widget.winfo_ismapped()
            assert widget.winfo_rooty()+widget.winfo_height()<=ui.root.winfo_rooty()+ui.root.winfo_height()
    finally:
        ui.shutdown();ui.worker.join(3)
