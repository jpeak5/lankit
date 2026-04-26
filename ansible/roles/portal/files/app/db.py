import contextlib
import sqlite3

DB_PATH = "/var/lib/lankit-portal/portal.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS bypass_log (
    id          INTEGER PRIMARY KEY,
    ip          TEXT NOT NULL,
    mac         TEXT,
    hostname    TEXT,
    duration_m  INTEGER NOT NULL,
    started_at  TEXT NOT NULL DEFAULT (datetime('now')),
    ended_at    TEXT,
    cancelled   INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS rename_log (
    id          INTEGER PRIMARY KEY,
    ip          TEXT NOT NULL,
    mac         TEXT,
    old_name    TEXT,
    new_name    TEXT NOT NULL,
    renamed_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS speed_results (
    id            INTEGER PRIMARY KEY,
    measured_at   TEXT NOT NULL DEFAULT (datetime('now')),
    download_mbps REAL,
    upload_mbps   REAL,
    ping_ms       REAL,
    error         TEXT
);
CREATE TABLE IF NOT EXISTS latency_log (
    id          INTEGER PRIMARY KEY,
    measured_at TEXT NOT NULL DEFAULT (datetime('now')),
    target      TEXT NOT NULL,
    rtt_ms      REAL
);
CREATE TABLE IF NOT EXISTS registrations (
    id                INTEGER PRIMARY KEY,
    mac               TEXT NOT NULL,
    ip                TEXT NOT NULL,
    requested_name    TEXT NOT NULL,
    requested_segment TEXT,
    status            TEXT NOT NULL DEFAULT 'pending',
    submitted_at      TEXT NOT NULL DEFAULT (datetime('now')),
    reviewed_at       TEXT,
    reviewed_by       TEXT
);
"""


@contextlib.contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def create_all():
    with get_db() as conn:
        conn.executescript(_SCHEMA)
