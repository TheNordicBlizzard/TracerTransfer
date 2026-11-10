"""Successful authenticated delivery polls are contact, including empty leases."""
from tests.test_activation import queued, enroll


def test_empty_delivery_poll_updates_contact_only_for_authorized_holder(queued):
    app,c,admin=queued
    _,sender=enroll(c,admin)
    _,delivery=enroll(c,admin,'delivery')
    def records():
        return c.get('/api/status',headers=admin).json()['devices']
    assert all(d['last_seen'] is None for d in records())
    response=c.post('/api/delivery/lease',headers=delivery,json={'limit':1})
    assert response.status_code==200 and response.json()=={'items':[]}
    rows=records()
    worker=next(d for d in rows if d['role']=='delivery')
    assert worker['last_seen']
    assert next(d for d in rows if d['role']=='sender')['last_seen'] is None
    before=worker['last_seen']
    assert c.post('/api/delivery/lease',headers=sender,json={'limit':1}).status_code==403
    assert next(d for d in records() if d['role']=='delivery')['last_seen']==before
    assert c.delete('/api/devices/'+worker['id'],headers=admin).status_code==200
    assert c.post('/api/delivery/lease',headers=delivery,json={'limit':1}).status_code==401
    assert next(d for d in records() if d['role']=='delivery')['last_seen']==before
