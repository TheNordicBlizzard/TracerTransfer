"""Local control authorization and dashboard regression tests."""
import pytest
from server.database import Database
from server.users import bootstrap_admin
from server import __main__ as gui


def test_local_admin_rechecks_password_enabled_and_role(tmp_path):
    db = Database(tmp_path)
    bootstrap_admin(db, 'admin', 'test-pass-1234')
    assert gui.verify_local_admin(db, 'admin', 'test-pass-1234')
    assert not gui.verify_local_admin(db, 'admin', 'wrong-password')
    assert not gui.verify_local_admin(db, 'missing', 'test-pass-1234')
    with db.connect(write=True) as c:
        c.execute('UPDATE users SET enabled=0')
    assert not gui.verify_local_admin(db, 'admin', 'test-pass-1234')
    with db.connect(write=True) as c:
        c.execute("UPDATE users SET enabled=1,role='operator'")
    assert not gui.verify_local_admin(db, 'admin', 'test-pass-1234')
    with db.connect() as c:
        assert c.execute('SELECT COUNT(*) FROM sessions').fetchone()[0] == 0


def test_local_admin_rate_limit_is_shared_across_usernames(tmp_path):
    db = Database(tmp_path)
    bootstrap_admin(db, 'admin', 'test-pass-1234')
    for i in range(10):
        assert not gui.verify_local_admin(db, 'missing'+str(i), 'bad')
    assert not gui.verify_local_admin(db, 'admin', 'test-pass-1234')


@pytest.fixture(scope='module')
def tk_session_root():
    # One Tcl interpreter per GUI test group; isolate windows with Toplevel.
    import tkinter as tk
    root = tk.Tk()
    root.withdraw()
    yield root
    root.destroy()


@pytest.fixture
def window(tmp_path, monkeypatch, tk_session_root):
    import tkinter as tk
    from tkinter import messagebox
    from types import SimpleNamespace
    import uvicorn
    db = Database(tmp_path)
    bootstrap_admin(db, 'admin', 'test-pass-1234')
    root = tk.Toplevel(tk_session_root)
    root.withdraw()
    monkeypatch.setattr(tk, 'Tk', lambda: root)
    monkeypatch.setattr(root, 'mainloop', lambda: None)
    errors, tasks, recovered, servers, threads = [], [], [], [], []
    monkeypatch.setattr(messagebox, 'showerror', lambda *a: errors.append(a))
    monkeypatch.setattr(messagebox, 'showinfo', lambda *a: None)
    monkeypatch.setattr(messagebox, 'askyesno', lambda *a: True)
    monkeypatch.setattr(gui, 'autostart', lambda args, enabled: tasks.append(enabled))
    monkeypatch.setattr(gui, 'recover_admin', lambda *a: recovered.append(a))
    monkeypatch.setattr(gui, 'create_app', lambda *a, **kw: object())
    class Thread:
        def __init__(self, **kwargs):
            self.alive = False
            threads.append(self)
        def is_alive(self): return self.alive
        def start(self): self.alive = True
        def join(self, timeout=None): self.alive = False
    def server(config):
        item = SimpleNamespace(should_exit=False, started=True)
        servers.append(item)
        return item
    monkeypatch.setattr(gui.threading, 'Thread', Thread)
    monkeypatch.setattr(uvicorn, 'Server', server)
    lock = gui.InstanceLock(tmp_path)
    with lock:
        gui.run_gui(SimpleNamespace(data_dir=tmp_path, host='127.0.0.1', port=8765), db, instance_lock=lock)
        def descendants(widget):
            for child in widget.winfo_children():
                yield child
                yield from descendants(child)
        widgets = list(descendants(root))
        entries = [w for w in widgets if isinstance(w, tk.Entry)]
        def click(prefix):
            next(w for w in widgets if isinstance(w, tk.Button) and w.cget('text').startswith(prefix)).invoke()
        def credentials(password='test-pass-1234'):
            entries[1].delete(0, 'end')
            entries[1].insert(0, password)
        yield SimpleNamespace(root=root, db=db, errors=errors, tasks=tasks, recovered=recovered,
                              servers=servers, threads=threads, lock=lock, click=click, credentials=credentials, entries=entries)
    try:
        for callback in root.tk.call('after', 'info'):
            root.after_cancel(callback)
        root.destroy()
    except tk.TclError:
        pass


