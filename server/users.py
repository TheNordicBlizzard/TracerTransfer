"""Account operations shared by local bootstrap and authenticated API."""
import re
from argon2 import PasswordHasher, Type
from fastapi import HTTPException

HASHER = PasswordHasher(type=Type.ID)


def validate_account(username, password=None, role=None):
    if not isinstance(username, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}', username):
        raise HTTPException(422, 'Invalid username')
    if password is not None and (not isinstance(password, str) or not 12 <= len(password) <= 1024):
        raise HTTPException(422, 'Password must contain 12 to 1024 characters')
    if role is not None and role not in ('admin', 'operator'):
        raise HTTPException(422, 'Invalid role')


def bootstrap_admin(db, username, password):
    validate_account(username, password, 'admin')
    hashed = HASHER.hash(password)
    with db.connect(write=True) as c:
        if c.execute('SELECT 1 FROM users LIMIT 1').fetchone():
            raise ValueError('Already initialized; use local recovery with server stopped')
        c.execute('INSERT INTO users(username,password_hash,role) VALUES(?,?,?)', (username, hashed, 'admin'))


def recover_admin(db, username, password):
    """Local GUI caller must hold the process lock before invoking recovery."""
    validate_account(username, password, 'admin')
    with db.connect(write=True) as c:
        c.execute('INSERT INTO users(username,password_hash,role,enabled) VALUES(?,?,?,1) '
                  'ON CONFLICT(username) DO UPDATE SET password_hash=excluded.password_hash, role=excluded.role, enabled=1',
                  (username, HASHER.hash(password), 'admin'))
        c.execute('DELETE FROM sessions')
