"""Tk status/configuration and guarded administrative operations.

Application authorization does not protect files or processes from the OS user/admin.
"""
from dataclasses import replace
import os
from pathlib import Path
import subprocess
import sys
import threading
import queue
import math
import time
from client.auth import Authorizer, AuthorizationError
from client.state import Config, read_config, save_config, protect_token, COMPANY_SERVER, baseline_folder
from client.activation import ActivationService


class ConfigurationService:
    def __init__(self, data_dir):
        self.data_dir = Path(data_dir)

    def apply(self, config, auth, *, old_auth=None, enroll_name=None):
        """Deprecated: explicit direct-mode compatibility for internal migration/tests only.

        Never exposed in the end-user UI. Queued servers reject legacy enrollment.
        """
        config = replace(config, delivery_mode='direct')
        config.validate()
        previous = read_config(self.data_dir)
        changed_server = previous.server_url.rstrip('/') != config.server_url.rstrip('/')
        if previous.server_url and changed_server:
            if old_auth is None or old_auth.server_url != previous.server_url.rstrip('/'):
                raise AuthorizationError('Autentique no servidor antigo antes de migrar.')
            old_auth.require_admin()
        if auth.server_url != config.server_url.rstrip('/') or auth.ca_file != config.ca_file:
            raise AuthorizationError('Faça login no servidor/certificado de destino.')
        auth.require_admin()
        if changed_server or not previous.protected_token or enroll_name:
            if not enroll_name or not enroll_name.strip():
                raise ValueError('Informe nome do dispositivo para inscrição.')
            device = auth.enroll(enroll_name.strip())
            config = replace(config, device_id=str(device['id']), protected_token=protect_token(device['token']))
        else:
            config = replace(config, device_id=previous.device_id, protected_token=previous.protected_token)
        # Revalidate immediately before local mutation (network may have failed during enrollment).
        auth.require_admin()
        if previous.server_url and changed_server:
            old_auth.require_admin()
        save_config(self.data_dir, config)
        return config

    def pause(self, auth=None):
        config = read_config(self.data_dir)
        save_config(self.data_dir, replace(config, paused=not config.paused))

    def startup(self, enabled):
        config = read_config(self.data_dir)
        set_startup(self.data_dir, enabled)
        save_config(self.data_dir, replace(config, startup_enabled=enabled))

    def folder(self, folder):
        baseline = baseline_folder(folder)
        config = read_config(self.data_dir)
        changed = Path(folder).resolve() != Path(config.source_dir).resolve() if config.source_dir else True
        save_config(self.data_dir, replace(config, source_dir=str(Path(folder).resolve()),
                    activation_baseline=baseline if changed else config.activation_baseline))


def set_startup(data_dir, enabled):
    if os.name != 'nt':
        raise OSError('Inicialização automática requer Windows.')
    import winreg
    import hashlib
    label = 'TracerTransfer-' + hashlib.sha256(str(Path(data_dir).resolve()).lower().encode()).hexdigest()[:16]
    if getattr(sys, 'frozen', False):
        argv = [sys.executable]
    else:
        # pythonw prevents an unwanted console during user login.
        exe = Path(sys.executable).with_name('pythonw.exe')
        launcher = Path(__file__).resolve().parent.parent / 'launch_client.py'
        argv = [str(exe if exe.exists() else sys.executable), str(launcher)]
    command = subprocess.list2cmdline(argv + ['--background', '--data-dir', str(Path(data_dir).resolve())])
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r'Software\Microsoft\Windows\CurrentVersion\Run') as key:
        if enabled:
            winreg.SetValueEx(key, label, 0, winreg.REG_SZ, command)
            if winreg.QueryValueEx(key, label)[0] != command:
                raise OSError('Não foi possível verificar inicialização.')
        else:
            try:
                winreg.DeleteValue(key, label)
            except FileNotFoundError:
                pass
            try:
                winreg.QueryValueEx(key, label)
            except FileNotFoundError:
                return
            raise OSError('Inicialização permanece configurada.')


