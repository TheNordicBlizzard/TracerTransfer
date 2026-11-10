"""Tests for deployment-level configuration loading."""
from pathlib import Path
import json
import os
import pytest
from configuration import (
    get_default_server_url,
    get_default_output_dir,
    get_admin_ssh_host,
    get_backup_ssh_host,
    load_deployment_config,
    DEFAULT_GENERIC_SERVER,
    DEFAULT_GENERIC_OUTPUT_DIR,
)


def test_defaults_when_no_config(monkeypatch, tmp_path):
    monkeypatch.setenv('TRACER_DEPLOYMENT_CONFIG', str(tmp_path / 'nonexistent.json'))
    monkeypatch.delenv('TRACER_SERVER_URL', raising=False)
    monkeypatch.delenv('TRACER_DELIVERY_OUTPUT_DIR', raising=False)
    monkeypatch.delenv('TRACER_ADMIN_SSH_HOST', raising=False)
    monkeypatch.delenv('TRACER_BACKUP_SSH_HOST', raising=False)

    assert get_default_server_url().startswith('https://')
    assert get_default_output_dir() == DEFAULT_GENERIC_OUTPUT_DIR
    assert '@' in get_admin_ssh_host()
    assert '@' in get_backup_ssh_host()


def test_environment_override(monkeypatch):
    monkeypatch.setenv('TRACER_SERVER_URL', 'https://override.example.org')
    monkeypatch.setenv('TRACER_DELIVERY_OUTPUT_DIR', 'D:/Custom/Tracer')
    monkeypatch.setenv('TRACER_ADMIN_SSH_HOST', 'admin@admin.example.org')
    monkeypatch.setenv('TRACER_BACKUP_SSH_HOST', 'backup@backup.example.org')

    assert get_default_server_url() == 'https://override.example.org'
    assert get_default_output_dir() == 'D:/Custom/Tracer'
    assert get_admin_ssh_host() == 'admin@admin.example.org'
    assert get_backup_ssh_host() == 'backup@backup.example.org'


def test_custom_file_override(monkeypatch, tmp_path):
    cfg_file = tmp_path / 'custom_deployment.json'
    cfg_file.write_text(json.dumps({
        'server_url': 'https://json.example.net',
        'delivery_output_dir': 'E:/Tracer',
        'admin_ssh_host': 'admin@vm.test',
        'backup_ssh_host': 'backup@vm.test'
    }), encoding='utf-8')
    monkeypatch.setenv('TRACER_DEPLOYMENT_CONFIG', str(cfg_file))
    monkeypatch.delenv('TRACER_SERVER_URL', raising=False)
    monkeypatch.delenv('TRACER_DELIVERY_OUTPUT_DIR', raising=False)
    monkeypatch.delenv('TRACER_ADMIN_SSH_HOST', raising=False)
    monkeypatch.delenv('TRACER_BACKUP_SSH_HOST', raising=False)

    assert get_default_server_url() == 'https://json.example.net'
    assert get_default_output_dir() == 'E:/Tracer'
    assert get_admin_ssh_host() == 'admin@vm.test'
    assert get_backup_ssh_host() == 'backup@vm.test'
