"""Non-blocking update discovery; application occurs only between queue cycles."""
import json
import os
from pathlib import Path
import sys
import threading

from updates.windows import atomic_state, client_paths, launch_client, launch_delivery, real_path

CHECK_SECONDS = 6 * 60 * 60
RETRY_SECONDS = 15 * 60


def installed_client(data_dir, *, once=False):
    if once or os.name != 'nt' or not getattr(sys, 'frozen', False):
        return False
    try:
        install, data = client_paths()
        return (real_path(data_dir) == real_path(data)
                and real_path(sys.executable) == real_path(install / 'TracerCliente.exe')
                and real_path(install / 'TracerAtualizador.exe').is_file())
    except (OSError, ValueError, KeyError):
        return False


def activated_profile(config):
    from client.state import COMPANY_SERVER
    return (config.activated and config.delivery_mode == 'queued'
            and config.server_url == COMPANY_SERVER and config.ca_file is None)


class ClientUpdates:
    def __init__(self, agent):
        self.agent = agent
        self.pending = None
        self.guard = threading.Lock()
        self.thread = threading.Thread(target=self._discover, name='update-discovery', daemon=True)
        self.cache = Path(agent.data_dir) / 'updates'

    def start(self):
        self.thread.start()

    def _record(self, state, **fields):
        self.cache.mkdir(exist_ok=True)
        atomic_state(self.cache / 'status.json', {'state': state, **fields})

    def _discover(self):
        # Deliberately separate from the heartbeat and transfer locks.
        from client.state import read_config
        from updates import VERSION
        from updates.core import prepare_update, UpdateUnavailable, UpdateRejected
        failures = 0
        while not self.agent.stop_event.is_set():
            delay = CHECK_SECONDS
            try:
                config = read_config(self.agent.data_dir)
                if not activated_profile(config):
                    delay = 60
                else:
                    with self.guard:
                        pending = self.pending is not None
                    if not pending:
                        prepared = prepare_update('client', VERSION, self.cache / 'downloads')
                        with self.guard:
                            self.pending = prepared
                        self._record('prepared' if prepared else 'current', version=prepared.version if prepared else VERSION)
                        failures = 0
            except UpdateUnavailable:
                failures += 1
                delay = min(CHECK_SECONDS, RETRY_SECONDS * 2 ** min(failures - 1, 5))
                self._safe_record('unavailable')  # private repo/404 does not stop transfers
            except UpdateRejected:
                self._safe_record('rejected')
            except Exception:
                failures += 1
                delay = min(CHECK_SECONDS, RETRY_SECONDS * 2 ** min(failures - 1, 5))
                self._safe_record('failed')
            self.agent.stop_event.wait(delay)

    def _safe_record(self, state):
        try:
            self._record(state)
        except OSError:
            pass  # Disk/network update problems must not stop the sender.

    def apply_between_cycles(self):
        from client.state import read_config
        with self.agent.lock:
            if self.agent.stop_event.is_set():
                return
            config = read_config(self.agent.data_dir)
            if not activated_profile(config):
                return
            with self.guard:
                prepared, self.pending = self.pending, None
            if prepared is None:
                return
            try:
                launch_client(prepared, self.agent.data_dir)
            except Exception:
                self._safe_record('apply_failed')
                return
            self.agent.update_requested.set()
            self.agent.stop_event.set()

    def close(self):
        self.thread.join(timeout=1)  # HTTP worker is daemon; never delay clean GUI exit.


def delivery_paths(data_dir=None):
    if data_dir is None:
        base = Path(os.environ.get('PROGRAMDATA', 'C:/ProgramData')) / 'TracerTransfer-Delivery'
        data = base / 'data'
    else:
        data = Path(data_dir)
        base = data.parent
    return base, data


def installed_delivery(data_dir, *, once=False):
    if once or os.name != 'nt' or not getattr(sys, 'frozen', False):
        return False
    try:
        base, data = delivery_paths(data_dir)
        return (real_path(data_dir) == real_path(data)
                and real_path(sys.executable) == real_path(base / 'bin' / 'TracerEntregador.exe')
                and real_path(base / 'updater' / 'TracerAtualizador.exe').is_file())
    except (OSError, ValueError, KeyError):
        return False


def activated_delivery_profile(data_dir):
    try:
        path = Path(data_dir) / 'identity.json'
        if not path.is_file():
            return False
        identity = json.loads(path.read_text(encoding='utf-8'))
        return identity.get('role') == 'delivery' and bool(identity.get('server_url'))
    except Exception:
        return False


class DeliveryUpdates:
    def __init__(self, agent):
        self.agent = agent
        self.pending = None
        self.guard = threading.Lock()
        self.thread = threading.Thread(target=self._discover, name='delivery-update-discovery', daemon=True)
        self.cache = Path(agent.data_dir) / 'updates'
        self.base_dir = Path(agent.data_dir).parent

    def start(self):
        self.thread.start()

    def _record(self, state, **fields):
        self.cache.mkdir(exist_ok=True)
        atomic_state(self.cache / 'status.json', {'state': state, **fields})

    def _current_version(self):
        from updates import VERSION
        version_file = self.base_dir / 'updater' / 'installed_version'
        if version_file.is_file():
            try:
                v = version_file.read_text(encoding='ascii').strip()
                if v:
                    return v
            except OSError:
                pass
        return VERSION

    def _discover(self):
        from updates.core import prepare_update, UpdateUnavailable, UpdateRejected
        failures = 0
        while not self.agent.stop_event.is_set():
            delay = CHECK_SECONDS
            try:
                if not activated_delivery_profile(self.agent.data_dir):
                    delay = 60
                else:
                    with self.guard:
                        pending = self.pending is not None
                    if not pending:
                        version = self._current_version()
                        prepared = prepare_update('delivery', version, self.cache / 'downloads')
                        with self.guard:
                            self.pending = prepared
                        self._record('prepared' if prepared else 'current', version=prepared.version if prepared else version)
                        failures = 0
            except UpdateUnavailable:
                failures += 1
                delay = min(CHECK_SECONDS, RETRY_SECONDS * 2 ** min(failures - 1, 5))
                self._safe_record('unavailable')
            except UpdateRejected:
                self._safe_record('rejected')
            except Exception:
                failures += 1
                delay = min(CHECK_SECONDS, RETRY_SECONDS * 2 ** min(failures - 1, 5))
                self._safe_record('failed')
            self.agent.stop_event.wait(delay)

    def _safe_record(self, state):
        try:
            self._record(state)
        except OSError:
            pass

    def apply_between_cycles(self):
        with self.agent.lock:
            if self.agent.stop_event.is_set():
                return
            if not activated_delivery_profile(self.agent.data_dir):
                return
            with self.guard:
                prepared, self.pending = self.pending, None
            if prepared is None:
                return
            try:
                launch_delivery(prepared, self.base_dir)
            except Exception:
                self._safe_record('apply_failed')
                return
            self.agent.update_requested.set()

    def close(self):
        self.thread.join(timeout=1)