def describe_status(status, *, activated, now=None):
    """Presentation only: recent heartbeat is not proof of a live connection."""
    now = time.time() if now is None else now
    heartbeat = status.last_heartbeat
    age = now - heartbeat if isinstance(heartbeat, (int, float)) else None
    recent = age is not None and math.isfinite(age) and 0 <= age <= 45
    contact = (f'Último contato há {int(age)} s' if age is not None and math.isfinite(age) and age >= 0
               else 'Sem contato confirmado')
    if not activated:
        title, tone = 'Ative este computador', 'warning'
    elif status.paused:
        title, tone = 'Envios pausados', 'warning'
    elif status.error:
        title, tone = 'Atenção no envio', 'danger'
    elif status.conflicts or status.uncertain:
        title, tone = 'Há arquivos para revisar', 'warning'
    elif not recent:
        title, tone = 'Aguardando contato com o servidor', 'muted'
    elif status.awaiting_delivery:
        title, tone = 'Aguardando entrega no destino', 'accent'
    elif status.pending:
        title, tone = 'Arquivos na fila de envio', 'accent'
    else:
        title, tone = 'Acompanhando a pasta', 'accent'
    return {'title': title, 'tone': tone, 'contact': contact,
            'detail': status.error or (f'{status.conflicts} conflito(s) · {status.uncertain} entrega(s) incerta(s). '
                'Solicite análise ao responsável; não renomeie nem reenvie os arquivos.'
                if status.conflicts or status.uncertain else '')}