def test_gui_controls_require_fresh_admin_credentials(window):
    w = window
    w.click('Iniciar servidor')
    w.click('Habilitar início')
    w.click('Remover início')
    assert not w.servers and not w.tasks
    w.credentials()
    w.click('Iniciar servidor')
    assert len(w.servers) == 1
    assert w.entries[1].get() == ''
    w.click('Parar servidor')
    assert not w.servers[0].should_exit
    close = w.root.protocol('WM_DELETE_WINDOW')
    w.root.tk.call(close)
    assert not w.servers[0].should_exit
    w.credentials()
    w.click('Parar servidor')
    assert w.servers[0].should_exit
    w.credentials()
    w.click('Remover início')
    assert w.tasks == [False]


def test_gui_recovery_requires_elevation_stopped_service_and_confirmation(window, monkeypatch):
    w = window
    monkeypatch.setattr(gui, 'local_recovery_allowed', lambda: False)
    w.credentials('replacement-password-123')
    w.click('Recuperar administrador')
    assert not w.recovered
    monkeypatch.setattr(gui, 'local_recovery_allowed', lambda: True)
    w.credentials()
    w.click('Iniciar servidor')
    w.credentials('replacement-password-123')
    w.click('Recuperar administrador')
    assert not w.recovered
    w.credentials()
    w.click('Parar servidor')
    w.threads[0].alive = False
    from tkinter import messagebox
    monkeypatch.setattr(messagebox, 'askyesno', lambda *a: False)
    w.credentials('replacement-password-123')
    w.click('Recuperar administrador')
    assert not w.recovered
    monkeypatch.setattr(messagebox, 'askyesno', lambda *a: True)
    w.credentials('replacement-password-123')
    w.click('Recuperar administrador')
    assert len(w.recovered) == 1
    assert w.recovered[0][1:] == ('admin', 'replacement-password-123')
    assert w.entries[1].get() == ''
    w.lock.__exit__()
    w.credentials('replacement-password-123')
    w.click('Recuperar administrador')
    assert len(w.recovered) == 1


def test_gui_autostart_and_close_accept_authenticated_admin(window):
    w = window
    w.credentials()
    w.click('Habilitar início')
    assert w.tasks == [True]
    w.credentials()
    w.click('Iniciar servidor')
    w.credentials()
    w.root.tk.call(w.root.protocol('WM_DELETE_WINDOW'))
    assert w.servers[0].should_exit
    assert not w.threads[0].alive


def test_first_admin_creation_is_only_for_an_empty_database(window):
    w = window
    import tkinter as tk
    def descendants(widget):
        for child in widget.winfo_children():
            yield child
            yield from descendants(child)
    widgets = list(descendants(w.root))
    first = next(x for x in widgets if isinstance(x, tk.Button) and x.cget('text').startswith('Criar primeiro'))
    assert first.cget('state') == 'disabled'
    # Even invoking the callback directly cannot overwrite an initialized DB.
    w.credentials('replacement-password-123')
    w.root.tk.call(first.cget('command'))
    assert gui.verify_local_admin(w.db, 'admin', 'test-pass-1234')
    with w.db.connect(write=True) as c:
        c.execute('DELETE FROM users')
    first.configure(state='normal')
    w.credentials('new-pass-12345')
    first.invoke()
    assert gui.verify_local_admin(w.db, 'admin', 'new-pass-12345')
    assert first.cget('state') == 'disabled'


