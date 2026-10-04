"""Safe public views of durable outbox entries."""
import json

from .errors import APIError


def raw_action(store, action_id):
    row = store.conn.execute("SELECT * FROM actions WHERE action_id=?", (action_id,)).fetchone()
    return dict(row) if row else None


def action_view(row):
    body = json.loads(row["body"])
    return {"action_id": row["action_id"], "state": row["state"], "operation": body["operation"],
            "scenario_id": body["scenario_id"], "epoch": body["epoch"],
            "expected_revision": body["expected_revision"], "routes": json.loads(row["routes"]),
            "revision": row["revision"], "error": row["error"]}


def get_action(store, action_id):
    with store.lock:
        row = raw_action(store, action_id)
        if row is None: raise APIError(404, "action_not_found")
        return action_view(row)
