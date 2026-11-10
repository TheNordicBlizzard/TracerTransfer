"""Persistent file queue; immutable bounded snapshots; independent heartbeat timer."""
import hashlib
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import httpx
from urllib.parse import quote
from client.auth import http_client
from client.state import read_config, unprotect_token, file_signature, COMPANY_SERVER, authorized_sender_profile
from client.status import Status

MAX_FILE_BYTES = 20 * 1024 * 1024
HEARTBEAT_SECONDS = 15


class Agent:
    def __init__(self, data_dir, *, transport=None, clock=time.time):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.spool = self.data_dir / 'spool'
        self.spool.mkdir(exist_ok=True)
        self.transport = transport
        self.user_mode = False  # Final GUI never accepts the explicit test/direct path.
        self.clock = clock
        self.status = Status()
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.update_requested = threading.Event()
        self.auto_update = False  # Entry point opts in only for the installed frozen profile.
        self.db = sqlite3.connect(self.data_dir / 'queue.sqlite3', check_same_thread=False)
        self.db.executescript('''
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS observations(path TEXT PRIMARY KEY,signature TEXT,first REAL,last REAL);
        CREATE TABLE IF NOT EXISTS queue(
            path TEXT,sha TEXT,name TEXT,snapshot TEXT,state TEXT DEFAULT 'pending',
            attempts INTEGER DEFAULT 0,next_retry REAL DEFAULT 0,error TEXT,
            PRIMARY KEY(path,sha));
        ''')
        columns = {r[1] for r in self.db.execute('PRAGMA table_info(queue)')}
        if 'receipt_id' not in columns:
            self.db.execute('ALTER TABLE queue ADD COLUMN receipt_id TEXT')
            self.db.commit()
        observed_columns = {r[1] for r in self.db.execute('PRAGMA table_info(observations)')}
        if 'captured_signature' not in observed_columns:
            self.db.execute('ALTER TABLE observations ADD COLUMN captured_signature TEXT')
            self.db.commit()
        # Keep all nonterminal snapshots, including conflict/uncertain evidence.
        known = {r[0] for r in self.db.execute("SELECT snapshot FROM queue WHERE state!='sent'")}
        for p in self.spool.iterdir():
            if str(p) not in known:
                p.unlink(missing_ok=True)

    def _counts(self):
        self.status.pending = self.db.execute("SELECT count(*) FROM queue WHERE state!='sent'").fetchone()[0]
        self.status.sent = self.db.execute("SELECT count(*) FROM queue WHERE state='sent'").fetchone()[0]
        self.status.awaiting_delivery = self.db.execute("SELECT count(*) FROM queue WHERE state='awaiting'").fetchone()[0]
        self.status.conflicts = self.db.execute("SELECT count(*) FROM queue WHERE state='conflict'").fetchone()[0]
        self.status.uncertain = self.db.execute("SELECT count(*) FROM queue WHERE state='uncertain'").fetchone()[0]
        if not self.status.error:
            row = self.db.execute("SELECT error FROM queue WHERE state!='sent' AND error IS NOT NULL ORDER BY rowid LIMIT 1").fetchone()
            if row:
                self.status.error = row[0]

    def _authorized_config(self, config):
        if self.user_mode:
            return authorized_sender_profile(config)
        return config.delivery_mode == 'direct' or config.activated

    def _scan(self, config):
        now = self.clock()
        root = Path(config.source_dir)
        if not root.is_dir() or root.is_symlink() or root.is_junction():
            raise ValueError('Pasta de origem indisponível.')
        seen = set()
        for p in root.iterdir():
            if p.is_symlink() or (hasattr(p, 'is_junction') and p.is_junction()) or not p.is_file() or p.suffix.lower() not in ('.oma', '.vca'):
                continue
            path = str(p.resolve())
            seen.add(path)
            try:
                st = p.stat()
                if not 0 < st.st_size <= MAX_FILE_BYTES:
                    self.status.error = f'Arquivo vazio ou acima de {MAX_FILE_BYTES} bytes: {p.name}'
                    self.db.execute('DELETE FROM observations WHERE path=?', (path,))
                    continue
                signature = file_signature(p)
                if config.activation_baseline.get(path) == signature:
                    continue
                old = self.db.execute('SELECT signature,first,last,captured_signature FROM observations WHERE path=?', (path,)).fetchone()
                if not old or old[0] != signature:
                    self.db.execute('INSERT OR REPLACE INTO observations(path,signature,first,last) VALUES (?,?,?,?)', (path, signature, now, now))
                    continue
                if old[3] == signature:
                    continue  # Already captured, including pending/conflict/uncertain.
                if now - old[1] < max(config.stable_seconds, config.poll_seconds) or now - st.st_mtime < config.stable_seconds:
                    continue
                self.db.execute('UPDATE observations SET last=? WHERE path=?', (now, path))
                if self._snapshot(p, path, st):
                    self.db.execute('UPDATE observations SET captured_signature=? WHERE path=?', (signature, path))
            except (OSError, ValueError) as exc:
                self.status.error = f'Leitura indisponível: {p.name} ({type(exc).__name__})'
        for (path,) in self.db.execute('SELECT path FROM observations').fetchall():
            if path not in seen:
                self.db.execute('DELETE FROM observations WHERE path=?', (path,))
        self.db.commit()

    def _snapshot(self, source, path, before):
        fd, name = tempfile.mkstemp(prefix='snapshot-', dir=self.spool)
        snapshot = Path(name)
        try:
            digest = hashlib.sha256()
            length = 0
            with os.fdopen(fd, 'wb') as dest, source.open('rb') as src:
                # Reject reparse-point swaps before touching their content.
                if source.is_symlink() or getattr(source, 'is_junction', lambda: False)():
                    return
                opened = os.fstat(src.fileno())
                if (opened.st_ino, opened.st_mtime_ns, opened.st_size) != (before.st_ino, before.st_mtime_ns, before.st_size):
                    return
                while chunk := src.read(1024 * 1024):
                    length += len(chunk)
                    if length > MAX_FILE_BYTES:
                        return
                    dest.write(chunk)
                    digest.update(chunk)
                after = os.fstat(src.fileno())
                if (opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns) or length != before.st_size:
                    return
                dest.flush()
                os.fsync(dest.fileno())
            sha = digest.hexdigest()
            self.db.execute('INSERT OR IGNORE INTO queue(path,sha,name,snapshot) VALUES(?,?,?,?)',
                (path, sha, source.name, str(snapshot)))
            if self.db.execute('SELECT changes()').fetchone()[0]:
                self.db.commit()
                snapshot = None
            return True
        finally:
            if snapshot is not None:
                snapshot.unlink(missing_ok=True)

    @staticmethod
    def _safe_error(exc, name):
        status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
        if status in (401, 403):
            return 'Credencial revogada ou sem permissão. Contate o responsável.'
        if status == 409:
            return f'Conflito de arquivo: {name}. Contate o responsável; o original foi preservado.'
        return f'Falha de comunicação: {name} ({type(exc).__name__})'

    def _accept_receipt(self, result, path, sha, name, snapshot):
        state = result.get('status')
        receipt = result.get('receipt_id')
        if (state not in ('pending', 'delivered', 'conflict', 'uncertain')
                or not isinstance(receipt, str) or not receipt
                or result.get('sha256') != sha or result.get('name') != name):
            raise ValueError('Resposta de entrega incompatível')
        local = {'pending': 'awaiting', 'delivered': 'sent'}.get(state, state)
        message = {'conflict': f'Conflito de entrega: {name}. Contate o responsável.',
                   'uncertain': f'Entrega incerta: {name}. Aguarde verificação do responsável.'}.get(state)
        self.db.execute('UPDATE queue SET state=?,receipt_id=?,error=?,attempts=0,next_retry=0 WHERE path=? AND sha=?',
                        (local, receipt, message, path, sha))
        self.db.commit()
        if local == 'sent':
            Path(snapshot).unlink(missing_ok=True)
        elif message:
            self.status.error = message

    def _upload(self, config):
        token = unprotect_token(config.protected_token)
        with http_client(config.server_url, config.ca_file, self.transport) as client:
            rows = self.db.execute("SELECT path,sha,name,snapshot,attempts,state,receipt_id FROM queue WHERE state IN ('pending','awaiting') AND next_retry<=? ORDER BY next_retry,rowid LIMIT 10", (self.clock(),)).fetchall()
            for path, sha, name, snapshot, attempts, state, receipt in rows:
                try:
                    if self.stop_event.is_set():
                        break
                    if state == 'awaiting':
                        r = client.get('/api/files/status/' + quote(receipt, safe=''),
                                       headers={'Authorization': f'Bearer {token}'})
                        r.raise_for_status()
                        result = r.json()
                        if r.status_code != 200 or result.get('receipt_id') != receipt:
                            raise ValueError('Recibo divergente')
                        self._accept_receipt(result, path, sha, name, snapshot)
                        self.db.execute('UPDATE queue SET next_retry=? WHERE path=? AND sha=?',
                                        (self.clock() + config.poll_seconds, path, sha))
                        self.db.commit()
                        continue
                    with open(snapshot, 'rb') as body:
                        if hashlib.file_digest(body, 'sha256').hexdigest() != sha:
                            raise ValueError('Snapshot corrompido')
                        body.seek(0)
                        r = client.put('/api/files/' + quote(name, safe=''), content=body,
                            headers={'Authorization': f'Bearer {token}', 'X-Content-SHA256': sha,
                                     'Content-Type': 'application/octet-stream'})
                    r.raise_for_status()
                    result = r.json()
                    if config.delivery_mode == 'direct':
                        if r.status_code != 200 or result.get('sha256') != sha:
                            raise ValueError('Resposta direct incompatível')
                        self.db.execute("UPDATE queue SET state='sent',error=NULL WHERE path=? AND sha=?", (path, sha))
                        self.db.commit()
                        Path(snapshot).unlink(missing_ok=True)
                    else:
                        if r.status_code != 202:
                            raise ValueError('Servidor não confirmou protocolo de fila')
                        self._accept_receipt(result, path, sha, name, snapshot)
                except Exception as exc:
                    error = self._safe_error(exc, name)
                    self.status.error = error
                    delay = min(3600, 2 ** min(attempts + 1, 12))
                    # A conflict is terminal for automatic uploads, but preserve spool.
                    if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 409:
                        self.db.execute("UPDATE queue SET state='conflict' WHERE path=? AND sha=?", (path, sha))
                    self.db.execute('UPDATE queue SET attempts=?,next_retry=?,error=? WHERE path=? AND sha=?',
                        (attempts + 1, self.clock() + delay, error, path, sha))
                    self.db.commit()

    def heartbeat(self):
        try:
            config = read_config(self.data_dir)
            if not (config.server_url and config.protected_token) or not self._authorized_config(config):
                return
            token = unprotect_token(config.protected_token)
            # Own HTTP connection, no queue lock: uploads cannot starve heartbeat.
            with http_client(config.server_url, config.ca_file, self.transport) as client:
                r = client.post('/api/heartbeat', headers={'Authorization': f'Bearer {token}'},
                    json={'pending': self.status.pending, 'error': self.status.error})
                r.raise_for_status()
            self.status.last_heartbeat = self.clock()
        except Exception as exc:
            self.status.error = self._safe_error(exc, 'conexão com o servidor')

    def run_once(self, *, send_heartbeat=True):
        with self.lock:
            self.status.error = None
            try:
                config = read_config(self.data_dir)
                self.status.paused = config.paused
                self.status.configured = bool(config.server_url and config.source_dir and config.protected_token
                                              and self._authorized_config(config))
                if self.status.configured and not config.paused:
                    self._scan(config)
                    self._counts()
                    self._upload(config)
            except Exception as exc:
                self.status.error = f'Agente indisponível ({type(exc).__name__})'
            self._counts()
        if send_heartbeat:
            self.heartbeat()
        return self.status

    def run(self):
        def pulses():
            while not self.stop_event.is_set():
                self.heartbeat()
                self.stop_event.wait(HEARTBEAT_SECONDS)
        pulse = threading.Thread(target=pulses, daemon=True, name='heartbeat')
        pulse.start()
        updates = None
        if self.auto_update:
            from updates.runtime import ClientUpdates
            updates = ClientUpdates(self)
            updates.start()
        try:
            while not self.stop_event.is_set():
                self.run_once(send_heartbeat=False)
                if updates:
                    try:
                        updates.apply_between_cycles()
                    except Exception:
                        # Update/config/cache failures must never terminate transfers.
                        pass
                try:
                    delay = read_config(self.data_dir).poll_seconds
                except Exception:
                    delay = 5
                self.stop_event.wait(delay)
        finally:
            self.stop_event.set()
            pulse.join(timeout=12)
            if updates:
                updates.close()

    def close(self):
        self.stop_event.set()
        with self.lock:
            self.db.close()
