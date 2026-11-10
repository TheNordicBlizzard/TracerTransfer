"""Real queued integration, Windows DPAPI, subprocess agents and trusted test TLS.

Run from the repository: uv run python -m tests.integration_queued
Optional --client-exe PATH --delivery-exe PATH replace only the corresponding
runtime. Enrollment always uses the production ActivationService/activate APIs.
No service, registry, production folder or human account is changed.
"""
import argparse
from collections import Counter
from contextlib import ExitStack, closing
import hashlib
import json
import os
from pathlib import Path
import secrets
import sqlite3
import subprocess
import sys
import tempfile
import time
import uuid

from client.activation import ActivationService
from client.auth import http_client
from client.state import Config, save_config, read_config, unprotect_token
from delivery.agent import activate
from delivery.state import unprotect_token as delivery_unprotect
from server.database import Database
from server.users import bootstrap_admin
from tests.support import SCRATCH, free_port, stop_process, tls_proxy, wait_ready

ROOT = Path(__file__).resolve().parents[1]


class IntegrationFailure(RuntimeError):
    """Only fixed, non-secret diagnostics may leave the harness."""


def require(condition, label):
    if not condition:
        raise IntegrationFailure(label)


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def rows(path, sql):
    if not path.exists():
        return []
    with closing(sqlite3.connect(path, timeout=5)) as connection:
        connection.row_factory = sqlite3.Row
        return [dict(row) for row in connection.execute(sql)]


class Runtime:
    def __init__(self, module, executable, private_env, secrets_in_memory):
        self.module = module
        self.executable = Path(executable).resolve() if executable else None
        self.env = private_env
        self.secrets = secrets_in_memory
        if self.executable:
            require(self.executable.is_file(), module + '_executable_missing')
        self.sha256 = digest(self.executable) if self.executable else None
        self.calls = 0

    def unchanged(self):
        if self.executable:
            require(self.executable.is_file() and digest(self.executable) == self.sha256,
                    self.module + '_executable_changed_during_test_no_retry')

    def once(self, *arguments):
        self.unchanged()
        command = [str(self.executable)] if self.executable else [sys.executable, '-m', self.module]
        process = subprocess.Popen(command + list(arguments), cwd=ROOT, env=self.env,
                                   stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE)
        try:
            out, err = process.communicate(timeout=45)
        except subprocess.TimeoutExpired:
            stop_process(process)
            process.communicate(timeout=10)
            raise IntegrationFailure(self.module + '_cycle_timeout_no_retry') from None
        self.calls += 1
        self.unchanged()
        for secret in self.secrets:
            require(secret.encode() not in out + err, self.module + '_secret_in_process_output')
        require(process.returncode == 0, self.module + '_cycle_exit_nonzero_no_retry')

    def metadata(self):
        return {'origin': 'exe' if self.executable else 'python-source',
                'filename': self.executable.name if self.executable else self.module,
                'sha256': self.sha256, 'cycles': self.calls}


