"""Transactional upgrades from the documented v1 on-disk input format."""
import json
import sqlite3


REQUESTS_V1 = """
CREATE TABLE IF NOT EXISTS requests (
 request_id TEXT PRIMARY KEY, tenant TEXT NOT NULL, key TEXT NOT NULL,
 fingerprint TEXT NOT NULL, prompt TEXT NOT NULL, max_tokens INTEGER NOT NULL,
 deadline_ms INTEGER, model_version TEXT NOT NULL, status TEXT NOT NULL,
 created_ms INTEGER NOT NULL, finished_ms INTEGER, attempts INTEGER NOT NULL DEFAULT 0,
 output_tokens INTEGER NOT NULL DEFAULT 0, input_tokens INTEGER NOT NULL DEFAULT 0,
 UNIQUE(tenant,key)
);
"""

TABLES = [
    "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS events (request_id TEXT, seq INTEGER, kind TEXT, data TEXT, PRIMARY KEY(request_id,seq))",
    "CREATE TABLE IF NOT EXISTS invoices (request_id TEXT PRIMARY KEY, input_tokens INTEGER, output_tokens INTEGER)",
    "CREATE TABLE IF NOT EXISTS telemetry (event_id TEXT PRIMARY KEY, payload TEXT NOT NULL)",
    """CREATE TABLE IF NOT EXISTS actions (
      action_id TEXT PRIMARY KEY, body TEXT NOT NULL, routes TEXT NOT NULL,
      state TEXT NOT NULL, revision INTEGER, error TEXT, created_ms INTEGER NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS requests_schedule ON requests(status,created_ms,request_id)",
]


def migrate(conn, config):
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version not in (0, 1, 2):
        raise ValueError("unsupported state version")
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(REQUESTS_V1)
        columns = {row[1] for row in conn.execute("PRAGMA table_info(requests)")}
        if "route_revision" not in columns and version != 1:
            conn.execute("ALTER TABLE requests ADD COLUMN route_revision INTEGER NOT NULL DEFAULT 0")
        if "error" not in columns:
            conn.execute("ALTER TABLE requests ADD COLUMN error TEXT")
        for statement in TABLES:
            conn.execute(statement)
        defaults = {
            "clock": config["clock_start_ms"],
            "routing": {"revision": 0, "routes": {t: 0 for t in config["tenants"]}},
            "lease": {"holder": "", "epoch": 0, "expires_ms": 0},
        }
        for key, value in defaults.items():
            conn.execute("INSERT OR IGNORE INTO meta VALUES (?,?)", (key, json.dumps(value)))
        conn.execute("PRAGMA user_version=2")
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def connect(path):
    conn = sqlite3.connect(str(path), timeout=10, check_same_thread=False, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=FULL")
    return conn
