"""Deployment configuration loader for TracerTransfer.

Decouples deployment endpoints (server HTTPS URL, output directories,
and administrative SSH hosts) from generic application source code.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import sys

DEFAULT_GENERIC_SERVER = 'https://tracer.example.com'
DEFAULT_GENERIC_OUTPUT_DIR = 'C:/FreeForm/Tracer'


def _candidate_config_paths() -> list[Path]:
    candidates = []
    env_path = os.environ.get('TRACER_DEPLOYMENT_CONFIG')
    if env_path:
        candidates.append(Path(env_path))

    if getattr(sys, 'frozen', False):
        exe_dir = Path(sys.executable).resolve().parent
        candidates.append(exe_dir / 'deployment.local.json')
        candidates.append(exe_dir / 'deployment.json')

    root = Path(__file__).resolve().parent
    candidates.append(root / 'deployment.local.json')
    candidates.append(root / 'deployment.json')
    candidates.append(root / 'deploy' / 'deployment.local.json')
    candidates.append(root / 'deploy' / 'deployment.json')
    return candidates


def load_deployment_config() -> dict:
    for path in _candidate_config_paths():
        try:
            if path.is_file():
                return json.loads(path.read_text(encoding='utf-8-sig'))
        except Exception:
            continue
    return {}


def get_default_server_url(default: str = DEFAULT_GENERIC_SERVER) -> str:
    env_val = os.environ.get('TRACER_SERVER_URL')
    if env_val and env_val.strip():
        return env_val.strip()
    cfg = load_deployment_config()
    server = cfg.get('server_url')
    if server and isinstance(server, str) and server.strip():
        return server.strip()
    return default


def get_default_output_dir(default: str = DEFAULT_GENERIC_OUTPUT_DIR) -> str:
    env_val = os.environ.get('TRACER_DELIVERY_OUTPUT_DIR')
    if env_val and env_val.strip():
        return env_val.strip()
    cfg = load_deployment_config()
    output_dir = cfg.get('delivery_output_dir')
    if output_dir and isinstance(output_dir, str) and output_dir.strip():
        return output_dir.strip()
    return default


def get_admin_ssh_host(default: str = 'traceradmin@192.0.2.10') -> str:
    env_val = os.environ.get('TRACER_ADMIN_SSH_HOST')
    if env_val and env_val.strip():
        return env_val.strip()
    cfg = load_deployment_config()
    host = cfg.get('admin_ssh_host')
    if host and isinstance(host, str) and host.strip():
        return host.strip()
    return default


def get_backup_ssh_host(default: str = 'tracer-backup@192.0.2.20') -> str:
    env_val = os.environ.get('TRACER_BACKUP_SSH_HOST')
    if env_val and env_val.strip():
        return env_val.strip()
    cfg = load_deployment_config()
    host = cfg.get('backup_ssh_host')
    if host and isinstance(host, str) and host.strip():
        return host.strip()
    return default
