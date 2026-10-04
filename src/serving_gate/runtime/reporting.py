"""Read-only exports deliberately project out prompts and routing credentials."""
import json
from pathlib import Path
import sqlite3

from .actions import action_view

REQUEST_FIELDS = ("request_id", "tenant", "model_version", "route_revision", "status", "attempts",
                  "created_ms", "finished_ms", "output_tokens", "error")


def snapshot(conn):
    meta = {r[0]: json.loads(r[1]) for r in conn.execute("SELECT key,value FROM meta")}
    return {"schema_version": 2, "now_ms": meta["clock"],
            "requests": [{key: row[key] for key in REQUEST_FIELDS} for row in conn.execute("SELECT * FROM requests ORDER BY request_id")],
            "invoices": [dict(row) for row in conn.execute("SELECT * FROM invoices ORDER BY request_id")],
            "routing": meta["routing"],
            "actions": [action_view(dict(row)) for row in conn.execute("SELECT * FROM actions ORDER BY action_id")]}


def live_snapshot(store):
    with store.lock: return snapshot(store.conn)


def export_state(state, output):
    connection = sqlite3.connect(Path(state).resolve().as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        result = snapshot(connection)
    finally:
        connection.close()
    directory = Path(output)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "runtime_snapshot.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result
