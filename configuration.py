"""Strict deployment loader: private operator profiles never enter public packages."""
from __future__ import annotations
import json
import os
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit

DEFAULT_GENERIC_SERVER = ''
DEFAULT_GENERIC_OUTPUT_DIR = 'C:/FreeForm/Tracer'


def validate_server_url(value):
    if not isinstance(value, str) or not value or any(ord(c) <= 32 or ord(c) == 127 for c in value):
        raise ValueError('Servidor não configurado ou origem HTTPS inválida.')
    origin = urlsplit(value)
    try:
        port = origin.port
    except ValueError:
        raise ValueError('Porta HTTPS inválida.') from None
    if (origin.scheme != 'https' or not origin.hostname or origin.username is not None
            or origin.password is not None or origin.query or origin.fragment
            or origin.path not in ('', '/') or (port is not None and not 1 <= port <= 65535)
            or '\\' in value):
        raise ValueError('Configure uma origem HTTPS sem credenciais, caminho ou query.')
    return value


def _candidate_config_paths():
    explicit = os.environ.get('TRACER_DEPLOYMENT_CONFIG')
    if explicit is not None:
        if not explicit:
            raise ValueError('TRACER_DEPLOYMENT_CONFIG não pode estar vazio.')
        return [Path(explicit)]
    if getattr(sys, 'frozen', False):
        installed = Path(sys.executable).parent
        bundled = Path(getattr(sys, '_MEIPASS', installed))
        return [installed / 'deployment.json', bundled / 'deployment.json']
    root = Path(__file__).resolve().parent
    return [root / 'deployment.local.json', root / 'deployment.json']


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Campo de implantação duplicado.')
        result[key] = value
    return result


def load_deployment_config():
    explicit = 'TRACER_DEPLOYMENT_CONFIG' in os.environ
    for path in _candidate_config_paths():
        if not path.exists():
            if explicit:
                raise ValueError('Arquivo de implantação explícito não encontrado.')
            continue
        if path.is_symlink() or path.is_junction() or not path.is_file() or path.stat().st_size > 16384:
            raise ValueError('Arquivo de implantação inseguro ou excessivo.')
        try:
            cfg = json.loads(path.read_text(encoding='utf-8-sig'), object_pairs_hook=_object)
        except (OSError, UnicodeError, ValueError):
            raise ValueError('Arquivo de implantação inválido; nenhum fallback foi utilizado.') from None
        allowed = {'server_url', 'delivery_output_dir', 'admin_ssh_host', 'backup_ssh_host'}
        if not isinstance(cfg, dict) or set(cfg) - allowed or any(not isinstance(v, str) or not v for v in cfg.values()):
            raise ValueError('Campos de implantação desconhecidos ou inválidos.')
        if getattr(sys, 'frozen', False) and set(cfg) - {'server_url'}:
            raise ValueError('Produto distribuído aceita apenas o endpoint, não o perfil administrativo.')
        if 'server_url' in cfg:
            validate_server_url(cfg['server_url'])
        return cfg
    return {}


def get_default_server_url(default=DEFAULT_GENERIC_SERVER):
    if 'TRACER_SERVER_URL' in os.environ:
        return validate_server_url(os.environ['TRACER_SERVER_URL'])
    value = load_deployment_config().get('server_url', default)
    return validate_server_url(value) if value else ''


def get_default_output_dir(default=DEFAULT_GENERIC_OUTPUT_DIR):
    value = os.environ.get('TRACER_DELIVERY_OUTPUT_DIR')
    if value is None:
        value = load_deployment_config().get('delivery_output_dir', default)
    if not isinstance(value, str) or not value or '\x00' in value or not Path(value).is_absolute():
        raise ValueError('Diretório de entrega deve ser absoluto.')
    return value


def _ssh_host(field, variable, default):
    value = os.environ.get(variable)
    if value is None:
        value = load_deployment_config().get(field, default)
    if not re.fullmatch(r'[A-Za-z0-9_.-]+@[A-Za-z0-9.-]+', value or ''):
        raise ValueError('Identidade SSH de implantação inválida.')
    return value


def get_admin_ssh_host(default='traceradmin@192.0.2.10'):
    return _ssh_host('admin_ssh_host', 'TRACER_ADMIN_SSH_HOST', default)


def get_backup_ssh_host(default='tracer-backup@192.0.2.20'):
    return _ssh_host('backup_ssh_host', 'TRACER_BACKUP_SSH_HOST', default)