class ClientUI:
    """Local operator controls. All I/O runs off Tk's main loop."""
    def __init__(self, agent, *, root=None):
        import tkinter as tk
        from tkinter import ttk
        self.agent = agent
        self.agent.user_mode = True
        self.service = ConfigurationService(agent.data_dir)
        self.events = queue.Queue()
        self.root = root if root is not None else tk.Tk()
        self.root.title('TracerTransfer')
        height = min(760, max(640, self.root.winfo_screenheight() - 100))
        self.root.geometry(f'880x{height}')
        self.root.minsize(760, 640)
        self.root.configure(background='#101416')
        self.root.protocol('WM_DELETE_WINDOW', self.hide)
        self.tray = None
        self.closed = False
        self.busy = False
        self._configure_styles()
        frame = ttk.Frame(self.root, padding=(24, 18), style='Tracer.TFrame')
        frame.pack(fill='both', expand=True)
        # Reserve actions first: long activation/error content scrolls, Sair never does.
        footer = ttk.Frame(frame, style='Tracer.TFrame')
        footer.pack(fill='x', side='bottom')
        viewport = tk.Canvas(frame, background='#101416', highlightthickness=0, takefocus=True)
        scrollbar = ttk.Scrollbar(frame, orient='vertical', command=viewport.yview, style='Tracer.Vertical.TScrollbar')
        scrollbar.pack(side='right', fill='y', padx=(8, 0))
        viewport.pack(side='left', fill='both', expand=True)
        viewport.configure(yscrollcommand=scrollbar.set)
        body = ttk.Frame(viewport, style='Tracer.TFrame')
        body_window = viewport.create_window((0, 0), window=body, anchor='nw')
        body.bind('<Configure>', lambda event: viewport.configure(scrollregion=viewport.bbox('all')))
        viewport.bind('<Configure>', lambda event: viewport.itemconfigure(body_window, width=event.width))
        self.root.bind('<MouseWheel>', lambda event: viewport.yview_scroll(-int(event.delta / 120), 'units')
                       if viewport.yview() != (0.0, 1.0) else None)
        self.body_viewport = viewport
        frame = body
        header = ttk.Frame(frame, style='Tracer.TFrame')
        header.pack(fill='x', pady=(0, 14))
        ttk.Label(header, text='TracerTransfer', style='Tracer.Brand.TLabel').pack(side='left')
        ttk.Label(header, text='CLIENTE WINDOWS', style='Tracer.Muted.TLabel').pack(side='right', pady=8)
        state = ttk.Frame(frame, style='Tracer.TFrame')
        state.pack(fill='x', pady=(0, 14))
        self.status_text = tk.StringVar(master=self.root, value='Preparando acompanhamento')
        self.state_label = ttk.Label(state, textvariable=self.status_text, style='Tracer.Title.TLabel')
        self.state_label.pack(anchor='w')
        self.contact_text = tk.StringVar(master=self.root, value='Sem contato confirmado')
        ttk.Label(state, textvariable=self.contact_text, style='Tracer.Muted.TLabel').pack(anchor='w', pady=(4, 0))
        metrics = ttk.Frame(frame, style='Tracer.Card.TFrame', padding=(16, 12))
        metrics.pack(fill='x', pady=(0, 14))
        self.metrics = {}
        for column, (key, title) in enumerate([('pending', 'Na fila local'), ('awaiting_delivery', 'Aguardando destino'),
                                                ('sent', 'Entregues'), ('review', 'Para revisar')]):
            metrics.columnconfigure(column, weight=1, uniform='metric')
            cell = ttk.Frame(metrics, style='Tracer.Card.TFrame')
            cell.grid(row=0, column=column, sticky='nsew', padx=(0, 8))
            self.metrics[key] = tk.StringVar(master=self.root, value='0')
            ttk.Label(cell, textvariable=self.metrics[key], style='Tracer.Number.TLabel').pack(anchor='w')
            ttk.Label(cell, text=title, style='Tracer.CardMuted.TLabel').pack(anchor='w', pady=(2, 0))
        self.alert_text = tk.StringVar(master=self.root)
        self.alert_label = ttk.Label(frame, textvariable=self.alert_text, style='Tracer.Error.TLabel', wraplength=700)
        self.alert_label.pack(fill='x', pady=(0, 8))
        cfg = read_config(agent.data_dir)
        self.folder = tk.StringVar(master=self.root, value=cfg.source_dir)
        self.name = tk.StringVar(master=self.root, value=os.environ.get('COMPUTERNAME', 'Cliente Windows'))
        self.code = tk.StringVar(master=self.root)
        self.identity_text = tk.StringVar(master=self.root)
        self.feedback_text = tk.StringVar(master=self.root)
        self.entries = []
        folder_card = ttk.Frame(frame, padding=16, style='Tracer.Card.TFrame')
        self.folder_card = folder_card
        folder_card.pack(fill='x', pady=(0, 12))
        ttk.Label(folder_card, text='Pasta dos arquivos', style='Tracer.CardTitle.TLabel').pack(anchor='w')
        ttk.Label(folder_card, text='Novos arquivos .OMA e .VCA desta pasta entram na fila de envio.',
                  style='Tracer.CardMuted.TLabel').pack(anchor='w', pady=(4, 8))
        row = ttk.Frame(folder_card, style='Tracer.Card.TFrame')
        row.pack(fill='x')
        entry = ttk.Entry(row, textvariable=self.folder, style='Tracer.TEntry')
        entry.pack(side='left', fill='x', expand=True)
        self.entries.append(entry)
        choose = ttk.Button(row, text='Escolher…', style='Tracer.TButton', command=lambda: self.safe(self.choose_folder))
        choose.pack(side='left', padx=(8, 0))
        save = ttk.Button(row, text='Salvar pasta', style='Tracer.TButton', command=lambda: self.safe(self.apply))
        save.pack(side='left', padx=(8, 0))
        self.activation_card = ttk.Frame(frame, padding=16, style='Tracer.Card.TFrame')
        self.activation_card.pack(fill='x', pady=(0, 12))
        ttk.Label(self.activation_card, text='Ativação do computador', style='Tracer.CardTitle.TLabel').pack(anchor='w')
        ttk.Label(self.activation_card, text='Use o código individual fornecido pelo responsável. Não é uma senha administrativa.',
                  style='Tracer.CardMuted.TLabel', wraplength=700).pack(anchor='w', pady=(4, 10))
        for label, variable, secret in [('Nome do computador', self.name, False), ('Código de ativação', self.code, True)]:
            line = ttk.Frame(self.activation_card, style='Tracer.Card.TFrame')
            line.pack(fill='x', pady=(0, 8))
            ttk.Label(line, text=label, width=22, style='Tracer.Card.TLabel').pack(side='left')
            entry = ttk.Entry(line, textvariable=variable, show='*' if secret else '', style='Tracer.TEntry')
            entry.pack(side='left', fill='x', expand=True)
            self.entries.append(entry)
        activate = ttk.Button(self.activation_card, text='Ativar computador', style='Tracer.Primary.TButton',
                              command=lambda: self.safe(self.activate))
        activate.pack(anchor='e')
        self.preferences = ttk.Frame(frame, style='Tracer.TFrame')
        self.preferences.pack(fill='x', pady=(0, 10))
        self.startup_text = tk.StringVar(master=self.root)
        ttk.Label(self.preferences, textvariable=self.startup_text, style='Tracer.Muted.TLabel').pack(side='left')
        start_buttons = []
        for label, enabled in [('Ativar no login', True), ('Desativar', False)]:
            button = ttk.Button(self.preferences, text=label, style='Tracer.TButton',
                command=lambda e=enabled: self._submit(lambda: self.service.startup(e)))
            button.pack(side='left', padx=(8, 0))
            start_buttons.append(button)
        ttk.Label(footer, text=COMPANY_SERVER, style='Tracer.Muted.TLabel').pack(anchor='w', pady=(8, 0))
        ttk.Label(footer, text='Recebido no servidor ≠ entregue ao destino. Arquivos anteriores à ativação são ignorados.\n'
                  'Fechar mantém na bandeja; Sair encerra o cliente.', style='Tracer.Muted.TLabel',
                  wraplength=700).pack(anchor='w', pady=(6, 0))
        commands = ttk.Frame(footer, style='Tracer.TFrame')
        commands.pack(fill='x', pady=(10, 0))
        pause = ttk.Button(commands, text='Pausar envios', style='Tracer.TButton', command=lambda: self.safe(self.pause))
        pause.pack(side='left')
        exit_button = ttk.Button(commands, text='Sair', style='Tracer.TButton', command=lambda: self.safe(self.exit))
        exit_button.pack(side='right')
        ttk.Label(commands, textvariable=self.feedback_text, style='Tracer.Muted.TLabel').pack(side='left', padx=12)
        ttk.Label(frame, textvariable=self.identity_text, style='Tracer.Muted.TLabel').pack(anchor='w')
        self.local_buttons = [choose, save, activate, pause, exit_button, *start_buttons]
        for control in [*self.entries, choose, save, activate, *start_buttons]:
            control.bind('<FocusIn>', self._reveal_control)
        self.admin_buttons = []  # Compatibility: no administrative controls exist.
        self.worker = threading.Thread(target=agent.run, daemon=True, name='transfer-agent')
        self.worker.start()
        self._start_tray()
        self.update_permissions()
        self.root.after(100, self.refresh)

    def _reveal_control(self, event):
        """Keyboard focus must remain visible when onboarding needs scrolling."""
        canvas = self.body_viewport
        canvas.update_idletasks()
        bounds = canvas.bbox('all')
        if not bounds or bounds[3] <= canvas.winfo_height():
            return
        top = event.widget.winfo_rooty() - canvas.winfo_rooty()
        bottom = top + event.widget.winfo_height()
        offset = top - 8 if top < 0 else bottom - canvas.winfo_height() + 8 if bottom > canvas.winfo_height() else 0
        if offset:
            canvas.yview_moveto(max(0, canvas.canvasy(0) + offset) / bounds[3])

    def _configure_styles(self):
        from tkinter import ttk
        style = ttk.Style(self.root)
        style.theme_use('clam')
        colors = {'background': '#101416', 'foreground': '#edf2f1', 'font': ('Segoe UI', 10)}
        style.configure('Tracer.TFrame', background='#101416')
        style.configure('Tracer.Card.TFrame', background='#181e21')
        style.configure('Tracer.Vertical.TScrollbar', background='#354943', troughcolor='#101416',
                        bordercolor='#101416', arrowcolor='#a5b5b0')
        style.configure('Tracer.TLabel', **colors)
        for name, settings in {
            'Brand': {'font': ('Segoe UI', 20, 'bold')},
            'Title': {'font': ('Segoe UI', 15, 'bold')},
            'Muted': {'foreground': '#a5b5b0'},
            'Error': {'foreground': '#ffb4ac'},
            'Card': {'background': '#181e21'},
            'CardTitle': {'background': '#181e21', 'font': ('Segoe UI', 11, 'bold')},
            'CardMuted': {'background': '#181e21', 'foreground': '#a5b5b0'},
            'Number': {'background': '#181e21', 'font': ('Segoe UI', 20, 'bold')},
        }.items():
            style.configure(f'Tracer.{name}.TLabel', **(colors | settings))
        for tone, color in [('accent', '#58d6aa'), ('warning', '#f1c774'), ('danger', '#ffb4ac'), ('muted', '#a5b5b0')]:
            style.configure(f'Tracer.{tone}.TLabel', **(colors | {'foreground': color, 'font': ('Segoe UI', 15, 'bold')}))
        style.configure('Tracer.TEntry', fieldbackground='#101416', foreground='#edf2f1',
                        insertcolor='#edf2f1', bordercolor='#3b4b48', padding=7, font=('Segoe UI', 10))
        style.map('Tracer.TEntry', fieldbackground=[('disabled', '#20282b')],
                  foreground=[('disabled', '#a5b5b0')], bordercolor=[('focus', '#58d6aa')])
        style.configure('Tracer.TButton', background='#263330', foreground='#edf2f1', padding=(12, 7),
                        borderwidth=1, bordercolor='#3b4b48', font=('Segoe UI', 10))
        style.map('Tracer.TButton', background=[('active', '#354943'), ('disabled', '#20282b')],
                  foreground=[('disabled', '#788985')])
        style.configure('Tracer.Primary.TButton', background='#58d6aa', foreground='#101b17', padding=(16, 7),
                        font=('Segoe UI', 10, 'bold'), borderwidth=0)
        style.map('Tracer.Primary.TButton', background=[('disabled', '#263330'), ('active', '#85e8c4')],
                  foreground=[('disabled', '#a5b5b0')])

    def safe(self, operation):
        from tkinter import messagebox
        try:
            operation()
        except Exception as exc:
            messagebox.showerror('TracerTransfer', str(exc), parent=self.root)

    def _submit(self, operation, *, activated=False):
        if self.busy or self.closed:
            return
        self.busy = True
        self.feedback_text.set('Aplicando alteração…')
        self.update_permissions()
        def work():
            try:
                # Lock acquisition itself can wait for an upload: never do it on Tk.
                with self.agent.lock:
                    operation()
                self.events.put(('activated' if activated else 'done', None))
            except Exception as exc:
                self.events.put(('error', str(exc)))
        threading.Thread(target=work, daemon=True, name='client-configuration').start()

    def choose_folder(self):
        from tkinter import filedialog
        folder = filedialog.askdirectory(parent=self.root)
        if folder:
            self.folder.set(folder)

    def apply(self):
        folder = self.folder.get()
        self._submit(lambda: self.service.folder(folder))

    def activate(self):
        code, name, folder = self.code.get(), self.name.get(), self.folder.get()
        def enroll():
            self.service.folder(folder)
            ActivationService(self.agent.data_dir, transport=self.agent.transport).activate(code, name)
        self._submit(enroll, activated=True)

    def pause(self):
        self._submit(self.service.pause)

    def exit(self):
        self.shutdown()

    def update_permissions(self):
        for button in self.local_buttons:
            button.configure(state='disabled' if self.busy else 'normal')
        self.local_buttons[4].configure(state='normal')  # Sair never depends on network.
        cfg = read_config(self.agent.data_dir)
        self.current_config = cfg
        self.identity_text.set('Ativado — remetente' if cfg.activated else
                               'Ativação por código obrigatória — solicite ao responsável')
        self.startup_text.set('Início no login: '+('ativado' if cfg.startup_enabled else 'desativado'))
        self.local_buttons[3].configure(text='Retomar envios' if cfg.paused else 'Pausar envios')
        self.local_buttons[5].configure(state='disabled' if self.busy or cfg.startup_enabled else 'normal')
        self.local_buttons[6].configure(state='disabled' if self.busy or not cfg.startup_enabled else 'normal')
        if cfg.activated:
            self.activation_card.pack_forget()
        elif not self.activation_card.winfo_manager():
            self.activation_card.pack(fill='x', pady=(0, 12), before=self.preferences)
        # No central login, editable server address or CA field.
        self.local_buttons[2].configure(state='disabled' if self.busy or cfg.activated else 'normal')
        for entry in self.entries[1:]:
            entry.configure(state='disabled' if self.busy or cfg.activated else 'normal')
        self.entries[0].configure(state='disabled' if self.busy else 'normal')

    def shutdown(self):
        if self.closed:
            return
        self.closed = True
        self.agent.stop_event.set()
        self.code.set('')
        if self.tray:
            self.tray.stop()
        self.root.destroy()

    def hide(self):
        if self.tray and self.tray.visible:
            self.root.withdraw()
        else:
            self.root.iconify()

    def _start_tray(self):
        try:
            import pystray
            from PIL import Image, ImageDraw
            image = Image.new('RGB', (64, 64), '#15344d')
            ImageDraw.Draw(image).text((15, 23), 'TT', fill='white')
            def send(action):
                return lambda icon, item: self.events.put((action, None))
            self.tray = pystray.Icon('TracerTransfer', image, 'TracerTransfer', pystray.Menu(
                pystray.MenuItem('Mostrar status', send('show'), default=True),
                pystray.MenuItem('Pausar / retomar', send('pause')),
                pystray.MenuItem('Sair', send('exit'))))
            self.tray.run_detached()
        except Exception:
            self.tray = None

    def refresh(self):
        if self.closed:
            return
        if getattr(self.agent, 'update_requested', None) is not None and self.agent.update_requested.is_set():
            self.shutdown()  # On the Tk thread: release tray/UI before helper installation.
            return
        from tkinter import messagebox
        s = self.agent.status
        presentation = describe_status(s, activated=self.current_config.activated)
        self.status_text.set(presentation['title'])
        self.state_label.configure(style=f'Tracer.{presentation["tone"]}.TLabel')
        self.contact_text.set(presentation['contact']+' · contato recente não comprova conexão ativa')
        self.alert_text.set(presentation['detail'])
        if presentation['detail']:
            if not self.alert_label.winfo_manager():
                self.alert_label.pack(fill='x', pady=(0, 8), before=self.folder_card)
        else:
            self.alert_label.pack_forget()
        for key in ('pending', 'awaiting_delivery', 'sent'):
            self.metrics[key].set(str(getattr(s, key)))
        self.metrics['review'].set(str(s.conflicts+s.uncertain))
        while not self.events.empty():
            action, value = self.events.get_nowait()
            if action in ('done', 'activated', 'error'):
                self.busy = False
                self.feedback_text.set('Não foi possível salvar.' if action == 'error' else
                                       'Computador ativado.' if action == 'activated' else 'Alteração salva.')
                if action == 'activated':
                    self.code.set('')
                if action == 'error':
                    messagebox.showerror('TracerTransfer', value, parent=self.root)
                self.update_permissions()
            elif action == 'show':
                self.root.deiconify()
                self.root.lift()
            elif action in ('pause', 'exit'):
                self.safe(getattr(self, action))
                if self.closed:
                    return
        self.root.after(200, self.refresh)

    def run(self):
        try:
            self.root.mainloop()
        finally:
            self.agent.stop_event.set()
            self.worker.join(timeout=120)

