"""Root-only creation of a NEW human administrator via encrypted SSH stdin.

Never resets/overwrites an existing account. Passwords are not accepted on argv.
"""
import argparse
import json
import os
from pathlib import Path
import sqlite3
import sys


def main():
    p=argparse.ArgumentParser()
    p.add_argument('operation',choices=['check','create'])
    p.add_argument('--username',default='admin')
    args=p.parse_args()
    if os.name!='posix' or os.geteuid()!=0 or Path('/etc/hostname').read_text().strip()!='tracer-transfer':
        raise PermissionError('Authorized VM root only')
    sys.path.insert(0,'/opt/tracer-transfer')
    from server.users import HASHER,validate_account
    dbpath=Path('/var/lib/tracer-transfer/tracer.sqlite3')
    db=sqlite3.connect(dbpath.as_uri()+'?mode=rw',uri=True,timeout=10)
    try:
        if args.operation=='check':
            validate_account(args.username)
            available=not db.execute('SELECT 1 FROM users WHERE username=?',(args.username,)).fetchone()
            print(json.dumps({'ready':True,'username_available':available}));return 0
        raw=sys.stdin.read(8193)
        if len(raw)>8192:raise ValueError('Input too large')
        payload=json.loads(raw)
        if not isinstance(payload,dict) or set(payload)!={'username','password'}:raise ValueError('Invalid input')
        validate_account(payload['username'],payload['password'],'admin')
        hashed=HASHER.hash(payload['password'])
        payload['password']=None;raw=None
        db.execute('BEGIN IMMEDIATE')
        if db.execute('SELECT 1 FROM users WHERE username=?',(payload['username'],)).fetchone():
            raise FileExistsError('Account exists; use authenticated password change or controlled recovery')
        db.execute("INSERT INTO users(username,password_hash,role,enabled) VALUES(?,?,'admin',1)",(payload['username'],hashed))
        db.commit()
        row=db.execute('SELECT role,enabled FROM users WHERE username=?',(payload['username'],)).fetchone()
        if row!=('admin',1):raise RuntimeError('Read-back failed')
        print(json.dumps({'created':True,'username':payload['username']}));return 0
    finally:db.close()


if __name__=='__main__':
    try:raise SystemExit(main())
    except Exception as exc:
        print(json.dumps({'ok':False,'error':type(exc).__name__}),file=sys.stderr)
        raise SystemExit(1)
