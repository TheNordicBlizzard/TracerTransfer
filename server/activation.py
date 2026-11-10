"""Single-display enrollment codes, atomically bound to a client-owned secret."""
import secrets
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from server.auth import fingerprint, trusted_client_peer
from server.database import utcnow


class Model(BaseModel):
    model_config = ConfigDict(extra='forbid', hide_input_in_errors=True)


class NewActivation(Model):
    name: str = Field(min_length=1, max_length=120, pattern=r'^[^\x00-\x1f\x7f]+$')
    role: Literal['sender', 'delivery']
    expires_hours: int = Field(default=24, ge=1, le=720, strict=True)


class Activate(Model):
    code: str = Field(pattern=r'^TT-[0-9A-F]{40}$', min_length=43, max_length=43)
    installation_id: str = Field(pattern=r'^[0-9a-f]{32}$', min_length=32, max_length=32)
    installation_secret: str = Field(pattern=r'^[A-Za-z0-9_-]{43}$', min_length=43, max_length=43)
    name: str = Field(min_length=1, max_length=120, pattern=r'^[^\x00-\x1f\x7f]+$')


def register(app, db, auth):
    @app.post('/api/activations', status_code=201)
    def create(body: NewActivation, request: Request):
        auth.user(request, admin=True)
        identifier, code = uuid.uuid4().hex, 'TT-' + secrets.token_hex(20).upper()
        expires = (datetime.now(timezone.utc) + timedelta(hours=body.expires_hours)).isoformat()
        with db.connect(write=True) as c:
            c.execute('INSERT INTO activations(id,code_hash,name,role,created_at,expires_at) VALUES(?,?,?,?,?,?)',
                      (identifier, fingerprint(code), body.name, body.role, utcnow(), expires))
        return {'id': identifier, 'code': code, 'role': body.role, 'expires_at': expires}

    @app.get('/api/activations')
    def listing(request: Request):
        auth.user(request, admin=True)
        with db.connect() as c:
            return [dict(r) for r in c.execute('SELECT id,name,role,created_at,expires_at,revoked,device_id,used_at FROM activations ORDER BY created_at DESC LIMIT 1000')]

    @app.delete('/api/activations/{identifier}')
    def revoke(identifier: str, request: Request):
        auth.user(request, admin=True)
        with db.connect(write=True) as c:
            row = c.execute('SELECT * FROM activations WHERE id=?', (identifier,)).fetchone()
            if not row:
                raise HTTPException(404, 'Activation not found')
            if row['device_id']:
                raise HTTPException(409, 'Activation used; revoke the device instead')
            c.execute('UPDATE activations SET revoked=1 WHERE id=?', (identifier,))
        return {'ok': True}

    @app.post('/api/activate')
    def redeem(body: Activate, request: Request):
        auth.limit('activation:' + trusted_client_peer(request), maximum=30)
        secret_hash = fingerprint(body.installation_secret)
        with db.connect(write=True) as c:
            row = c.execute('SELECT * FROM activations WHERE code_hash=?', (fingerprint(body.code),)).fetchone()
            if not row or row['revoked']:
                raise HTTPException(401, 'Activation invalid or unavailable')
            if row['device_id']:
                device = c.execute('SELECT * FROM devices WHERE id=?', (row['device_id'],)).fetchone()
                if (not device or not device['enabled'] or device['installation_id'] != body.installation_id
                        or not secrets.compare_digest(device['token_hash'], secret_hash)):
                    raise HTTPException(401, 'Activation invalid or unavailable')
                return {'device_id': device['id'], 'activation_id': row['id'], 'role': row['role']}
            if row['expires_at'] <= utcnow():
                raise HTTPException(401, 'Activation invalid or unavailable')
            identifier = uuid.uuid4().hex
            try:
                c.execute('INSERT INTO devices(id,name,token_hash,created_at,role,activation_id,installation_id) VALUES(?,?,?,?,?,?,?)',
                          (identifier, body.name, secret_hash, utcnow(), row['role'], row['id'], body.installation_id))
            except sqlite3.IntegrityError:
                raise HTTPException(409, 'Installation already enrolled') from None
            c.execute('UPDATE activations SET device_id=?,used_at=? WHERE id=?', (identifier, utcnow(), row['id']))
        return {'device_id': identifier, 'activation_id': row['id'], 'role': row['role']}