def run(client_exe=None, delivery_exe=None):
    require(os.name == 'nt', 'real_dpapi_requires_windows')
    phase = 'setup'
    started = time.monotonic()
    secret_values = []
    try:
        with ExitStack() as stack:
            temporary = stack.enter_context(tempfile.TemporaryDirectory(prefix='queued-integration-', dir=SCRATCH))
            root = Path(temporary)
            paths = {name: root / name for name in
                     ('backend', 'client', 'delivery', 'source', 'output', 'profile', 'tls')}
            for path in paths.values():
                path.mkdir()
            env = {**os.environ, 'LOCALAPPDATA': str(paths['profile']),
                   'APPDATA': str(paths['profile']), 'PROGRAMDATA': str(paths['profile']),
                   'TMPDIR': str(root), 'TEMP': str(root), 'TMP': str(root),
                   'TRACER_TEST_TMP': str(root), 'PYTHONUTF8': '1'}
            sender = Runtime('client', client_exe, env, secret_values)
            receiver = Runtime('delivery', delivery_exe, env, secret_values)
            password = secrets.token_urlsafe(40)
            secret_values.append(password)
            bootstrap_admin(Database(paths['backend']), 'queued_test_admin', password)
            port = free_port()
            backend = subprocess.Popen(
                [sys.executable, '-m', 'server', '--headless', '--host', '127.0.0.1',
                 '--port', str(port), '--data-dir', str(paths['backend']), '--delivery-mode', 'queued'],
                cwd=ROOT, env=env, stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            stack.callback(lambda: stop_process(backend) if backend.poll() is None else None)
            wait_ready('http://127.0.0.1:' + str(port), backend)
            url, ca = stack.enter_context(tls_proxy(paths['tls'], port))
            # Exercise certificate DNS SAN, not an unverified HTTPS or a pinned IP shortcut.
            url = url.replace('127.0.0.1', 'localhost')
            http = stack.enter_context(http_client(url, str(ca)))

            def api(method, route, expected=200, **kwargs):
                response = http.request(method, route, **kwargs)
                require(response.status_code == expected, 'api_status_' + str(expected) + '_' + route.split('/')[2])
                return response.json()

            phase = 'enrollment'
            login = api('POST', '/api/login', json={'username': 'queued_test_admin', 'password': password})
            secret_values.append(login['token'])
            admin = {'Authorization': 'Bearer ' + login['token']}
            backlog = paths['source'] / 'backlog.oma'
            backlog.write_bytes(b'PRE-ACTIVATION BACKLOG - NEVER SEND\r\n')
            save_config(paths['client'], Config(source_dir=str(paths['source']), poll_seconds=0.1, stable_seconds=0))

            def code(role):
                result = api('POST', '/api/activations', 201, headers=admin,
                             json={'name': 'Queued integration ' + role, 'role': role, 'expires_hours': 1})
                secret_values.append(result['code'])
                return result['code']

            sender_code = code('sender')
            cfg = ActivationService(paths['client'], server_url=url, ca_file=str(ca)).activate(sender_code, 'Integration sender')
            require(cfg.activated and cfg.delivery_mode == 'queued', 'sender_not_activated')
            secret_values.append(unprotect_token(cfg.protected_token))
            delivery_code = code('delivery')
            identity = activate(paths['delivery'], {'code': delivery_code, 'name': 'Integration delivery',
                                                  'server_url': url, 'ca_file': str(ca)})
            secret_values.append(delivery_unprotect(identity['protected_token']))
            require(identity['role'] == 'delivery', 'delivery_not_activated')
            # Both codes must reject an unrelated installation; no code is printed/persisted.
            for used_code in (sender_code, delivery_code):
                api('POST', '/api/activate', 401, json={'code': used_code,
                    'installation_id': uuid.uuid4().hex, 'installation_secret': secrets.token_urlsafe(32),
                    'name': 'Rejected code reuse'})
            activation_rows = api('GET', '/api/activations', headers=admin)
            require(len(activation_rows) == 2 and all(r['used_at'] and r['device_id'] for r in activation_rows),
                    'activation_readback_failed')

            queue_db = paths['client'] / 'queue.sqlite3'
            backend_db = paths['backend'] / 'tracer.sqlite3'

            def queue():
                return rows(queue_db, 'SELECT name,sha,state,receipt_id,snapshot,error FROM queue ORDER BY name')

            def receipts():
                return rows(backend_db, 'SELECT id,name,sha256,size,status,error FROM deliveries ORDER BY name')

            def client_once():
                sender.once('--data-dir', str(paths['client']), '--headless', '--once')

            def delivery_once():
                receiver.once('--data-dir', str(paths['delivery']), '--output-dir', str(paths['output']),
                              '--poll-seconds', '0.1', '--once')
                status = json.loads((paths['delivery'] / 'status.json').read_text(encoding='utf-8'))
                require(status['status'] == 'poll_ok' and status['pending_ack'] == 0, 'delivery_status_not_ok')

            def until(predicate, label, timeout=75):
                deadline = time.monotonic() + timeout
                while time.monotonic() < deadline:
                    client_once()
                    if predicate():
                        return
                    time.sleep(0.15)
                raise IntegrationFailure(label)

            phase = 'backlog'
            client_once()
            time.sleep(0.15)
            client_once()
            require(queue() == [] and receipts() == [], 'backlog_was_queued')
            require(list((paths['client'] / 'spool').iterdir()) == [], 'backlog_snapshot_created')

            phase = 'queued_before_ack'
            payloads = {'new-order.oma': b'OMA queued integration\r\n' + secrets.token_bytes(512),
                        'new-order.vca': b'VCA queued integration\r\n' + secrets.token_bytes(333)}
            expected = {}
            for name, payload in payloads.items():
                (paths['source'] / name).write_bytes(payload)
                expected[name] = hashlib.sha256(payload).hexdigest()
            (paths['source'] / 'ignored.txt').write_bytes(b'extension filter')
            until(lambda: len(queue()) == 2 and all(r['state'] == 'awaiting' for r in queue()), 'awaiting_timeout')
            before_ack = queue()
            require(len(receipts()) == 2 and all(r['status'] == 'pending' for r in receipts()), 'not_real_pending_receipts')
            require(all(r['receipt_id'] and Path(r['snapshot']).is_file() for r in before_ack), 'awaiting_snapshot_missing')
            require(not list(paths['output'].iterdir()), 'output_before_delivery')
            client_once()
            require(all(r['state'] == 'awaiting' for r in queue()), 'sent_before_ack')
            require(all(r['sha'] == expected[r['name']] for r in queue()), 'snapshot_hash_mismatch')

            phase = 'delivery_ack'
            for _ in payloads:
                delivery_once()
            require(all(r['status'] == 'delivered' for r in receipts()), 'delivery_ack_missing')
            until(lambda: len(queue()) == 2 and all(r['state'] == 'sent' for r in queue()), 'sent_timeout')
            require(not list((paths['client'] / 'spool').iterdir()), 'sent_spool_not_cleaned')
            for name, sha in expected.items():
                require(digest(paths['output'] / name) == sha == digest(paths['source'] / name), 'published_hash_mismatch')
            receipt_identity = receipts()
            output_stat = {name: (paths['output'] / name).stat().st_mtime_ns for name in payloads}
            client_once()
            delivery_once()
            require(receipts() == receipt_identity, 'idempotency_changed_receipts')
            require(output_stat == {name: (paths['output'] / name).stat().st_mtime_ns for name in payloads},
                    'idempotency_republished_output')

            phase = 'conflict'
            collision = 'collision.oma'
            (paths['output'] / collision).write_bytes(b'EXISTING DESTINATION - PRESERVE')
            preserved_hash = digest(paths['output'] / collision)
            (paths['source'] / collision).write_bytes(b'NEW DIFFERENT CONTENT - MUST CONFLICT')
            until(lambda: any(r['name'] == collision and r['state'] == 'awaiting' for r in queue()), 'conflict_awaiting_timeout')
            delivery_once()
            until(lambda: any(r['name'] == collision and r['state'] == 'conflict' for r in queue()), 'terminal_conflict_timeout')
            conflict_record = next(r for r in queue() if r['name'] == collision)
            require(Path(conflict_record['snapshot']).is_file() and conflict_record['error'], 'conflict_evidence_missing')
            conflict_receipts = receipts()
            require(next(r for r in conflict_receipts if r['name'] == collision)['status'] == 'conflict', 'backend_conflict_missing')
            client_once()
            delivery_once()
            require(receipts() == conflict_receipts and digest(paths['output'] / collision) == preserved_hash,
                    'conflict_not_terminal_or_destination_overwritten')

            phase = 'revocation'
            api('DELETE', '/api/devices/' + cfg.device_id, headers=admin)
            device_rows = rows(backend_db, 'SELECT id,enabled FROM devices')
            require(any(r['id'] == cfg.device_id and r['enabled'] == 0 for r in device_rows), 'revocation_readback_failed')
            blocked_name = 'after-revocation.vca'
            (paths['source'] / blocked_name).write_bytes(b'REVOKED SENDER MUST NOT ENQUEUE ON BACKEND')
            until(lambda: any(r['name'] == blocked_name and r['state'] == 'pending' and r['error']
                              and 'revogada' in r['error'] for r in queue()), 'revocation_not_observed')
            # Recheck authorization on actual upload, independent of heartbeat status/exit zero.
            token = unprotect_token(read_config(paths['client']).protected_token)
            blocked_payload = (paths['source'] / blocked_name).read_bytes()
            api('PUT', '/api/files/' + blocked_name, 401, content=blocked_payload,
                headers={'Authorization': 'Bearer ' + token, 'X-Content-SHA256': hashlib.sha256(blocked_payload).hexdigest(),
                         'Content-Type': 'application/octet-stream'})
            delivery_once()
            require(receipts() == conflict_receipts and not (paths['output'] / blocked_name).exists(), 'revoked_upload_accepted')
            require(backlog.read_bytes() == b'PRE-ACTIVATION BACKLOG - NEVER SEND\r\n', 'backlog_original_changed')
            for name, payload in payloads.items():
                require((paths['source'] / name).read_bytes() == payload, 'source_changed')
            phase = 'secret_audit'
            # Release the backend's Windows exclusive instance lock before auditing it.
            stop_process(backend)
            require(backend.poll() is not None, 'backend_not_stopped')
            # Audit all harness-owned persisted files including SQLite/WAL and DPAPI profiles.
            for path in root.rglob('*'):
                if path.is_file():
                    try:
                        data = path.read_bytes()
                    except OSError as exc:
                        raise IntegrationFailure('audit_read_failed_' + path.name + '_' + str(exc.winerror)) from None
                    for value in secret_values:
                        require(value.encode() not in data, 'plaintext_secret_persisted')
            final_queue = queue()
            final_receipts = receipts()
            sender.unchanged()
            receiver.unchanged()
            result = {'ok': True, 'backend': 'python-subprocess-real-server-queued',
                      'tls': {'verified': True, 'hostname': 'localhost', 'explicit_test_ca': True},
                      'activation': {'single_use_rejections': 2, 'dpapi_current_user': True},
                      'client': sender.metadata(), 'delivery': receiver.metadata(),
                      'proofs': {'backlog_ignored': True, 'awaiting_before_ack': 2,
                                 'published_hashes': expected, 'idempotent': True,
                                 'collision_preserved_sha256': preserved_hash, 'terminal_conflict': True,
                                 'revoked_upload_http_status': 401, 'plaintext_secret_audit': True},
                      'counts': {'queue': dict(Counter(r['state'] for r in final_queue)),
                                 'receipts': dict(Counter(r['status'] for r in final_receipts)),
                                 'output_files': len(list(paths['output'].iterdir()))},
                      'seconds': round(time.monotonic() - started, 2)}
        # TemporaryDirectory has removed only this harness's private scratch tree.
        result['scratch_cleaned'] = not root.exists()
        require(result['scratch_cleaned'], 'scratch_cleanup_failed')
        return result
    except IntegrationFailure:
        raise
    except Exception as exc:
        # Never stringify arbitrary HTTP/DPAPI/process errors with credentials.
        raise IntegrationFailure('unexpected_failure_in_' + phase + '_' + type(exc).__name__
                                 + '_winerror_' + str(getattr(exc, 'winerror', None))) from None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--client-exe', type=Path)
    parser.add_argument('--delivery-exe', type=Path)
    args = parser.parse_args(argv)
    try:
        result = run(args.client_exe, args.delivery_exe)
    except IntegrationFailure as exc:
        print(json.dumps({'ok': False, 'error': str(exc)}, sort_keys=True))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
