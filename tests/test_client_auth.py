import httpx
import pytest
from client.auth import Authorizer, AuthorizationError


def test_logout_clears_session_even_when_server_offline():
    def handle(request):
        if request.url.path == '/api/login':
            return httpx.Response(200,json={'token':'session','role':'admin','username':'admin'})
        if request.url.path == '/api/me':
            return httpx.Response(200,json={'role':'admin','username':'admin'})
        raise httpx.ConnectError('offline',request=request)
    auth=Authorizer('https://localhost',transport=httpx.MockTransport(handle))
    auth.login('admin','long-password-value')
    auth.logout()
    assert auth.session is None
    assert auth.identity is None
    auth.close()


def test_failed_enrollment_invalidates_memory_session():
    def handle(request):
        if request.url.path == '/api/login':
            return httpx.Response(200,json={'token':'session','role':'admin','username':'admin'})
        if request.url.path == '/api/me':
            return httpx.Response(200,json={'role':'admin','username':'admin'})
        return httpx.Response(401,json={'detail':'revoked'})
    auth=Authorizer('https://localhost',transport=httpx.MockTransport(handle))
    auth.login('admin','long-password-value')
    with pytest.raises(AuthorizationError):
        auth.enroll('device')
    assert auth.session is None
    assert auth.identity is None
    auth.close()


def test_logout_requests_server_revocation():
    requests=[]
    def handle(request):
        requests.append((request.url.path,request.headers.get('authorization')))
        if request.url.path == '/api/login':
            return httpx.Response(200,json={'token':'session','role':'admin','username':'admin'})
        if request.url.path == '/api/me':
            return httpx.Response(200,json={'role':'admin','username':'admin'})
        return httpx.Response(200,json={'ok':True})
    auth=Authorizer('https://localhost',transport=httpx.MockTransport(handle))
    auth.login('admin','long-password-value')
    auth.logout()
    assert ('/api/logout','Bearer session') in requests
    auth.close()
