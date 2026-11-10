"""Live Linux staging API validation; not an industrial/Windows delivery test."""
import hashlib
import json
from pathlib import Path
import secrets
import sqlite3
import sys
import uuid
import httpx


def main():
    data=Path('/var/lib/tracer-transfer')
    if sys.platform!='linux' or '--allow-staging' not in sys.argv or not (data/'.staging-deployment').is_file():
        raise RuntimeError('Explicit Linux staging marker/approval required')
    sys.path.insert(0,'/opt/tracer-transfer')
    from server.users import HASHER
    label='validation-'+uuid.uuid4().hex[:12]
    password=secrets.token_urlsafe(32)
    devices=[]; credentials=[]; codes=[]
    report={'kind':'live_backend_protocol_only','checks':[]}
    db=sqlite3.connect(data/'tracer.sqlite3',timeout=10)
    try:
        assert db.execute("SELECT COUNT(*) FROM users WHERE role='admin' AND enabled=1").fetchone()[0]>=1
        db.execute('INSERT INTO users(username,password_hash,role) VALUES(?,?,?)',(label,HASHER.hash(password),'admin'));db.commit()
        with httpx.Client(base_url='http://127.0.0.1:8765',trust_env=False,timeout=15) as api:
            assert api.get('/health').json()=={'ok':True}
            login=api.post('/api/login',json={'username':label,'password':password})
            assert login.status_code==200
            admin={'Authorization':'Bearer '+login.json()['token']}
            assert api.get('/api/status',headers=admin).json()['delivery_mode']=='queued'
            report['checks'].append('real_login_queued_mode')
            for role in ['sender','delivery']:
                issued=api.post('/api/activations',headers=admin,json={'name':label+'-'+role,'role':role,'expires_hours':24})
                assert issued.status_code==201
                code=issued.json()['code'];codes.append(code)
                identity=uuid.uuid4().hex;secret=secrets.token_urlsafe(32)
                payload={'code':code,'installation_id':identity,'installation_secret':secret,'name':label+'-'+role}
                enrolled=api.post('/api/activate',json=payload)
                assert enrolled.status_code==200 and enrolled.json()['role']==role
                devices.append(enrolled.json()['device_id']);credentials.append(secret)
                retry=api.post('/api/activate',json=payload)
                assert retry.status_code==200 and retry.json()==enrolled.json()
                payload['installation_id']=uuid.uuid4().hex
                assert api.post('/api/activate',json=payload).status_code==401
            report['checks'].append('single_use_idempotent_role_scoped_activation')
            listing=api.get('/api/activations',headers=admin)
            assert listing.status_code==200
            assert all(not {'code','code_hash','installation_secret'}&set(item) for item in listing.json())
            sender={'Authorization':'Bearer '+credentials[0]};delivery={'Authorization':'Bearer '+credentials[1]}
            assert api.post('/api/delivery/lease',headers=sender,json={'limit':1}).status_code==403
            body=b'SYNTHETIC LINUX BACKEND VALIDATION - NO INDUSTRIAL DELIVERY\r\n'
            digest=hashlib.sha256(body).hexdigest();name=label+'.oma'
            incoming=api.put('/api/files/'+name,headers={**sender,'X-Content-SHA256':digest},content=body)
            assert incoming.status_code==202 and incoming.json()['status']=='pending'
            receipt=incoming.json()['receipt_id']
            from server.maintenance import backup,restore
            import shutil
            copy=backup(data,Path('/var/backups/tracer-transfer'))
            manifest=json.loads((copy/'manifest.json').read_text())
            assert 'spool/'+receipt in manifest['required_payloads']
            target_restore=Path('/var/backups/tracer-transfer')/('restore-live-'+uuid.uuid4().hex)
            restored=restore(copy,target_restore)
            assert hashlib.sha256((restored/'spool'/receipt).read_bytes()).hexdigest()==digest
            shutil.rmtree(restored)
            report['checks'].append('live_pending_payload_backup_and_isolated_restore')
            leased=api.post('/api/delivery/lease',headers=delivery,json={'limit':1})
            assert leased.status_code==200
            items=leased.json()['items'];assert len(items)==1 and items[0]['id']==receipt
            lease=items[0]['lease_token']
            downloaded=api.get('/api/delivery/files/'+receipt,headers={**delivery,'X-Delivery-Lease':lease})
            assert downloaded.status_code==200 and hashlib.sha256(downloaded.content).hexdigest()==digest
            report['checks'].append('durable_receipt_lease_download_hash_and_isolation')
            ack={'lease_token':lease,'sha256':digest,'result':'delivered','error':None}
            assert api.post('/api/delivery/ack/'+receipt,headers=delivery,json=ack).status_code==200
            assert api.post('/api/delivery/ack/'+receipt,headers=delivery,json=ack).status_code==200
            final=api.get('/api/files/status/'+receipt,headers=sender)
            assert final.status_code==200 and final.json()['status']=='delivered'
            # This ACK exercises the backend protocol, NOT a real Windows publication.
            report['checks'].append('terminal_ack_and_status_idempotency')
            for device,secret in zip(devices,credentials):
                assert api.delete('/api/devices/'+device,headers=admin).status_code==200
                assert api.post('/api/heartbeat',headers={'Authorization':'Bearer '+secret},json={'pending':0,'error':None}).status_code==401
            assert api.post('/api/logout',headers=admin).status_code==200
            audit=(data/'audit.log').read_text(encoding='utf-8')
            assert all(value not in audit for value in credentials+codes+[password,login.json()['token']])
            report['checks'].append('revocation_and_no_plaintext_credentials_in_audit')
            target=data/'spool'/receipt
            if target.exists():
                assert target.is_file() and not target.is_symlink() and hashlib.sha256(target.read_bytes()).hexdigest()==digest
                target.unlink()
        report['temporary_devices']=devices
        report['synthetic_receipt']=receipt
        report['status']='passed'
        print(json.dumps(report))
    finally:
        for device in devices:db.execute('UPDATE devices SET enabled=0 WHERE id=?',(device,))
        db.execute('DELETE FROM sessions WHERE username=?',(label,))
        db.execute('DELETE FROM users WHERE username=?',(label,));db.commit();db.close()


if __name__=='__main__':main()
