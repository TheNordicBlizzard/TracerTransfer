"""Root-only Linux half of cutover; preserve both generations on rollback.

Windows entry must already be frozen. Public connector stays stopped until the
controller verifies candidate readiness. Never roll back over new receipts.
"""
import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import time
import urllib.request

sys.path.insert(0, '/opt/tracer-transfer')


def merge_legacy(source, candidate):
    from server.database import Database
    database = Database(candidate)
    with closing(sqlite3.connect(source.as_uri()+'?mode=ro', uri=True)) as old:
        old.row_factory = sqlite3.Row
        if old.execute('PRAGMA quick_check').fetchall()[0][0] != 'ok':
            raise ValueError('Legacy database failed integrity')
        if old.execute('SELECT count(*) FROM devices WHERE enabled=1').fetchone()[0]:
            raise ValueError('Active legacy clients require explicit migration')
        counts = {'users': 0, 'devices': 0, 'files': 0}
        with database.connect(write=True) as new:
            for row in old.execute('SELECT * FROM users'):
                current = new.execute('SELECT password_hash,role,enabled FROM users WHERE username=?', (row['username'],)).fetchone()
                if current and tuple(current) != (row['password_hash'], row['role'], row['enabled']):
                    raise ValueError('Conflicting human account; never choose credentials silently')
                if not current:
                    new.execute('INSERT INTO users(username,password_hash,role,enabled) VALUES(?,?,?,?)',
                                tuple(row[k] for k in ('username','password_hash','role','enabled')))
                counts['users'] += 1
            columns = ('id','name','token_hash','enabled','created_at','last_seen','pending','error')
            for row in old.execute('SELECT * FROM devices'):
                current = new.execute('SELECT activation_id FROM devices WHERE id=?', (row['id'],)).fetchone()
                if current and current['activation_id'] is not None:
                    raise ValueError('Activated identity collision')
                new.execute('INSERT INTO devices('+','.join(columns)+') VALUES('+','.join('?' for _ in columns)+') '
                            'ON CONFLICT(id) DO UPDATE SET '+','.join(k+'=excluded.'+k for k in columns[1:]),
                            tuple(row[k] for k in columns))
                counts['devices'] += 1
            for row in old.execute('SELECT * FROM files'):
                current = new.execute('SELECT size FROM files WHERE device_id=? AND name=? COLLATE NOCASE AND sha256=?',
                                      (row['device_id'], row['name'], row['sha256'])).fetchone()
                if current and current['size'] != row['size']:
                    raise ValueError('Conflicting file history')
                new.execute('INSERT OR IGNORE INTO files(device_id,name,sha256,size,received_at) VALUES(?,?,?,?,?)',
                            tuple(row[k] for k in ('device_id','name','sha256','size','received_at')))
                counts['files'] += 1
        return counts


def authorization_fingerprint(connection):
    tables = [
        ('users','username,password_hash,role,enabled','username'),
        ('devices','id,token_hash,enabled,role,activation_id,installation_id','id'),
        ('activations','id,code_hash,role,expires_at,revoked,device_id','id'),
    ]
    records = {table: [list(row) for row in connection.execute(
        'SELECT '+columns+' FROM '+table+' ORDER BY '+order)]
        for table,columns,order in tables}
    return hashlib.sha256(json.dumps(records,sort_keys=True).encode()).hexdigest()


def ensure_rollback_safe(connection, state):
    if connection.execute('SELECT count(*) FROM deliveries').fetchone()[0] != state['baseline_deliveries']:
        raise RuntimeError('New receipts require reconciliation; both ingress paths remain stopped')
    if authorization_fingerprint(connection) != state.get('baseline_authorization'):
        raise RuntimeError('Changed accounts/codes/identities require reconciliation; preserve both generations')


def run(*arguments):
    subprocess.run(arguments, check=True, capture_output=True)


def ready():
    for _ in range(40):
        try:
            with urllib.request.urlopen('http://127.0.0.1:8765/health', timeout=2) as response:
                if json.load(response) == {'ok': True}:
                    return
        except (OSError, ValueError):
            pass
        time.sleep(.25)
    raise RuntimeError('Backend readiness failed')


def save(state_dir, record):
    temporary = state_dir/'state.tmp'
    temporary.write_text(json.dumps(record), encoding='utf-8')
    temporary.replace(state_dir/'state.json')


def start_private():
    run('systemctl','start','tracer-transfer.service')
    ready()
    run('systemctl','start','tracer-proxy.service','tracer-maintenance.timer')


def rollback(state_dir):
    state = json.loads((state_dir/'state.json').read_text())
    subprocess.run(['systemctl','disable','--now','tracer-cloudflared.service'], check=True, capture_output=True)
    for unit in ('tracer-maintenance.timer','tracer-maintenance.service','tracer-proxy.service','tracer-transfer.service'):
        run('systemctl','stop',unit)
    live = Path('/var/lib/tracer-transfer')
    original = state_dir/'original-data'
    if original.exists() and not live.exists():
        original.rename(live)
    elif original.exists():
        with closing(sqlite3.connect((live/'tracer.sqlite3').as_uri()+'?mode=ro', uri=True)) as db:
            # Any receipt is a potential external promise: require reconciliation,
            # even if terminal. Never switch old ingress on over newer receipts.
            ensure_rollback_safe(db, state)
        live.rename(state_dir/'preserved-new-data')
        original.rename(live)
    start_private()
    state['phase'] = 'rolled-back'
    save(state_dir, state)
    return {'rolled_back': True, 'new_generation_preserved': (state_dir/'preserved-new-data').exists()}


