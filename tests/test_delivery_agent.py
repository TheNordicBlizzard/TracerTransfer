"""Isolated delivery journal/protocol tests; never installs or touches real services."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess

import httpx
import pytest

from delivery.agent import DeliveryAgent, activate, http_client, validate_filename
from delivery.state import State, atomic_json, protect_token, unprotect_token


class FakeBackend:
    def __init__(self, body=b'delivery-test', name='job.oma'):
        self.body = body
        self.item = {'id': 'a' * 32, 'name': name, 'sha256': hashlib.sha256(body).hexdigest(),
                     'size': len(body), 'lease_token': 'lease-secret',
                     'lease_until': (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()}
        self.offer = True
        self.downloads, self.acks, self.calls = 0, [], []
        self.ack_status = 200
        self.drop_ack = False
        self.download_status = 200

    def handle(self, request):
        self.calls.append((request.method, request.url.path))
        assert request.url.scheme == 'https'
        assert request.headers['Authorization'] == 'Bearer test-device-secret'
        if request.url.path == '/api/delivery/lease':
            assert json.loads(request.content) == {'limit': 1}
            items = [self.item] if self.offer else []
            self.offer = False
            return httpx.Response(200, json={'items': items})
        if request.url.path.startswith('/api/delivery/files/'):
            self.downloads += 1
            assert request.headers['X-Delivery-Lease'] == self.item['lease_token']
            return httpx.Response(self.download_status, content=self.body)
        if request.url.path.startswith('/api/delivery/ack/'):
            payload = json.loads(request.content)
            self.acks.append(payload)
            if self.drop_ack:
                self.drop_ack = False
                raise httpx.ReadError('lost ack response', request=request)
            return httpx.Response(self.ack_status, json={'ok': True})
        raise AssertionError('Delivery leaked into another role: ' + request.url.path)


@pytest.fixture
def environment(tmp_path):
    data, output = tmp_path / 'data', tmp_path / 'output'
    data.mkdir(); output.mkdir()
    atomic_json(data / 'identity.json', {'role': 'delivery', 'server_url': 'https://delivery.invalid',
                'protected_token': protect_token('test-device-secret')})
    return data, output


def make_agent(environment, backend):
    return DeliveryAgent(*environment, transport=httpx.MockTransport(backend.handle))


def test_download_hash_publish_ack_and_role_isolation(environment):
    data, output = environment
    backend = FakeBackend()
    agent = make_agent(environment, backend)
    try:
        assert agent.run_once() == 1
        assert (output / 'job.oma').read_bytes() == backend.body
        assert agent.state.get(backend.item['id'])['acked'] == 1
        assert backend.acks[-1]['result'] == 'delivered'
        assert backend.acks[-1]['sha256'] == hashlib.sha256(backend.body).hexdigest()
        assert not list(agent.staging.iterdir())
        assert set(path for _, path in backend.calls) == {'/api/delivery/lease', '/api/delivery/files/' + backend.item['id'], '/api/delivery/ack/' + backend.item['id']}
        assert json.loads((data / 'status.json').read_text())['status'] == 'poll_ok'
    finally:
        agent.close()


def test_lost_ack_consumed_file_is_never_republished_after_restart(environment):
    _, output = environment
    backend = FakeBackend(); backend.drop_ack = True
    agent = make_agent(environment, backend)
    with pytest.raises(httpx.ReadError):
        agent.run_once()
    assert agent.state.get(backend.item['id'])['phase'] == 'terminal'
    (output / 'job.oma').unlink()  # FreeForm has consumed the file.
    agent.close()
    agent = make_agent(environment, backend)
    try:
        agent.run_once()
        assert not list(output.iterdir())
        assert backend.downloads == 1
        assert len(backend.acks) == 2
        assert agent.state.get(backend.item['id'])['acked'] == 1
    finally:
        agent.close()


def test_expired_ack_refreshed_lease_only_acks_terminal_journal(environment):
    _, output = environment
    backend = FakeBackend(); backend.ack_status = 409
    agent = make_agent(environment, backend)
    try:
        agent.run_once()
        (output / 'job.oma').unlink()
        backend.item['lease_token'] = 'new-lease'
        backend.offer = True
        backend.ack_status = 200
        agent.run_once()
        assert not list(output.iterdir())
        assert backend.downloads == 1
        assert backend.acks[-1]['lease_token'] == 'new-lease'
        assert backend.acks[-1]['result'] == 'delivered'
    finally:
        agent.close()


@pytest.mark.parametrize('same', [True, False])
def test_case_insensitive_existing_file_duplicate_or_conflict(environment, same):
    _, output = environment
    backend = FakeBackend()
    original = backend.body if same else b'original-industrial-file'
    (output / 'JOB.OMA').write_bytes(original)
    agent = make_agent(environment, backend)
    try:
        agent.run_once()
        assert (output / 'JOB.OMA').read_bytes() == original
        assert len(list(output.iterdir())) == 1
        assert backend.downloads == 0
        assert backend.acks[-1]['result'] == ('delivered' if same else 'conflict')
    finally:
        agent.close()


@pytest.mark.parametrize('present,matching', [(False, False), (True, True), (True, False)])
def test_publishing_crash_recovery_never_recreates_missing_file(environment, present, matching):
    _, output = environment
    backend = FakeBackend(); backend.offer = False
    agent = make_agent(environment, backend)
    agent.state.remember(backend.item)
    agent.state.update(backend.item['id'], phase='publishing')
    if present:
        (output / 'job.oma').write_bytes(backend.body if matching else b'other-file')
    agent.close()
    agent = make_agent(environment, backend)
    try:
        agent.run_once()
        assert backend.downloads == 0
        assert backend.acks[-1]['result'] == ('delivered' if matching else 'uncertain')
        if not present:
            assert not list(output.iterdir())
        elif not matching:
            assert (output / 'job.oma').read_bytes() == b'other-file'
    finally:
        agent.close()


def test_journal_is_publishing_before_atomic_link(environment, monkeypatch):
    backend = FakeBackend()
    agent = make_agent(environment, backend)
    link = os.link
    observed = []
    def checked_link(src, dest):
        observed.append(agent.state.get(backend.item['id'])['phase'])
        link(src, dest)
    monkeypatch.setattr('delivery.agent.os.link', checked_link)
    try:
        agent.run_once()
        assert observed == ['publishing']
    finally:
        agent.close()


def test_crash_between_link_and_terminal_commit_consumed_file_uncertain(environment, monkeypatch):
    _, output = environment
    backend = FakeBackend()
    agent = make_agent(environment, backend)
    class Crash(BaseException):
        pass
    original_terminal = agent.terminal
    def crash(*args, **kwargs):
        raise Crash()
    monkeypatch.setattr(agent, 'terminal', crash)
    with pytest.raises(Crash):
        agent.run_once()
    assert (output / 'job.oma').read_bytes() == backend.body
    (output / 'job.oma').unlink()
    monkeypatch.setattr(agent, 'terminal', original_terminal)
    try:
        agent.run_once()
        assert backend.acks[-1]['result'] == 'uncertain'
        assert backend.downloads == 1
        assert not list(output.iterdir())
    finally:
        agent.close()


@pytest.mark.parametrize('failure', ['hash', 'short', 'long'])
def test_integrity_failure_reports_uncertain_no_publication(environment, failure):
    _, output = environment
    backend = FakeBackend()
    if failure == 'hash':
        backend.item['sha256'] = '0' * 64
    elif failure == 'short':
        backend.body = b'x'
    else:
        backend.body += b'x'
    agent = make_agent(environment, backend)
    try:
        agent.run_once()
        assert backend.acks[-1]['result'] == 'uncertain'
        assert not list(output.iterdir())
        assert not list(agent.staging.iterdir())
    finally:
        agent.close()


def test_expired_lease_is_not_downloaded(environment):
    backend = FakeBackend()
    backend.item['lease_until'] = '2000-01-01T00:00:00+00:00'
    agent = make_agent(environment, backend)
    try:
        with pytest.raises(ValueError, match='lease_expired'):
            agent.run_once()
        assert backend.downloads == 0
    finally:
        agent.close()


def test_download_http_failure_retry_preserves_originals(environment):
    _, output = environment
    backend = FakeBackend(); backend.download_status = 503
    agent = make_agent(environment, backend)
    try:
        with pytest.raises(httpx.HTTPStatusError):
            agent.run_once()
        assert not list(output.iterdir())
        assert not list(agent.staging.iterdir())
        backend.offer = True; backend.download_status = 200
        agent.run_once()
        assert backend.acks[-1]['result'] == 'delivered'
    finally:
        agent.close()


def test_publication_failure_reports_uncertain_and_preserves_files(environment, monkeypatch):
    _, output = environment
    backend = FakeBackend()
    agent = make_agent(environment, backend)
    def denied(*args, **kwargs):
        raise PermissionError('disk failure')
    monkeypatch.setattr('delivery.agent.os.link', denied)
    with pytest.raises(PermissionError):
        agent.run_once()
    try:
        agent.run_once()
        assert backend.acks[-1]['result'] == 'uncertain'
        assert not list(output.iterdir())
        assert not list(agent.staging.iterdir())
    finally:
        agent.close()


@pytest.mark.parametrize('name', ['../bad.oma','CON.oma','job.txt','job.oma.','c:job.oma','bad/inner.oma','job.oma '])
def test_invalid_filename_never_published(environment, name):
    backend = FakeBackend(name=name)
    agent = make_agent(environment, backend)
    try:
        with pytest.raises(ValueError):
            validate_filename(name)
        agent.run_once()
        assert backend.acks[-1]['result'] == 'uncertain'
        assert backend.acks[-1]['error'] == 'invalid_name'
        assert backend.downloads == 0
    finally:
        agent.close()


def test_dpapi_persists_no_plaintext_and_same_user_roundtrip():
    protected = protect_token('private-test-secret')
    assert 'private-test-secret' not in protected
    assert unprotect_token(protected) == 'private-test-secret'


def test_activation_secret_persisted_before_post_and_reused_after_lost_response(tmp_path):
    requests = []
    bootstrap = {'code': 'TT-' + 'A' * 40, 'server_url': 'https://delivery.invalid', 'name': 'test'}
    def handler(request):
        body = json.loads(request.content)
        stored = json.loads((tmp_path / 'identity.json').read_text())
        assert unprotect_token(stored['protected_token']) == body['installation_secret']
        assert stored['installation_id'] == body['installation_id']
        assert 'role' not in body
        requests.append(body)
        if len(requests) == 1:
            raise httpx.ReadError('lost activation', request=request)
        return httpx.Response(200, json={'role': 'delivery','device_id':'d','activation_id':'a'})
    transport = httpx.MockTransport(handler)
    with pytest.raises(httpx.ReadError):
        activate(tmp_path, bootstrap, transport=transport)
    identity = activate(tmp_path, bootstrap, transport=transport)
    assert requests[0] == requests[1]
    assert identity['role'] == 'delivery'
    assert requests[0]['installation_secret'] not in (tmp_path / 'identity.json').read_text()


def test_sender_activation_is_rejected_and_never_usable(tmp_path):
    response = lambda req: httpx.Response(200, json={'role':'sender','device_id':'d','activation_id':'a'})
    with pytest.raises(ValueError, match='not a delivery'):
        activate(tmp_path, {'code':'TT-'+'A'*40}, transport=httpx.MockTransport(response))
    identity = json.loads((tmp_path / 'identity.json').read_text())
    assert identity['role'] is None
    output = tmp_path.parent / (tmp_path.name + '-output'); output.mkdir()
    with pytest.raises(ValueError, match='Delivery identity required'):
        DeliveryAgent(tmp_path, output)


@pytest.mark.parametrize('code', ['TT-'+'a'*40,' TT-'+'A'*40,'TT-'+'A'*39])
def test_activation_code_is_never_normalized(tmp_path, code):
    with pytest.raises(ValueError, match='format'):
        activate(tmp_path, {'code':code})
    assert not (tmp_path / 'identity.json').exists()


def test_singleton_lock(environment):
    first = State(environment[0])
    try:
        with pytest.raises((RuntimeError, OSError)):
            State(environment[0])
    finally:
        first.close()


@pytest.mark.parametrize('url', ['http://delivery.invalid','https://user:password@delivery.invalid','https://delivery.invalid/path','https://delivery.invalid?x=y'])
def test_https_origin_is_mandatory(url):
    with pytest.raises(ValueError):
        http_client(url)


def test_reject_untrusted_ca_path_without_tls_bypass(tmp_path):
    with pytest.raises(FileNotFoundError):
        http_client('https://delivery.invalid', str(tmp_path / 'missing-ca.pem'))


def test_staging_junction_is_rejected(environment, tmp_path):
    if os.name != 'nt':
        pytest.skip('Windows junction evidence')
    data, _ = environment
    target = tmp_path / 'junction-target'; target.mkdir()
    junction = data / 'staging'
    result = subprocess.run(['cmd.exe','/d','/c','mklink','/J',str(junction),str(target)], capture_output=True)
    assert result.returncode == 0, result.stderr
    try:
        with pytest.raises(ValueError, match='link/junction'):
            make_agent(environment, FakeBackend())
        assert not list(target.iterdir())
    finally:
        os.rmdir(junction)  # Only remove junction, never its target tree.


@pytest.mark.parametrize('expected', ['delivered', 'conflict', 'uncertain'])
def test_agent_integrates_with_real_queued_backend(tmp_path, expected):
    # Real application/router/auth/SQLite/spool, adapted in-process by MockTransport.
    # HTTPS handshake is independently exercised by smoke_delivery_agent.
    from fastapi.testclient import TestClient
    from server.app import create_app
    from server.users import bootstrap_admin
    from tests.test_activation import code, enroll
    app = create_app(tmp_path / 'backend', delivery_mode='queued')
    bootstrap_admin(app.state.db, 'admin', 'correct-horse-battery')
    data, output = tmp_path / 'agent', tmp_path / 'output'
    data.mkdir(); output.mkdir()
    payload = b'real-backend-integration-synthetic'
    with TestClient(app, base_url='https://delivery.invalid') as server:
        token = server.post('/api/login', json={'username':'admin','password':'correct-horse-battery'}).json()['token']
        admin = {'Authorization':'Bearer '+token}
        _, sender = enroll(server, admin)
        activation = code(server, admin, 'delivery')
        calls = []
        def forward(request):
            calls.append(request.url.path)
            response = server.request(request.method, request.url.path,
                                      headers=dict(request.headers), content=request.content)
            return httpx.Response(response.status_code, headers=response.headers, content=response.content)
        transport = httpx.MockTransport(forward)
        activate(data, {'code':activation['code'],'server_url':'https://delivery.invalid'}, transport=transport)
        receipt = server.put('/api/files/actual.oma', content=payload,
                             headers=sender | {'X-Content-SHA256':hashlib.sha256(payload).hexdigest()})
        assert receipt.status_code == 202, receipt.text
        receipt_id = receipt.json()['receipt_id']
        if expected == 'conflict':
            (output / 'ACTUAL.OMA').write_bytes(b'preserve-original')
        agent = DeliveryAgent(data, output, transport=transport)
        try:
            if expected == 'uncertain':
                # Durable publication intent with file consumed/missing after crash.
                offered = server.post('/api/delivery/lease', json={'limit':1},
                    headers={'Authorization':'Bearer '+unprotect_token(json.loads((data/'identity.json').read_text())['protected_token'])}).json()['items'][0]
                agent.state.remember(offered)
                agent.state.update(receipt_id, phase='publishing')
            agent.run_once()
            status = server.get('/api/files/status/'+receipt_id, headers=sender)
            assert status.json()['status'] == expected, status.text
            if expected == 'delivered':
                assert (output/'actual.oma').read_bytes() == payload
            elif expected == 'conflict':
                assert (output/'ACTUAL.OMA').read_bytes() == b'preserve-original'
            else:
                assert not list(output.iterdir())
            assert all(p == '/api/activate' or p.startswith('/api/delivery/') for p in calls)
        finally:
            agent.close()


@pytest.mark.parametrize('script_name', ['Install-Delivery.ps1','Update-Delivery.ps1','Uninstall-Delivery.ps1'])
def test_deployment_validate_only_rejects_untrusted_wrapper_without_changes(tmp_path, script_name):
    if os.name != 'nt':
        pytest.skip('Windows PowerShell validation')
    root = Path(__file__).resolve().parents[1]
    output = tmp_path / 'output'; output.mkdir()
    sentinel = output / 'original.oma'; sentinel.write_bytes(b'preserve-original')
    base = tmp_path / 'prepared'
    base.mkdir()
    wrapper = base / 'TracerTransferDelivery.exe'
    wrapper.write_bytes(b'UNTRUSTED TEST FIXTURE - NEVER EXECUTE')
    command = ['powershell.exe','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',
               str(root/'deploy/windows-delivery'/script_name),'-BaseDirectory',str(base),'-ValidateOnly']
    if script_name == 'Install-Delivery.ps1':
        command += ['-OutputDirectory',str(output),'-WrapperPath',str(wrapper),'-BinaryPath',str(wrapper),
                    '-ExpectedBinarySha256',hashlib.sha256(wrapper.read_bytes()).hexdigest()]
    before = set(tmp_path.rglob('*'))
    result = subprocess.run(command,capture_output=True,text=True,errors='replace',timeout=20)
    assert result.returncode != 0
    assert 'WinSW' in result.stdout + result.stderr
    assert set(tmp_path.rglob('*')) == before
    assert sentinel.read_bytes() == b'preserve-original'


def test_unreadable_existing_file_reports_conflict_without_download(environment, monkeypatch):
    _, output = environment
    backend = FakeBackend()
    existing = output / 'job.oma'
    existing.write_bytes(b'preserve unreadable original')
    original_open = Path.open

    def guarded_open(path, *args, **kwargs):
        if path == existing:
            raise PermissionError('synthetic exclusive access')
        return original_open(path, *args, **kwargs)

    agent = make_agent(environment, backend)
    try:
        with monkeypatch.context() as patch:
            patch.setattr(Path, 'open', guarded_open)
            agent.run_once()
        assert agent.state.get(backend.item['id'])['result'] == 'conflict'
        assert backend.downloads == 0
        assert existing.read_bytes() == b'preserve unreadable original'
    finally:
        agent.close()


def test_default_delivery_size_matches_server_limit(environment):
    agent = make_agent(environment, FakeBackend())
    try:
        assert agent.max_size == 20 * 1024 * 1024
    finally:
        agent.close()


def test_service_assets_parse_without_execution():
    import xml.etree.ElementTree as ET
    root = Path(__file__).resolve().parents[1]
    deploy = root / 'deploy' / 'windows-delivery'
    xml = ET.parse(deploy / 'TracerTransferDelivery.xml').getroot()
    assert xml.findtext('serviceaccount/user') == 'LocalService'
    assert xml.findtext('startmode') == 'Automatic'
    assert xml.findtext('id') == 'TracerTransferDelivery'
    if os.name == 'nt':
        script = "$ErrorActionPreference='Stop'; $failed=$false; Get-ChildItem -LiteralPath $env:TT_PS_SCRIPTS -Filter '*.ps1' | ForEach-Object { $t=$null;$e=$null;[void][Management.Automation.Language.Parser]::ParseFile($_.FullName,[ref]$t,[ref]$e);if($e.Count){$failed=$true;$e|ForEach-Object {Write-Output $_.Message}} }; if($failed){exit 1}"
        result = subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',script],
                                env={**os.environ,'TT_PS_SCRIPTS':str(deploy)}, capture_output=True, text=True)
        assert result.returncode == 0, result.stdout + result.stderr
