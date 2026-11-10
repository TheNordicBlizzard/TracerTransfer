"""Windows GUI bootstrap and loopback-only process entry point."""
import argparse
import ipaddress
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import webbrowser

from server.app import create_app
from server.database import Database
from server.users import HASHER, bootstrap_admin, recover_admin
from argon2.exceptions import VerificationError, InvalidHashError
from collections import deque
import time
import weakref


_local_attempts = weakref.WeakKeyDictionary()
_local_attempts_lock = threading.Lock()
_dummy_hash = HASHER.hash(os.urandom(32).hex())


def verify_local_admin(db, username, password):
    """Revalidate each local control action; never create sessions or save secrets.

    Same ten-attempt/60-second budget as HTTP login, shared across usernames.
    The GUI retains its Database for its lifetime, so reopening widgets cannot
    reset the budget. Successful checks consume a slot too, just like Auth.login.
    """
    now = time.monotonic()
    with _local_attempts_lock:
        attempts = _local_attempts.setdefault(db, deque())
        while attempts and attempts[0] <= now - 60:
            attempts.popleft()
        if len(attempts) >= 10:
            return False
        attempts.append(now)
    if not isinstance(username, str) or not isinstance(password, str) or len(username) > 64 or len(password) > 1024:
        return False
    with db.connect() as c:
        row = c.execute('SELECT password_hash,role,enabled FROM users WHERE username=?', (username,)).fetchone()
    try:
        valid = HASHER.verify(row['password_hash'] if row else _dummy_hash, password)
    except (VerificationError, InvalidHashError):
        valid = False
    if not valid or not row or not row['enabled'] or row['role'] != 'admin':
        return False
    # A concurrent password reset/disable must not authorize the old credential.
    with db.connect() as c:
        current = c.execute('SELECT password_hash,role,enabled FROM users WHERE username=?', (username,)).fetchone()
    return bool(current and current['enabled'] and current['role'] == 'admin'
                and current['password_hash'] == row['password_hash'])


def local_recovery_allowed():
    """Recovery is an elevated local OS operation, not an application login."""
    if os.name != 'nt':
        return False  # This GUI's recovery path is Windows-only; fail closed.
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return False


class InstanceLock:
    """OS-backed lock is released even after a crash; held through GUI lifetime."""
    def __init__(self, data_dir):
        self.path = Path(data_dir)/'server.lock'
        self.file = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.file = open(self.path, 'a+b')
        try:
            self.file.seek(0)
            if self.path.stat().st_size == 0:
                self.file.write(b'0')
                self.file.flush()
            self.file.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.file.close()
            self.file = None
            raise RuntimeError('Server already running for this data directory') from None
        return self

    def __exit__(self, *args):
        if self.file:
            if os.name == 'nt':
                import msvcrt
                self.file.seek(0)
                msvcrt.locking(self.file.fileno(), msvcrt.LK_UNLCK, 1)
            self.file.close()
            self.file = None


def consume_bootstrap(db, path):
    path = Path(path)
    if path.stat().st_size > 8192:
        path.unlink()
        raise ValueError('Bootstrap file too large')
    try:
        payload = json.loads(path.read_text(encoding='utf-8-sig'))
    finally:
        # Remove even on malformed JSON: never leave plaintext credentials behind.
        path.unlink(missing_ok=True)
    if not isinstance(payload, dict) or set(payload) != {'username','password'}:
        raise ValueError('Bootstrap JSON requires only username and password')
    bootstrap_admin(db, payload['username'], payload['password'])


