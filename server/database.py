"""Short-lived SQLite connections; transactions serialize account invariants."""
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


def utcnow():
    return datetime.now(timezone.utc).isoformat()


class Database:
    def __init__(self, data_dir):
        self.data_dir = Path(data_dir).resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.data_dir / 'tracer.sqlite3'
        with self.connect() as c:
            c.execute('PRAGMA journal_mode=WAL')
            c.executescript('''
            CREATE TABLE IF NOT EXISTS users(username TEXT PRIMARY KEY, password_hash TEXT NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('admin','operator')), enabled INTEGER NOT NULL DEFAULT 1);
            CREATE TABLE IF NOT EXISTS sessions(token_hash TEXT PRIMARY KEY, username TEXT NOT NULL,
                expires_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS devices(id TEXT PRIMARY KEY, name TEXT NOT NULL, token_hash TEXT NOT NULL UNIQUE,
                enabled INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, last_seen TEXT,
                pending INTEGER NOT NULL DEFAULT 0, error TEXT);
            CREATE TABLE IF NOT EXISTS files(device_id TEXT NOT NULL, name TEXT COLLATE NOCASE NOT NULL, sha256 TEXT NOT NULL,
                size INTEGER NOT NULL, received_at TEXT NOT NULL, UNIQUE(device_id,name,sha256));
            CREATE TABLE IF NOT EXISTS activations(
                id TEXT PRIMARY KEY, code_hash TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('sender','delivery')), created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL, revoked INTEGER NOT NULL DEFAULT 0,
                device_id TEXT UNIQUE, used_at TEXT);
            CREATE TABLE IF NOT EXISTS deliveries(
                id TEXT PRIMARY KEY, device_id TEXT NOT NULL, name TEXT COLLATE NOCASE NOT NULL,
                sha256 TEXT NOT NULL, size INTEGER NOT NULL, received_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','delivered','conflict','uncertain')),
                error TEXT, lease_holder TEXT, lease_hash TEXT, lease_until TEXT,
                ack_hash TEXT, ack_holder TEXT, ack_result TEXT, ack_error TEXT,
                UNIQUE(device_id,name,sha256));
            CREATE INDEX IF NOT EXISTS deliveries_pending ON deliveries(status,received_at);
            CREATE INDEX IF NOT EXISTS deliveries_name ON deliveries(name COLLATE NOCASE);
            ''')
            columns = {r[1] for r in c.execute('PRAGMA table_info(devices)')}
            for name, definition in (
                ('role', "TEXT NOT NULL DEFAULT 'sender'"),
                ('activation_id', 'TEXT'), ('installation_id', 'TEXT')):
                if name not in columns:
                    c.execute(f'ALTER TABLE devices ADD COLUMN {name} {definition}')
            c.execute('CREATE UNIQUE INDEX IF NOT EXISTS devices_installation ON devices(installation_id)')
            # Old persisted human sessions are deliberately invalid after startup.
            c.execute('DELETE FROM sessions')

    @contextmanager
    def connect(self, write=False):
        c = sqlite3.connect(self.path, timeout=30)
        c.row_factory = sqlite3.Row
        try:
            c.execute('PRAGMA foreign_keys=ON')
            c.execute('PRAGMA busy_timeout=30000')
            if write:
                c.execute('BEGIN IMMEDIATE')
            yield c
            c.commit()
        except BaseException:
            c.rollback()
            raise
        finally:
            c.close()
