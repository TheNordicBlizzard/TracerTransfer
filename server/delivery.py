"""Local durable queue, scoped expiring leases and replay-safe acknowledgements."""
import secrets
import threading
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import Field
from starlette.background import BackgroundTask
from server.activation import Model
from server.auth import fingerprint
from server.database import utcnow


class Lease(Model):
    limit: int = Field(default=1, ge=1, le=10, strict=True)


class Ack(Model):
    lease_token: str = Field(min_length=43, max_length=43, pattern=r'^[A-Za-z0-9_-]{43}$')
    sha256: str = Field(min_length=64, max_length=64, pattern=r'^[0-9a-f]{64}$')
    result: Literal['delivered', 'conflict', 'uncertain']
    error: str | None = Field(default=None, min_length=1, max_length=80, pattern=r'^[A-Za-z0-9_.-]+$')


class Delivery:
    def __init__(self, db, storage, auth):
        self.db, self.storage, self.auth = db, storage, auth
        self.lease_seconds = 120
        self.download_slots = threading.BoundedSemaphore(4)

    def device(self, request):
        return self.auth.device(request, 'delivery', activated=True)

    @staticmethod
    def active(c, device_id):
        if not c.execute("SELECT 1 FROM devices d JOIN activations a ON a.id=d.activation_id "
                         "AND a.device_id=d.id WHERE d.id=? AND d.enabled=1 AND d.role='delivery' AND a.revoked=0",
                         (device_id,)).fetchone():
            raise HTTPException(401, 'Device revoked')

    def lease(self, device, limit):
        now = utcnow()
        until = (datetime.now(timezone.utc) + timedelta(seconds=self.lease_seconds)).isoformat()
        items = []
        with self.db.connect(write=True) as c:
            self.active(c, device['id'])
            # An authenticated empty poll is contact too, not proof of delivery.
            c.execute('UPDATE devices SET last_seen=? WHERE id=?', (now, device['id']))
            # A live lease of any casing blocks that operational filename globally.
            rows = c.execute("SELECT * FROM deliveries WHERE status='pending' ORDER BY received_at,id").fetchall()
            for row in rows:
                if len(items) >= limit:
                    break
                if c.execute("SELECT 1 FROM deliveries WHERE name=? COLLATE NOCASE AND status='pending' AND lease_until>?",
                             (row['name'], now)).fetchone():
                    continue
                token = secrets.token_urlsafe(32)
                c.execute('UPDATE deliveries SET lease_holder=?,lease_hash=?,lease_until=? WHERE id=?',
                          (device['id'], fingerprint(token), until, row['id']))
                items.append({k: row[k] for k in ('id', 'name', 'sha256', 'size')}
                             | {'lease_token': token, 'lease_until': until})
        return {'items': items}

    @staticmethod
    def valid_lease(row, device, token):
        return (row and row['status'] == 'pending' and row['lease_holder'] == device['id']
                and row['lease_until'] and row['lease_until'] > utcnow()
                and secrets.compare_digest(row['lease_hash'] or '', fingerprint(token)))

    def download(self, device, identifier, token):
        if not token or len(token) > 512:
            raise HTTPException(409, 'Valid delivery lease required')
        with self.db.connect() as c:
            self.active(c, device['id'])
            row = c.execute('SELECT * FROM deliveries WHERE id=?', (identifier,)).fetchone()
            if not self.valid_lease(row, device, token):
                raise HTTPException(409, 'Valid delivery lease required')
        if not self.download_slots.acquire(blocking=False):
            raise HTTPException(429, 'Download capacity reached', headers={'Retry-After': '2'})
        lock, released = threading.Lock(), False
        def release():
            nonlocal released
            with lock:
                if not released:
                    released = True
                    self.download_slots.release()
        try:
            path = self.storage.spool / row['id']
            if not self.storage._integrity_matches(path, row['sha256'], row['size']):
                raise HTTPException(409, 'Spool integrity failure')
            def chunks():
                try:
                    with path.open('rb') as source:
                        while data := source.read(128 * 1024):
                            yield data
                finally:
                    release()
            return StreamingResponse(chunks(), media_type='application/octet-stream',
                                     headers={'Content-Length': str(row['size']), 'X-Content-SHA256': row['sha256']},
                                     background=BackgroundTask(release))
        except BaseException:
            release()
            raise

    def ack(self, device, identifier, body):
        if body.result == 'delivered' and body.error is not None:
            raise HTTPException(422, 'Delivered acknowledgement cannot contain an error')
        with self.db.connect(write=True) as c:
            self.active(c, device['id'])
            row = c.execute('SELECT * FROM deliveries WHERE id=?', (identifier,)).fetchone()
            if not row or row['sha256'] != body.sha256:
                raise HTTPException(409, 'Acknowledgement mismatch')
            if row['status'] != 'pending':
                if (row['ack_holder'] == device['id'] and row['ack_hash'] == fingerprint(body.lease_token)
                        and row['ack_result'] == body.result and row['ack_error'] == body.error):
                    return {'ok': True, 'status': row['status']}
                raise HTTPException(409, 'Acknowledgement mismatch')
            if not self.valid_lease(row, device, body.lease_token):
                raise HTTPException(409, 'Lease expired or invalid')
            c.execute('UPDATE deliveries SET status=?,error=?,ack_hash=?,ack_holder=?,ack_result=?,ack_error=?, '
                      'lease_hash=NULL,lease_holder=NULL,lease_until=NULL WHERE id=?',
                      (body.result, body.error, fingerprint(body.lease_token), device['id'], body.result, body.error, identifier))
        return {'ok': True, 'status': body.result}


def register(app, db, storage, auth):
    delivery = Delivery(db, storage, auth)
    app.state.delivery = delivery

    @app.post('/api/delivery/lease')
    def lease(body: Lease, request: Request):
        return delivery.lease(delivery.device(request), body.limit)

    @app.get('/api/delivery/files/{identifier}')
    def download(identifier: str, request: Request):
        return delivery.download(delivery.device(request), identifier, request.headers.get('x-delivery-lease', ''))

    @app.post('/api/delivery/ack/{identifier}')
    def ack(identifier: str, body: Ack, request: Request):
        return delivery.ack(delivery.device(request), identifier, body)

    @app.get('/api/files/status/{receipt_id}')
    def status(receipt_id: str, request: Request):
        device = auth.device(request, 'sender', activated=True)
        with db.connect() as c:
            row = c.execute('SELECT id AS receipt_id,name,sha256,status,error FROM deliveries WHERE id=? AND device_id=?',
                            (receipt_id, device['id'])).fetchone()
        if not row:
            raise HTTPException(404, 'Receipt not found')
        return dict(row)

    @app.get('/api/deliveries')
    def listing(request: Request):
        auth.user(request, admin=True)
        with db.connect() as c:
            return [dict(r) for r in c.execute('SELECT id AS receipt_id,device_id,name,sha256,size,received_at,status,error,lease_holder,lease_until FROM deliveries ORDER BY received_at DESC LIMIT 1000')]
