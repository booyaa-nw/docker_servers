from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DB_PATH = Path(os.getenv("LOG_VIEW_DB", "/data/log_view.sqlite3"))
FTS_AVAILABLE = False


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


@contextmanager
def connection():
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def initialize() -> None:
    global FTS_AVAILABLE
    with connection() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS events (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          kind TEXT NOT NULL,
          received_at TEXT NOT NULL,
          event_at TEXT,
          source_ip TEXT,
          node_id INTEGER REFERENCES nodes(id) ON DELETE SET NULL,
          summary TEXT NOT NULL DEFAULT '',
          message TEXT NOT NULL DEFAULT '',
          parsed TEXT NOT NULL DEFAULT '{}',
          raw_bytes BLOB,
          raw_text TEXT,
          source_key TEXT UNIQUE
        );
        CREATE INDEX IF NOT EXISTS idx_events_received ON events(received_at DESC);
        CREATE INDEX IF NOT EXISTS idx_events_event_at ON events(event_at);
        CREATE INDEX IF NOT EXISTS idx_events_kind ON events(kind);
        CREATE INDEX IF NOT EXISTS idx_events_source ON events(source_ip);
        CREATE INDEX IF NOT EXISTS idx_events_node ON events(node_id);
        CREATE TABLE IF NOT EXISTS nodes (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          name TEXT NOT NULL,
          description TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS node_addresses (
          node_id INTEGER NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
          address TEXT NOT NULL UNIQUE,
          PRIMARY KEY(node_id, address)
        );
        CREATE TABLE IF NOT EXISTS groups (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          name TEXT NOT NULL UNIQUE,
          description TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS node_groups (
          node_id INTEGER NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
          group_id INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
          PRIMARY KEY(node_id, group_id)
        );
        CREATE TABLE IF NOT EXISTS settings (
          key TEXT PRIMARY KEY,
          value TEXT NOT NULL
        );
        """)
        try:
            db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS events_fts USING fts5(summary, message, content='events', content_rowid='id')")
            db.executescript("""
            CREATE TRIGGER IF NOT EXISTS events_ai AFTER INSERT ON events BEGIN
              INSERT INTO events_fts(rowid, summary, message) VALUES (new.id, new.summary, new.message);
            END;
            CREATE TRIGGER IF NOT EXISTS events_ad AFTER DELETE ON events BEGIN
              INSERT INTO events_fts(events_fts, rowid, summary, message) VALUES ('delete', old.id, old.summary, old.message);
            END;
            CREATE TRIGGER IF NOT EXISTS events_au AFTER UPDATE ON events BEGIN
              INSERT INTO events_fts(events_fts, rowid, summary, message) VALUES ('delete', old.id, old.summary, old.message);
              INSERT INTO events_fts(rowid, summary, message) VALUES (new.id, new.summary, new.message);
            END;
            """)
            FTS_AVAILABLE = True
        except sqlite3.OperationalError:
            # Basic substring search remains available on SQLite builds without FTS5.
            FTS_AVAILABLE = False


def node_id_for_ip(db: sqlite3.Connection, source_ip: str | None) -> int | None:
    if not source_ip:
        return None
    row = db.execute("SELECT node_id FROM node_addresses WHERE address=?", (source_ip,)).fetchone()
    return int(row[0]) if row else None


def insert_event(*, kind: str, summary: str, message: str, parsed: dict[str, Any] | None = None,
                 source_ip: str | None = None, event_at: str | None = None,
                 raw_bytes: bytes | None = None, raw_text: str | None = None,
                 source_key: str | None = None) -> int | None:
    with connection() as db:
        node_id = node_id_for_ip(db, source_ip)
        cur = db.execute("""INSERT OR IGNORE INTO events
          (kind, received_at, event_at, source_ip, node_id, summary, message, parsed, raw_bytes, raw_text, source_key)
          VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
          (kind, now_iso(), event_at, source_ip, node_id, summary, message,
           json.dumps(parsed or {}, ensure_ascii=False), raw_bytes, raw_text, source_key))
        return int(cur.lastrowid) if cur.rowcount else None


def refresh_node_links(db: sqlite3.Connection, node_id: int) -> None:
    db.execute("UPDATE events SET node_id=? WHERE source_ip IN (SELECT address FROM node_addresses WHERE node_id=?)", (node_id, node_id))


def already_imported(source_key: str) -> bool:
    with connection() as db:
        return db.execute("SELECT 1 FROM events WHERE source_key=?", (source_key,)).fetchone() is not None
