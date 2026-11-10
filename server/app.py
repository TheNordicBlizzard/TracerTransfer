"""Application factory. Production listener must remain loopback-only."""
import logging
import json
from contextlib import asynccontextmanager
import secrets
import sqlite3
import uuid
from logging.handlers import RotatingFileHandler
from pathlib import Path
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ConfigDict, Field, StrictBool
from server.auth import Auth, fingerprint, trusted_client_peer
from server.database import Database, utcnow
from server.users import HASHER, validate_account


class Model(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Login(Model):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=1024)


class NewUser(Login):
    role: str = 'operator'


class ChangeUser(Model):
    enabled: StrictBool | None = None
    role: str | None = None
    password: str | None = Field(default=None, max_length=1024)


class NewDevice(Model):
    name: str = Field(min_length=1, max_length=120, pattern=r'^[^\x00-\x1f\x7f]+$')


class Heartbeat(Model):
    pending: int = Field(ge=0, le=2147483647, strict=True)
    error: str | None = Field(default=None, max_length=1000)


def create_app(data_dir, max_size=20*1024*1024, *, output_dir=None, delivery_mode='direct'):
    from server.storage import Storage
    from server.dashboard import HTML
    if max_size <= 0:
        raise ValueError('max_size must be positive')
    db = Database(data_dir)
    auth = Auth(db)
    storage = Storage(db, max_size, output_dir=output_dir, delivery_mode=delivery_mode)
    @asynccontextmanager
    async def lifespan(_app):
        try:
            yield
        finally:
            audit.removeHandler(handler)
            handler.close()

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.db, app.state.auth, app.state.storage = db, auth, storage
    app.state.delivery_mode = delivery_mode
    from server.activation import register as register_activation
    register_activation(app, db, auth)
    if delivery_mode == 'queued':
        from server.delivery import register as register_delivery
        register_delivery(app, db, storage, auth)

    @app.exception_handler(RequestValidationError)
    async def invalid_input(request, exc):
        # Do not echo rejected passwords, codes or secrets in validation responses.
        return JSONResponse(status_code=422, content={'detail': 'Invalid request fields'})
    audit = logging.getLogger('tracer.audit.' + str(uuid.uuid4()))
    audit.setLevel(logging.INFO)
    audit.propagate = False
    handler = RotatingFileHandler(Path(data_dir)/'audit.log', maxBytes=1024*1024, backupCount=5, encoding='utf-8')
    audit.addHandler(handler)

    @app.middleware('http')
    async def security(request, call_next):
        response = await call_next(request)
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'; base-uri 'none'"
        # Route template rather than raw URL: never log secrets or user-supplied strings.
        route = request.scope.get('route')
        audit.info('%s %s %s %s', utcnow(), request.method,
                   getattr(route, 'path', 'unmatched'), response.status_code)
        return response

    @app.get('/health')
    def health():
        return {'ok': True}

    @app.get('/', response_class=HTMLResponse)
    def dashboard():
        return HTML

    @app.post('/api/login')
    def login(body: Login, request: Request):
        return auth.login(body.username, body.password, trusted_client_peer(request))

    @app.post('/api/logout')
    def logout(request: Request):
        return auth.logout(request)

    @app.get('/api/me')
    def me(request: Request):
        user = auth.user(request)
        return {k:user[k] for k in ('username','role')}

    @app.get('/api/users')
    def users(request: Request):
        auth.user(request, admin=True)
        with db.connect() as c:
            return [dict(r) for r in c.execute('SELECT username,role,enabled FROM users ORDER BY username')]

    @app.post('/api/users', status_code=201)
    def create_user(body: NewUser, request: Request):
        auth.user(request, admin=True)
        validate_account(body.username, body.password, body.role)
        hashed = HASHER.hash(body.password)
        try:
            with db.connect(write=True) as c:
                c.execute('INSERT INTO users(username,password_hash,role) VALUES(?,?,?)', (body.username, hashed, body.role))
        except sqlite3.IntegrityError:
            raise HTTPException(409, 'Username already exists')
        return {'username':body.username,'role':body.role}

    @app.patch('/api/users/{username}')
    def change_user(username: str, body: ChangeUser, request: Request):
        auth.user(request, admin=True)
        if any(getattr(body, k) is None for k in body.model_fields_set):
            raise HTTPException(422, 'Null fields not supported')
        validate_account(username, body.password, body.role)
        hashed = HASHER.hash(body.password) if body.password is not None else None
        with db.connect(write=True) as c:
            row = c.execute('SELECT * FROM users WHERE username=?', (username,)).fetchone()
            if not row:
                raise HTTPException(404, 'User not found')
            enabled = body.enabled if body.enabled is not None else bool(row['enabled'])
            role = body.role or row['role']
            if row['enabled'] and row['role']=='admin' and (not enabled or role!='admin'):
                count = c.execute("SELECT COUNT(*) FROM users WHERE enabled=1 AND role='admin'").fetchone()[0]
                if count <= 1:
                    raise HTTPException(409, 'Cannot remove the last enabled administrator')
            c.execute('UPDATE users SET enabled=?,role=?,password_hash=? WHERE username=?',
                      (int(enabled),role,hashed or row['password_hash'],username))
            c.execute('DELETE FROM sessions WHERE username=?', (username,))
        auth.revoke_user(username)
        return {'username':username,'role':role,'enabled':enabled}

    @app.post('/api/devices', status_code=201)
    def create_device(body: NewDevice, request: Request):
        auth.user(request, admin=True)
        if delivery_mode == 'queued':
            raise HTTPException(403, 'Enrollment requires an activation code')
        identifier, token = uuid.uuid4().hex, secrets.token_urlsafe(32)
        with db.connect(write=True) as c:
            c.execute('INSERT INTO devices(id,name,token_hash,created_at) VALUES(?,?,?,?)',
                      (identifier,body.name,fingerprint(token),utcnow()))
        return {'id':identifier,'token':token}

    def device_list():
        with db.connect() as c:
            return [dict(r) for r in c.execute('SELECT id,name,role,activation_id,enabled,created_at,last_seen,pending,error FROM devices ORDER BY created_at')]

    @app.get('/api/devices')
    def devices(request: Request):
        auth.user(request)
        return device_list()

    @app.delete('/api/devices/{identifier}')
    def delete_device(identifier: str, request: Request):
        auth.user(request, admin=True)
        with db.connect(write=True) as c:
            if not c.execute('UPDATE devices SET enabled=0 WHERE id=?', (identifier,)).rowcount:
                raise HTTPException(404, 'Device not found')
        return {'ok':True}

    @app.post('/api/heartbeat')
    def heartbeat(body: Heartbeat, request: Request):
        device = auth.device(request, activated=delivery_mode == 'queued')
        with db.connect(write=True) as c:
            c.execute('UPDATE devices SET last_seen=?,pending=?,error=? WHERE id=?',
                      (utcnow(),body.pending,body.error,device['id']))
        return {'ok':True}

    @app.put('/api/files/{filename}')
    async def upload(filename: str, request: Request):
        device = auth.device(request, 'sender', activated=delivery_mode == 'queued')
        try:
            result = await storage.receive(request, device, filename)
            return JSONResponse(status_code=202, content=result) if delivery_mode == 'queued' else result
        except HTTPException as exc:
            if exc.status_code == 409:
                # Storage has already validated this ASCII filename and checksum.
                # Record enough to resolve a conflict, without tokens or file content.
                audit.warning('%s upload_conflict device=%s name=%s sha256=%s',
                              utcnow(), device['id'], filename,
                              request.headers['x-content-sha256'])
            raise

    @app.get('/api/status')
    def status(request: Request):
        user = auth.user(request)
        with db.connect() as c:
            files = [dict(r) for r in c.execute('SELECT device_id,name,sha256,size,received_at FROM files ORDER BY received_at DESC LIMIT 1000')]
            deliveries = ([dict(r) for r in c.execute('SELECT id AS receipt_id,device_id,name,sha256,size,received_at,status,error,lease_holder,lease_until FROM deliveries ORDER BY received_at DESC LIMIT 1000')]
                          if user['role'] == 'admin' else [])
        maintenance={}
        try:
            report_path=db.data_dir/'maintenance-status.json'
            if not report_path.is_symlink() and report_path.stat().st_size<=8192:
                report=json.loads(report_path.read_text(encoding='utf-8'))
                allowed={'backup_ok','checked_at','health_ok','space_ok','free_bytes','last_backup_at','backup_error'}
                maintenance={key:value for key,value in report.items() if key in allowed}
        except (OSError,ValueError,AttributeError):
            pass
        return {'devices':device_list(),'files':files,'delivery_mode':delivery_mode,'deliveries':deliveries,'maintenance':maintenance}
    return app
