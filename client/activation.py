"""Code enrollment. Persist installation provenance before any network request."""
from dataclasses import replace
import re
import secrets
import uuid
import httpx
from client.auth import http_client
from client.state import (COMPANY_SERVER, baseline_folder, read_config, save_config,
                          protect_token, unprotect_token)


class ActivationError(RuntimeError):
    pass


class ActivationService:
    def __init__(self, data_dir, *, server_url=COMPANY_SERVER, ca_file=None, transport=None):
        # Alternative HTTPS origin/CA is dependency injection for isolated tests,
        # never a user-facing setting.
        self.data_dir = data_dir
        self.server_url = server_url
        self.ca_file = ca_file
        self.transport = transport

    def activate(self, code, name):
        if not isinstance(code, str) or not re.fullmatch(r'TT-[0-9A-F]{40}', code):
            raise ActivationError('Código inválido. Copie exatamente o código TT- recebido.')
        if (not isinstance(name, str) or not 1 <= len(name.strip()) <= 120
                or re.search(r'[\x00-\x1f\x7f]', name)):
            raise ActivationError('Informe um nome de cliente com até 120 caracteres.')
        config = read_config(self.data_dir)
        if config.activated:
            raise ActivationError('Este cliente já está ativado.')
        if config.protected_token and not config.installation_id:
            raise ActivationError('Cadastro antigo encontrado. Solicite migração ao responsável.')
        if config.installation_id and (config.server_url != self.server_url or config.ca_file != self.ca_file):
            raise ActivationError('A instalação pertence a outro servidor.')
        if not config.installation_id:
            config = replace(config, server_url=self.server_url, ca_file=self.ca_file,
                             installation_id=uuid.uuid4().hex,
                             protected_token=protect_token(secrets.token_urlsafe(32)),
                             delivery_mode='queued', device_id='', activation_id='', role='')
            save_config(self.data_dir, config)
        secret = unprotect_token(config.protected_token)
        try:
            with http_client(self.server_url, self.ca_file, self.transport) as client:
                response = client.post('/api/activate', json={
                    'code': code, 'installation_id': config.installation_id,
                    'installation_secret': secret, 'name': name.strip()})
                if response.status_code in (400, 401, 403, 404, 409, 410, 422):
                    raise ActivationError('Código inválido, expirado, revogado ou já utilizado por outra instalação.')
                if response.status_code == 429:
                    raise ActivationError('Muitas tentativas. Aguarde antes de tentar novamente.')
                response.raise_for_status()
                result = response.json()
                if (response.status_code != 200 or not isinstance(result, dict) or result.get('role') != 'sender'
                        or not isinstance(result.get('device_id'), str) or not result['device_id']
                        or not isinstance(result.get('activation_id'), str) or not result['activation_id']):
                    raise ActivationError('Ativação incompatível: é necessário um código de remetente.')
            # Read latest local settings, retaining the persisted retry identity.
            config = replace(read_config(self.data_dir), device_id=result['device_id'],
                             activation_id=result['activation_id'], role='sender')
            config.activation_baseline = baseline_folder(config.source_dir)
            save_config(self.data_dir, config)
            return config
        except ActivationError:
            raise
        except (httpx.HTTPError, ValueError, KeyError, TypeError, OSError):
            raise ActivationError('Ativação indisponível. Tente novamente com o mesmo código; a identidade foi preservada.') from None