def autostart(args, enabled):
    """Opt-in per-user scheduled task; never requests elevation or a password."""
    if os.name != 'nt':
        raise RuntimeError('Autostart is supported only on Windows')
    import hashlib
    suffix = hashlib.sha256(str(args.data_dir.resolve()).encode()).hexdigest()[:12]
    name = 'TracerTransfer-Server-' + suffix
    if not enabled:
        result = subprocess.run(['schtasks','/Delete','/TN',name,'/F'], capture_output=True, text=True)
    else:
        command = [sys.executable]
        if not getattr(sys,'frozen',False):
            # Task Scheduler has an unrelated working directory.
            root = str(Path(__file__).resolve().parents[1])
            command += ['-c', f'import sys;sys.path.insert(0,{root!r});from server.__main__ import main;main()']
        command += ['--headless','--data-dir',str(args.data_dir.resolve()),'--host',args.host,'--port',str(args.port)]
        command += ['--delivery-mode', getattr(args, 'delivery_mode', 'direct')]
        if getattr(args, 'output_dir', None) is not None:
            command += ['--output-dir', str(args.output_dir.absolute())]
        result = subprocess.run(['schtasks','/Create','/TN',name,'/TR',subprocess.list2cmdline(command),
                                 '/SC','ONLOGON','/RL','LIMITED','/IT','/F'], capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError('Task Scheduler refused the per-user task: '+result.stderr.strip())
    check = subprocess.run(['schtasks','/Query','/TN',name], capture_output=True, text=True)
    if (enabled and check.returncode) or (not enabled and check.returncode == 0):
        raise RuntimeError('Task Scheduler state verification failed')


def run_gui(args, db, instance_lock=None):
    import tkinter as tk
    from tkinter import messagebox
    import uvicorn
    root = tk.Tk()
    root.title('TracerTransfer — Servidor')
    root.geometry('640x500')
    root.resizable(False,False)
    server = None
    thread = None
    state = tk.StringVar(value='Parado — acesso externo somente por proxy HTTPS/Caddy.')
    tk.Label(root,text='TracerTransfer',font=('Segoe UI',20)).pack(pady=14)
    tk.Label(root,textvariable=state,wraplength=520).pack(pady=8)
    tk.Label(root,text='Usuário admin e senha: informe novamente para cada operação.').pack()
    tk.Label(root,text='Recuperação: nova senha, serviço parado e Windows elevado (UAC).').pack()
    username = tk.Entry(root)
    username.insert(0,'admin')
    username.pack(pady=5)
    password = tk.Entry(root,show='*',width=36)
    password.pack(pady=5)

    def running():
        return bool(thread and thread.is_alive())

    def authorized():
        try:
            if not verify_local_admin(db, username.get(), password.get()):
                messagebox.showerror('Autorização necessária',
                                     'Informe admin habilitado e senha válida (limite: 10 tentativas/minuto).')
                return False
            return True
        except Exception:
            messagebox.showerror('Autorização necessária', 'Não foi possível validar o administrador local.')
            return False
        finally:
            password.delete(0, tk.END)

    def account(recovery=False):
        try:
            if running():
                raise RuntimeError('Pare o servidor antes de criar ou recuperar o administrador.')
            if not instance_lock or not instance_lock.file or instance_lock.path.parent.resolve() != db.data_dir:
                raise RuntimeError('Operação local exige bloqueio exclusivo do diretório de dados.')
            with db.connect() as c:
                initialized = bool(c.execute('SELECT 1 FROM users LIMIT 1').fetchone())
            if recovery:
                if not initialized:
                    raise RuntimeError('Use Criar primeiro administrador para banco ainda vazio.')
                if not local_recovery_allowed():
                    raise RuntimeError('Recuperação exige token Windows elevado. Reabra como administrador do Windows.')
                if not messagebox.askyesno('Recuperar administrador — emergência local',
                                          'Usar a nova senha informada e revogar TODAS as sessões?'):
                    return
                recover_admin(db,username.get(),password.get())
            else:
                if initialized:
                    raise RuntimeError('Banco já inicializado. Autentique-se ou use recuperação elevada.')
                bootstrap_admin(db,username.get(),password.get())
            state.set('Administrador salvo. Informe sua senha novamente para iniciar.')
            account_buttons()
        except Exception as exc:
            messagebox.showerror('Não foi possível salvar',getattr(exc,'detail',str(exc)))
        finally:
            password.delete(0,tk.END)

    def start():
        nonlocal server, thread
        if running():
            return
        if not authorized():
            return
        server = uvicorn.Server(uvicorn.Config(create_app(args.data_dir, output_dir=getattr(args, 'output_dir', None), delivery_mode=getattr(args, 'delivery_mode', 'direct')), host=args.host,port=args.port,
                                             proxy_headers=False,access_log=False,log_config=None))
        def serve():
            try:
                server.run()
            except (Exception,SystemExit):
                pass  # Poller reports failed startup; no unsafe Tk access from worker.
        thread = threading.Thread(target=serve,daemon=True)
        thread.start()
        state.set('Iniciando...')

    def stop():
        if not running() or not authorized():
            return
        if server:
            server.should_exit = True
        state.set('Parando...')

    def poll():
        if thread and thread.is_alive() and server and server.started:
            state.set(f'Ativo em {args.host}:{args.port} — proxy HTTPS necessário para rede.')
        elif thread and not thread.is_alive():
            state.set('Parado. Se falhou ao iniciar, confira a porta e o diretório.')
        root.after(500,poll)

    def task(enabled):
        if not authorized():
            return
        try:
            autostart(args,enabled)
            messagebox.showinfo('Início automático','Configuração confirmada para este usuário.')
        except Exception as exc:
            messagebox.showerror('Início automático',str(exc))

    accounts = tk.Frame(root)
    accounts.pack(pady=5)
    first_button = tk.Button(accounts,text='Criar primeiro administrador',command=account)
    first_button.pack(side='left',padx=5)
    recovery_button = tk.Button(accounts,text='Recuperar administrador (Windows elevado)',command=lambda:account(True))
    recovery_button.pack(side='left',padx=5)
    def account_buttons():
        with db.connect() as c:
            initialized = bool(c.execute('SELECT 1 FROM users LIMIT 1').fetchone())
        first_button.configure(state='disabled' if initialized else 'normal')
        recovery_button.configure(state='normal' if initialized else 'disabled')
    account_buttons()
    buttons = tk.Frame(root)
    buttons.pack(pady=10)
    tk.Button(buttons,text='Iniciar servidor (admin)',command=start).pack(side='left',padx=5)
    tk.Button(buttons,text='Parar servidor (admin)',command=stop).pack(side='left',padx=5)
    panel_host = '['+args.host+']' if ':' in args.host else args.host
    tk.Button(buttons,text='Abrir painel',command=lambda:webbrowser.open(f'http://{panel_host}:{args.port}/')).pack(side='left',padx=5)
    tasks = tk.Frame(root)
    tasks.pack()
    tk.Button(tasks,text='Habilitar início no login (admin)',command=lambda:task(True)).pack(side='left',padx=5)
    tk.Button(tasks,text='Remover início automático (admin)',command=lambda:task(False)).pack(side='left',padx=5)
    def close():
        if running() and not authorized():
            return
        password.delete(0, tk.END)
        if server:
            server.should_exit=True
        if thread and thread.is_alive():
            thread.join(timeout=10)
            if thread.is_alive():
                messagebox.showerror('Aguarde','O servidor ainda está encerrando uploads. Tente novamente.')
                return
        root.destroy()
    root.protocol('WM_DELETE_WINDOW',close)
    root.after(500,poll)
    root.mainloop()


def main(argv=None):
    parser = argparse.ArgumentParser(description='TracerTransfer server: loopback only; use HTTPS reverse proxy')
    default = Path(os.environ.get('LOCALAPPDATA',str(Path.home())))/'TracerTransfer'/'server'
    parser.add_argument('--data-dir',type=Path,default=default)
    parser.add_argument('--host',default='127.0.0.1')
    parser.add_argument('--port',type=int,default=8765)
    parser.add_argument('--output-dir', type=Path, help='Existing same-volume directory for flat, no-overwrite delivery.')
    parser.add_argument('--headless',action='store_true')
    parser.add_argument('--delivery-mode', choices=('direct', 'queued'), default='direct')
    parser.add_argument('--bootstrap-file',type=Path)
    args = parser.parse_args(argv)
    try:
        if not ipaddress.ip_address(args.host).is_loopback:
            parser.error('Bind only to a loopback IP; publish via Caddy HTTPS reverse proxy')
    except ValueError:
        parser.error('--host must be a numeric loopback IP')
    if not 1 <= args.port <= 65535:
        parser.error('Port must be between 1 and 65535')
    args.data_dir = args.data_dir.resolve()
    try:
        with InstanceLock(args.data_dir) as instance_lock:
            db = Database(args.data_dir)
            if args.bootstrap_file:
                consume_bootstrap(db,args.bootstrap_file)
            if args.headless:
                with db.connect() as c:
                    if not c.execute("SELECT 1 FROM users WHERE role='admin' AND enabled=1").fetchone():
                        raise ValueError('First admin required: start GUI or supply --bootstrap-file')
                import uvicorn
                uvicorn.run(create_app(args.data_dir, output_dir=args.output_dir, delivery_mode=args.delivery_mode),host=args.host,port=args.port,
                            proxy_headers=False,access_log=False,log_config=None)
            else:
                run_gui(args,db,instance_lock=instance_lock)
    except (RuntimeError,ValueError,OSError) as exc:
        parser.exit(1, str(exc)+'\n')


if __name__ == '__main__':
    main()
