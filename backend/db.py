import os
import json
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path

PATH = Path(os.getenv('DATABASE_PATH', '/data/nvr.sqlite3'))

@contextmanager
def db():
    PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute('PRAGMA busy_timeout=10000')
        yield conn
        conn.commit()
    finally:
        conn.close()

def init():
    PATH.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(PATH, os.O_CREAT | os.O_RDWR, 0o600)
    os.close(fd)
    os.chmod(PATH, 0o600)
    with db() as c:
        c.execute('PRAGMA journal_mode=WAL')
        c.executescript('''
        CREATE TABLE IF NOT EXISTS cameras (
          id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, rtsp_url TEXT NOT NULL,
          username TEXT, password TEXT, enabled INTEGER NOT NULL DEFAULT 1,
          recording_enabled INTEGER NOT NULL DEFAULT 1,
          recording_destination TEXT NOT NULL DEFAULT 'default',
          retention_days INTEGER, substream_url TEXT, description TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS segments (
          id INTEGER PRIMARY KEY, camera_id INTEGER NOT NULL, path TEXT NOT NULL UNIQUE,
          started_at TEXT NOT NULL, ended_at TEXT NOT NULL, size_bytes INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS segments_camera_date ON segments(camera_id, started_at);
        CREATE TABLE IF NOT EXISTS layouts (name TEXT PRIMARY KEY, payload TEXT NOT NULL);
        ''')
        # Preserve IDs across deletion: footage and layouts outlive camera rows.
        schema = c.execute("SELECT sql FROM sqlite_master WHERE name='cameras'").fetchone()[0]
        if 'AUTOINCREMENT' not in schema.upper():
            c.execute('BEGIN')
            c.execute(schema.replace('CREATE TABLE cameras', 'CREATE TABLE cameras_new', 1)
                      .replace('id INTEGER PRIMARY KEY', 'id INTEGER PRIMARY KEY AUTOINCREMENT', 1))
            c.execute('INSERT INTO cameras_new SELECT * FROM cameras')
            c.execute('DROP TABLE cameras')
            c.execute('ALTER TABLE cameras_new RENAME TO cameras')
        columns = {row[1] for row in c.execute('PRAGMA table_info(cameras)')}
        if 'key' not in columns:
            c.execute('ALTER TABLE cameras ADD COLUMN key TEXT')
        for row in c.execute("SELECT id FROM cameras WHERE key IS NULL OR key=''").fetchall():
            c.execute('UPDATE cameras SET key=? WHERE id=?', (uuid.uuid4().hex, row['id']))
        c.executescript("""
        CREATE UNIQUE INDEX IF NOT EXISTS cameras_key ON cameras(key);
        CREATE TRIGGER IF NOT EXISTS cameras_assign_key AFTER INSERT ON cameras
        WHEN NEW.key IS NULL OR NEW.key=''
        BEGIN UPDATE cameras SET key=lower(hex(randomblob(16))) WHERE id=NEW.id; END;
        """)
        if 'recording_schedule' not in columns:
            c.execute('ALTER TABLE cameras ADD COLUMN recording_schedule TEXT')
        c.executescript('''
        CREATE TABLE IF NOT EXISTS destinations (name TEXT PRIMARY KEY, expected_marker TEXT);
        CREATE TABLE IF NOT EXISTS alert_states (
          key TEXT PRIMARY KEY, failed_since REAL, notified INTEGER NOT NULL DEFAULT 0,
          last_failure REAL NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS webhook_events (
          id TEXT PRIMARY KEY, payload TEXT NOT NULL, created_at REAL NOT NULL,
          attempts INTEGER NOT NULL DEFAULT 0, next_attempt REAL NOT NULL,
          delivered_at REAL, failed INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS webhook_pending ON webhook_events(delivered_at, failed, next_attempt);
        ''')
        root = Path(os.getenv('DEFAULT_RECORDING_PATH', '/recordings')).resolve()
        archive_ids = [int(p.name) for p in root.glob('*/*') if p.name.isdecimal()]
        layout_ids = [tile['camera_id'] for row in c.execute('SELECT payload FROM layouts')
                      for tile in json.loads(row[0]).get('tiles', [])
                      if type(tile.get('camera_id')) is int]
        highest = max([0, *archive_ids, *layout_ids,
                       c.execute('SELECT COALESCE(MAX(camera_id),0) FROM segments').fetchone()[0],
                       c.execute('SELECT COALESCE(MAX(id),0) FROM cameras').fetchone()[0]])
        c.execute("INSERT INTO sqlite_sequence(name,seq) SELECT 'cameras',? WHERE NOT EXISTS (SELECT 1 FROM sqlite_sequence WHERE name='cameras')", (highest,))
        c.execute("UPDATE sqlite_sequence SET seq=MAX(seq,?) WHERE name='cameras'", (highest,))
