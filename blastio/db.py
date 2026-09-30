"""One durable local SQLite store; network filesystems and multiple hosts unsupported."""
import json
import sqlite3
import os
from pathlib import Path
from contextlib import contextmanager
from datetime import datetime, timezone
from .config import settings

def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')

def connect():
    Path(settings.db_path).parent.mkdir(parents=True, exist_ok=True,mode=0o700)
    conn = sqlite3.connect(settings.db_path, timeout=15, isolation_level=None)
    if os.name=='posix':os.chmod(settings.db_path,0o600)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys=ON')
    conn.execute('PRAGMA synchronous=FULL')
    conn.execute('PRAGMA busy_timeout=15000')
    return conn

@contextmanager
def transaction():
    conn = connect()
    try:
        conn.execute('BEGIN IMMEDIATE')
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()

def init_db():
    conn = connect()
    try:
        conn.execute('PRAGMA journal_mode=WAL')
        conn.execute('PRAGMA synchronous=FULL')
        migration = Path(__file__).resolve().parent.parent / 'migrations' / '001_initial.sql'
        conn.executescript(migration.read_text())
    finally:
        conn.close()

def audit(conn, actor, action, entity_type, entity_id, detail=None):
    conn.execute('INSERT INTO audit_log(actor,action,entity_type,entity_id,detail,created_at) VALUES(?,?,?,?,?,?)',
                 (str(actor), action, entity_type, str(entity_id), json.dumps(detail or {}, ensure_ascii=False), now()))

def get_setting(conn, key, default=None):
    row = conn.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
    return json.loads(row['value']) if row else default

def set_setting(conn, key, value):
    conn.execute('INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (key, json.dumps(value)))
