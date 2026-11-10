"""Central authorization. A session is memory-only and every mutation revalidates it."""
import ssl
import httpx
from client.state import Config


class AuthorizationError(RuntimeError):
    pass


def http_client(server_url, ca_file=None, transport=None):
    Config(server_url=server_url).validate()
    if not server_url:
        raise ValueError('Servidor não configurado.')
    context = ssl.create_default_context(cafile=ca_file or None)
    return httpx.Client(base_url=server_url.rstrip('/'), verify=context, timeout=10,
                        follow_redirects=False, trust_env=False, transport=transport)


class Authorizer:
    def __init__(self, server_url, ca_file=None, transport=None):
        self.server_url = server_url.rstrip('/')
        self.ca_file = ca_file
        self.client = http_client(server_url, ca_file, transport)
        self.session = None
        self.identity = None

    def login(self, username, password):
        self.logout()
        try:
            r = self.client.post('/api/login', json={'username': username, 'password': password})
            r.raise_for_status()
            self.session = r.json()['token']
            return self.revalidate()
        except Exception as exc:
            self.logout()
            raise AuthorizationError('Login indisponível ou inválido.') from exc

    def logout(self):
        previous = self.session
        self.session = None
        self.identity = None
        if previous:
            try:
                self.client.post('/api/logout', headers={'Authorization': f'Bearer {previous}'}, timeout=2)
            except httpx.HTTPError:
                # Offline logout still clears all local session state immediately.
                pass

    def revalidate(self):
        try:
            if not self.session:
                raise AuthorizationError('Faça login.')
            r = self.client.get('/api/me', headers={'Authorization': f'Bearer {self.session}'})
            r.raise_for_status()
            who = r.json()
            if who.get('role') not in ('admin', 'operator'):
                raise AuthorizationError('Perfil inválido.')
            self.identity = who
            return who
        except Exception as exc:
            self.logout()
            raise AuthorizationError('Sessão inválida, expirada ou servidor offline.') from exc

    def require_admin(self):
        who = self.revalidate()
        if who['role'] != 'admin':
            raise AuthorizationError('Somente administrador.')
        return who

    def enroll(self, name):
        self.require_admin()
        try:
            r = self.client.post('/api/devices', json={'name': name},
                headers={'Authorization': f'Bearer {self.session}'})
            r.raise_for_status()
            return r.json()
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            self.logout()
            raise AuthorizationError('Inscrição indisponível ou sessão sem permissão.') from exc

    def close(self):
        self.logout()
        self.client.close()
