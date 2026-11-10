"""Offline merge safety only; never contacts/stops any real machine."""
import importlib.util
from pathlib import Path
import sqlite3

import pytest
from server.database import Database

spec = importlib.util.spec_from_file_location('migration_data',Path(__file__).resolve().parents[1]/'deploy/linux/migrate_data.py')
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)


def fixtures(tmp_path):
    old = Database(tmp_path/'old')
    new = Database(tmp_path/'new')
    with old.connect(write=True) as db:
        db.execute("INSERT INTO users VALUES('admin','synthetic-hash','admin',1)")
        db.execute("INSERT INTO devices(id,name,token_hash,enabled,created_at) VALUES('old','Legacy','synthetic-token-hash',0,'2026-01-01')")
        db.execute("INSERT INTO files VALUES('old','original.oma',?,4,'2026-01-01')",('a'*64,))
    with new.connect(write=True) as db:
        db.execute("INSERT INTO users VALUES('new-human','new-synthetic-hash','admin',1)")
    return old,new


def test_merge_preserves_new_accounts_legacy_history_and_provenance(tmp_path):
    old,new=fixtures(tmp_path)
    result=migration.merge_legacy(old.path,new.data_dir)
    assert result=={'users':1,'devices':1,'files':1}
    with new.connect() as db:
        assert db.execute('SELECT count(*) FROM users').fetchone()[0]==2
        row=db.execute("SELECT enabled,activation_id FROM devices WHERE id='old'").fetchone()
        assert tuple(row)==(0,None)
        assert db.execute('SELECT count(*) FROM deliveries').fetchone()[0]==0
    migration.merge_legacy(old.path,new.data_dir)
    with new.connect() as db:assert db.execute('SELECT count(*) FROM files').fetchone()[0]==1


def test_conflicting_password_aborts_without_overwriting(tmp_path):
    old,new=fixtures(tmp_path)
    with new.connect(write=True) as db:db.execute("INSERT INTO users VALUES('admin','different','admin',1)")
    with pytest.raises(ValueError,match='Conflicting human'):
        migration.merge_legacy(old.path,new.data_dir)
    with new.connect() as db:
        assert db.execute("SELECT password_hash FROM users WHERE username='admin'").fetchone()[0]=='different'
        assert db.execute('SELECT count(*) FROM files').fetchone()[0]==0


def test_active_legacy_client_blocks_automatic_cutover(tmp_path):
    old,new=fixtures(tmp_path)
    with old.connect(write=True) as db:db.execute("UPDATE devices SET enabled=1")
    with pytest.raises(ValueError,match='Active legacy'):
        migration.merge_legacy(old.path,new.data_dir)
    with new.connect() as db:assert db.execute('SELECT count(*) FROM devices').fetchone()[0]==0


def test_activated_identity_collision_aborts_transaction(tmp_path):
    old,new=fixtures(tmp_path)
    with new.connect(write=True) as db:
        db.execute("INSERT INTO devices(id,name,token_hash,enabled,created_at,activation_id) VALUES('old','Activated','another',0,'2026','activation')")
    with pytest.raises(ValueError,match='identity collision'):
        migration.merge_legacy(old.path,new.data_dir)
    with new.connect() as db:
        assert db.execute("SELECT name FROM devices WHERE id='old'").fetchone()[0]=='Activated'
        assert not db.execute("SELECT 1 FROM users WHERE username='admin'").fetchone()


@pytest.mark.parametrize('change', ['receipt','password','activation'])
def test_rollback_refuses_new_promises_or_changed_authorization(tmp_path,change):
    _,new=fixtures(tmp_path)
    with new.connect(write=True) as db:
        state={'baseline_deliveries':0,'baseline_authorization':migration.authorization_fingerprint(db)}
        migration.ensure_rollback_safe(db,state)
        if change=='receipt':
            db.execute("INSERT INTO deliveries(id,device_id,name,sha256,size,received_at) VALUES('receipt','sender','new.oma',?,1,'2026')",('a'*64,))
        elif change=='password':
            db.execute("UPDATE users SET password_hash='changed' WHERE username='new-human'")
        else:
            db.execute("INSERT INTO activations(id,code_hash,name,role,created_at,expires_at) VALUES('new','synthetic-code-hash','New code','sender','2026','2027')")
        with pytest.raises(RuntimeError,match='reconciliation'):
            migration.ensure_rollback_safe(db,state)


def test_rollback_security_fingerprint_ignores_heartbeat_metadata(tmp_path):
    old,new=fixtures(tmp_path)
    migration.merge_legacy(old.path,new.data_dir)
    with new.connect(write=True) as db:
        state={'baseline_deliveries':0,'baseline_authorization':migration.authorization_fingerprint(db)}
        db.execute("UPDATE devices SET last_seen='new-time',pending=2,error='transient' WHERE id='old'")
        migration.ensure_rollback_safe(db,state)