def prepare(state_dir, digest):
    from server.maintenance import backup, restore
    archive = state_dir/'incoming.tar.gz'
    if (state_dir/'state.json').exists():
        raise ValueError('Checkpoint already used')
    with archive.open('rb') as stream:
        if hashlib.file_digest(stream,'sha256').hexdigest() != digest:
            raise ValueError('Transport hash mismatch')
    if subprocess.run(['systemctl','is-active','--quiet','tracer-cloudflared.service']).returncode == 0:
        raise ValueError('New connector must remain stopped')
    live = Path('/var/lib/tracer-transfer')
    with closing(sqlite3.connect((live/'tracer.sqlite3').as_uri()+'?mode=ro', uri=True)) as db:
        if db.execute('SELECT count(*) FROM devices WHERE enabled=1').fetchone()[0]:
            raise ValueError('Existing activated identities require review')
        if db.execute("SELECT count(*) FROM deliveries WHERE status!='delivered'").fetchone()[0]:
            raise ValueError('Existing open queue requires reconciliation')
    save(state_dir, {'phase':'preparing'})
    for unit in ('tracer-maintenance.timer','tracer-maintenance.service','tracer-proxy.service','tracer-transfer.service'):
        run('systemctl','stop',unit)
    try:
        snapshot = backup(live, state_dir/'backup', keep=1)
        candidate = restore(snapshot, state_dir/'candidate')
        incoming = state_dir/'windows'
        incoming.mkdir(mode=0o700)
        with tarfile.open(archive, 'r:gz') as tar:
            members = tar.getmembers()
            if sum(m.size for m in members)*3 + 256*1024**2 > shutil.disk_usage(state_dir).free:
                raise ValueError('Insufficient candidate space')
            for member in members:
                name = PurePosixPath(member.name)
                if name.is_absolute() or '..' in name.parts or not (member.isfile() or member.isdir()):
                    raise ValueError('Unsafe incoming archive')
                if not name.parts or name.parts[0] not in ('tracer.sqlite3','received'):
                    raise ValueError('Unexpected archive payload')
            tar.extractall(incoming, filter='data')
        counts = merge_legacy(incoming/'tracer.sqlite3', candidate)
        if (incoming/'received').exists():
            shutil.copytree(incoming/'received', candidate/'received', dirs_exist_ok=True)
        (candidate/'.staging-deployment').unlink(missing_ok=True)
        with closing(sqlite3.connect(candidate/'tracer.sqlite3')) as db:
            if db.execute('PRAGMA quick_check').fetchall() != [('ok',)]:
                raise ValueError('Candidate integrity failed')
            baseline = db.execute('SELECT count(*) FROM deliveries').fetchone()[0]
            baseline_authorization = authorization_fingerprint(db)
        import pwd
        owner = pwd.getpwnam('tracer')
        for item in [candidate, *candidate.rglob('*')]:
            if item.is_symlink():
                raise ValueError('Candidate link forbidden')
            os.chown(item, owner.pw_uid, owner.pw_gid)
        save(state_dir, {'phase':'ready-to-swap','counts':counts,'baseline_deliveries':baseline,'baseline_authorization':baseline_authorization})
        live.rename(state_dir/'original-data')
        candidate.rename(live)
        start_private()
        save(state_dir, {'phase':'installed','counts':counts,'baseline_deliveries':baseline,'baseline_authorization':baseline_authorization})
        return {'installed':True,'legacy_imported':counts,'new_connector_started':False}
    except BaseException:
        rollback(state_dir)
        raise


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['prepare','rollback','commit'])
    parser.add_argument('--state-dir', required=True, type=Path)
    parser.add_argument('--sha256')
    args = parser.parse_args()
    if os.geteuid()!=0 or Path('/etc/hostname').read_text().strip()!='tracer-transfer':
        raise PermissionError('Authorized guest root only')
    if not re.fullmatch(r'/var/lib/tracer-cutover-[a-f0-9]{12}',str(args.state_dir)):
        raise ValueError('Checkpoint namespace invalid')
    for ancestor in [args.state_dir,*args.state_dir.parents]:
        if ancestor.is_symlink():
            raise ValueError('Checkpoint link forbidden')
    os.umask(0o077)
    args.state_dir.mkdir(mode=0o700,exist_ok=True)
    if args.action=='prepare':
        if not args.sha256 or not re.fullmatch('[a-f0-9]{64}',args.sha256):
            raise ValueError('Exact archive SHA256 required')
        result=prepare(args.state_dir,args.sha256)
    elif args.action=='rollback':
        result=rollback(args.state_dir)
    else:
        state=json.loads((args.state_dir/'state.json').read_text())
        if state['phase']!='installed':raise ValueError('Candidate not installed')
        run('systemctl','is-active','--quiet','tracer-cloudflared.service')
        state['phase']='committed';save(args.state_dir,state)
        result={'committed':True}
    print(json.dumps(result))


if __name__=='__main__':
    main()
