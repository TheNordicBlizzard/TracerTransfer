"""Fail-closed deployment settings and preserved sender origin."""
import json
import pytest
import configuration as cfg

@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    for key in ('TRACER_SERVER_URL','TRACER_DEPLOYMENT_CONFIG','TRACER_DELIVERY_OUTPUT_DIR','TRACER_ADMIN_SSH_HOST','TRACER_BACKUP_SSH_HOST'):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(cfg, '_candidate_config_paths', lambda: [])

@pytest.mark.parametrize('url', ['', 'http://host.test', 'https://u:p@host.test', 'https://host.test/path', 'https://host.test?x=1', 'https://host.test#x','https://host.test:99999',' https://host.test','https://host.test\n'])
def test_invalid_override_never_falls_back(monkeypatch,url):
    monkeypatch.setenv('TRACER_SERVER_URL',url)
    with pytest.raises(ValueError):cfg.get_default_server_url()

@pytest.mark.parametrize('body',['{','[]','null','{"server_url":42}','{"server_url":"https://a.test","server_url":"https://b.test"}','{"token":"never-print"}'])
def test_invalid_file_never_falls_back(tmp_path,monkeypatch,body):
    path=tmp_path/'deployment.json';path.write_text(body)
    monkeypatch.setattr(cfg,'_candidate_config_paths',lambda:[path])
    with pytest.raises(ValueError) as failure:cfg.load_deployment_config()
    assert 'never-print' not in str(failure.value)

def test_missing_explicit_file_fails(tmp_path,monkeypatch):
    monkeypatch.undo()
    monkeypatch.delenv('TRACER_SERVER_URL',raising=False)
    monkeypatch.setenv('TRACER_DEPLOYMENT_CONFIG',str(tmp_path/'missing'))
    with pytest.raises(ValueError):cfg.get_default_server_url()

def test_no_profile_has_no_destination():
    assert cfg.get_default_server_url()==''

def test_frozen_rejects_admin_profile(tmp_path,monkeypatch):
    path=tmp_path/'deployment.json'
    path.write_text(json.dumps({'server_url':'https://a.test','admin_ssh_host':'admin@vm.test'}))
    monkeypatch.setattr(cfg,'_candidate_config_paths',lambda:[path])
    monkeypatch.setattr(cfg.sys,'frozen',True,raising=False)
    with pytest.raises(ValueError):cfg.load_deployment_config()

def test_generic_upgrade_preserves_bound_origin(monkeypatch):
    from client import state
    monkeypatch.setattr(state,'COMPANY_SERVER','')
    profile=state.Config(server_url='https://bound.test',device_id='d',installation_id='i',activation_id='a',protected_token='protected',role='sender')
    assert state.authorized_sender_profile(profile)
    monkeypatch.setattr(state,'COMPANY_SERVER','https://different.test')
    assert not state.authorized_sender_profile(profile)

def test_missing_destination_does_not_create_identity(tmp_path):
    from client.activation import ActivationError,ActivationService
    with pytest.raises(ActivationError,match='Servidor não configurado'):
        ActivationService(tmp_path,server_url='').activate('TT-'+'A'*40,'QA')
    assert not (tmp_path/'config.json').exists()