def test_dashboard_renders_status_and_clears_revoked_session(tmp_path):
    """Execute the shipped JavaScript against a tiny DOM, without network/CDNs."""
    import json
    import shutil
    import subprocess
    from server.dashboard import HTML
    if not shutil.which('node'):
        pytest.skip('Node.js is required for dashboard JavaScript regression tests')
    source = HTML.split('<script>', 1)[1].split('</script>', 1)[0]
    harness = r'''
const vm = require('node:vm'), assert = require('node:assert/strict');
class Element {
  constructor(tag='div'){this.tagName=tag;this.children=[];this.value='';this.hidden=false;this.textContent='';this.dataset={};}
  append(...values){this.children.push(...values)}
  replaceChildren(...values){this.children=[...values];this.textContent=''}
  setAttribute(key,value){this[key]=value}
}
const elements = {}, document = {getElementById:id=>elements[id] ||= new Element(),createElement:tag=>new Element(tag)};
let invalid=false, delayed=null, profile='admin';
const requests=[];
const status={deliveries:[{receipt_id:'receipt',name:'Part.oma',device_id:'new',status:'conflict',error:'destination_exists'}],devices:[{id:'<img>',name:'Desk <script>',enabled:1,last_seen:new Date(Date.now()-46000).toISOString(),pending:3,error:'<b>Erro</b>'},{id:'new',name:'Novo',enabled:1,last_seen:null,pending:0,error:null}],files:[{device_id:'<img>',name:'<img>.txt',size:20,sha256:'abc',received_at:'2026-01-01T00:00:00Z'}]};
const context=vm.createContext({document,console,Date,Error,JSON,encodeURIComponent,setInterval:()=>{},fetch:async(path,opts)=>{
 requests.push({path,method:opts.method,headers:opts.headers,body:opts.body?JSON.parse(opts.body):null});
 if(path==='/api/logout')return new Promise(()=>{});
 if(delayed && path==='/api/status')return await delayed;
 if(invalid)return {status:401,ok:false,json:async()=>({detail:'revoked'})};
 const data=path==='/api/login'?{token:'secret',role:'admin'}:path==='/api/me'?{role:profile,username:'admin'}:path==='/api/activations'?(opts.method==='POST'?{code:'ONE-TIME-CODE',role:'delivery',expires_at:'2099-01-01T00:00:00Z'}:[{id:'a1',name:'<script>',role:'sender',expires_at:'2099-01-01T00:00:00Z',revoked:false}]):path==='/api/users'?[{username:'<script>',role:'operator',enabled:true}]:status;
 return {ok:true,status:200,json:async()=>data};
}});
vm.runInContext(SOURCE,context);
const run = code=>vm.runInContext(code,context);
const text = element=>[element.textContent,...element.children.map(x=>typeof x==='string'?x:text(x))].join(' ');
(async()=>{
 await run("document.getElementById('username').value='admin';document.getElementById('password').value='password';document.getElementById('loginform').onsubmit({preventDefault(){}})");
 assert.equal(elements.admin.hidden,false);
 assert.match(text(elements.devices),/Desatualizado/);
 assert.match(text(elements.devices),/Sem contato/);
 assert.match(text(elements.devices),/Desk <script>/);
 assert.match(text(elements.files),/<img>.txt/);
 assert.match(text(elements.users),/operator|Operador/);
 assert.equal(elements.password.value,'');
 const walk = node=>[node,...node.children.flatMap(child=>typeof child==='string'?[]:walk(child))];
 assert.match(text(elements.deliveries),/Conflito/);
 assert.match(text(elements.deliveries),/destination_exists/);
 assert.match(text(elements.activations),/<script>/);
 await run("document.getElementById('devicename').value='Worker';document.getElementById('activationrole').value='delivery';document.getElementById('activationhours').value='24';document.getElementById('deviceform').onsubmit({preventDefault(){}})");
 assert.match(text(elements.devicecredential),/ONE-TIME-CODE/);
 assert.deepEqual(requests.find(r=>r.path==='/api/activations'&&r.method==='POST').body,{name:'Worker',role:'delivery',expires_hours:24});
 await walk(elements.activations).find(node=>node.tagName==='button').onclick();
 assert.ok(requests.some(r=>r.path==='/api/activations/a1'&&r.method==='DELETE'));
 assert.ok(!requests.some(r=>r.path==='/api/devices'&&r.method==='POST'));
 const control = (tag,label)=>walk(elements.users).find(node=>node.tagName===tag && (node.textContent===label || node['aria-label']===label));
 control('select','Perfil de <script>').value='admin';
 await control('button','Salvar perfil').onclick();
 assert.deepEqual(requests.find(r=>r.method==='PATCH').body,{role:'admin'});
 control('input','Nova senha de <script>').value='new-pass-12345';
 const input=control('input','Nova senha de <script>');
 await control('button','Redefinir senha').onclick();
 assert.equal(input.value,'');
 assert.deepEqual(requests.filter(r=>r.method==='PATCH').at(-1).body,{password:'new-pass-12345'});
 await control('button','Desativar').onclick();
 assert.deepEqual(requests.filter(r=>r.method==='PATCH').at(-1).body,{enabled:false});
 assert.equal(requests.find(r=>r.method==='PATCH').path,'/api/users/%3Cscript%3E');
 profile='operator';await run('refresh()');
 assert.equal(elements.admin.hidden,true);
 assert.equal(text(elements.users).trim(),'');
 assert.ok(!walk(elements.devices).some(node=>node.tagName==='button'));
 profile='admin';await run('refresh()');
 invalid=true;
 await run('refresh()');
 assert.equal(elements.panel.hidden,true);
 assert.equal(elements.admin.hidden,true);
 assert.equal(run('isAdmin'),false);
 for(const id of ['devices','files','users','devicecredential','statussummary','activations','deliveries'])assert.equal(text(elements[id]).trim(),'');
 assert.equal(run('token'),'');
 // A late successful response from the old session must never repopulate data.
 invalid=false;
 await run("document.getElementById('loginform').onsubmit({preventDefault(){}})");
 let release;
 delayed=new Promise(resolve=>release=resolve);
 const pending=run('refresh()');
 await new Promise(resolve=>setImmediate(resolve));
 run("document.getElementById('logout').onclick()");
 assert.equal(elements.panel.hidden,true);
 const logout=requests.find(r=>r.path==='/api/logout');
 assert.ok(logout);assert.equal(logout.method,'POST');
 assert.equal(logout.headers.Authorization,'Bearer secret');
 release({ok:true,status:200,json:async()=>status});
 await pending;
 assert.equal(text(elements.devices).trim(),'');
 assert.equal(elements.panel.hidden,true);
})().catch(error=>{console.error(error);process.exitCode=1});
'''
    script = tmp_path / 'dashboard-test.cjs'
    script.write_text('const SOURCE='+json.dumps(source)+';\n'+harness, encoding='utf-8')
    result = subprocess.run(['node', str(script)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'innerHTML' not in source
    assert 'localStorage' not in source
    assert 'value="viewer"' not in HTML


def test_headless_bootstrap_cannot_mutate_without_exclusive_lock(tmp_path):
    import json
    bootstrap = tmp_path / 'bootstrap.json'
    bootstrap.write_text(json.dumps({'username': 'admin', 'password': 'test-pass-1234'}), encoding='utf-8')
    db = Database(tmp_path)
    with gui.InstanceLock(tmp_path):
        with pytest.raises(SystemExit):
            gui.main(['--headless', '--data-dir', str(tmp_path), '--bootstrap-file', str(bootstrap)])
    assert bootstrap.exists()
    with db.connect() as c:
        assert c.execute('SELECT COUNT(*) FROM users').fetchone()[0] == 0


def test_local_recovery_checks_windows_token(monkeypatch):
    import ctypes
    from types import SimpleNamespace
    monkeypatch.setattr(gui.os, 'name', 'nt')
    monkeypatch.setattr(ctypes, 'windll', SimpleNamespace(shell32=SimpleNamespace(IsUserAnAdmin=lambda: 0)), raising=False)
    assert not gui.local_recovery_allowed()
    monkeypatch.setattr(ctypes.windll.shell32, 'IsUserAnAdmin', lambda: 1)
    assert gui.local_recovery_allowed()
    def denied():
        raise OSError('token unavailable')
    monkeypatch.setattr(ctypes.windll.shell32, 'IsUserAnAdmin', denied)
    assert not gui.local_recovery_allowed()
