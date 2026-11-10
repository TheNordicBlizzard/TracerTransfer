"""Opaque bearer credentials; only SHA-256 fingerprints persist."""
import hashlib
import ipaddress
import secrets
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from argon2.exceptions import VerificationError, InvalidHashError
from fastapi import HTTPException
from server.database import utcnow
from server.users import HASHER


def fingerprint(token):
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


def trusted_client_peer(request):
    """Trust only Caddy's single literal IP header over a loopback socket.

    Caddy MUST overwrite X-Tracer-Client-IP with {remote_host}, never append
    or pass through a client-supplied value. Other forwarding headers are ignored.
    """
    peer = request.client.host if request.client else 'unknown'
    try:
        if not ipaddress.ip_address(peer).is_loopback:
            return peer
        values = request.headers.getlist('x-tracer-client-ip')
        if len(values) != 1 or '%' in values[0]:
            return peer
        return str(ipaddress.ip_address(values[0]))
    except ValueError:
        return peer


def bearer(request):
    header = request.headers.get('authorization', '')
    scheme, _, token = header.partition(' ')
    if scheme.lower() != 'bearer' or not token or len(token) > 512:
        raise HTTPException(401, 'Bearer credential required', headers={'WWW-Authenticate':'Bearer'})
    return fingerprint(token)


class Auth:
    def __init__(self, db):
        self.db = db
        self.attempts = defaultdict(deque)
        self.lock = threading.Lock()
        self.dummy = HASHER.hash(secrets.token_urlsafe(32))
        self.sessions = {}

    def limit(self, peer, maximum=10):
        now = time.monotonic()
        with self.lock:
            for key in list(self.attempts):
                if not self.attempts[key] or self.attempts[key][-1] <= now - 60:
                    del self.attempts[key]
            q = self.attempts[peer]
            while q and q[0] <= now - 60:
                q.popleft()
            if len(q) >= maximum:
                raise HTTPException(429, 'Too many attempts', headers={'Retry-After':'60'})
            q.append(now)

    def revoke_user(self, username):
        with self.lock:
            self.sessions = {k:v for k,v in self.sessions.items() if v['username'] != username}

    def login(self, username, password, peer):
        # Defense in depth for callers bypassing the Pydantic HTTP model.
        if (not isinstance(username, str) or not 1 <= len(username) <= 64
                or not isinstance(password, str) or not 1 <= len(password) <= 1024):
            raise HTTPException(422, 'Invalid login input')
        self.limit(peer)
        with self.db.connect() as c:
            row = c.execute('SELECT * FROM users WHERE username=?', (username,)).fetchone()
        try:
            valid = HASHER.verify(row['password_hash'] if row else self.dummy, password)
        except (VerificationError, InvalidHashError):
            valid = False
        if not valid or not row or not row['enabled']:
            raise HTTPException(401, 'Invalid credentials')
        token = secrets.token_urlsafe(32)
        with self.db.connect(write=True) as c:
            current = c.execute('SELECT * FROM users WHERE username=?', (username,)).fetchone()
            if not current or not current['enabled'] or current['password_hash'] != row['password_hash']:
                raise HTTPException(401, 'Account changed; login again')
            expires = (datetime.now(timezone.utc)+timedelta(minutes=30)).isoformat()
            with self.lock:
                self.sessions = {k:v for k,v in self.sessions.items() if v['expires_at'] > utcnow()}
                self.sessions[fingerprint(token)] = {'username': username, 'expires_at': expires,
                                                      'password_hash': current['password_hash'], 'role': current['role']}
        return {'token':token,'username':username,'role':current['role']}

    def logout(self, request):
        # Deliberately don't authenticate the session first: retrying a logout
        # with the same well-formed Bearer must remain successful after revocation.
        token_hash = bearer(request)
        with self.lock:
            self.sessions.pop(token_hash, None)
        return {'ok': True}

    def user(self, request, admin=False):
        token_hash = bearer(request)
        with self.lock:
            session = self.sessions.get(token_hash)
        if not session or session['expires_at'] <= utcnow():
            raise HTTPException(401, 'Session expired or revoked')
        with self.db.connect() as c:
            row = c.execute('SELECT * FROM users WHERE username=? AND enabled=1', (session['username'],)).fetchone()
        if not row or row['password_hash'] != session['password_hash'] or row['role'] != session['role']:
            raise HTTPException(401, 'Session expired or revoked')
        if admin and row['role'] != 'admin':
            raise HTTPException(403, 'Administrator required')
        return dict(row)

    def device(self, request, role=None, *, activated=False):
        with self.db.connect() as c:
            row = c.execute('SELECT * FROM devices WHERE token_hash=? AND enabled=1', (bearer(request),)).fetchone()
            if row and activated:
                provenance = c.execute('SELECT 1 FROM activations WHERE id=? AND device_id=? AND revoked=0',
                                       (row['activation_id'], row['id'])).fetchone()
                if not provenance:
                    raise HTTPException(403, 'Activated device required')
        if not row:
            raise HTTPException(401, 'Device credential invalid or revoked')
        if role is not None and row['role'] != role:
            raise HTTPException(403, 'Device role not permitted')
        return dict(row)
