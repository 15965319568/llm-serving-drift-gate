"""Independent persistent router used by the CPU replay worker."""
import json
import sqlite3
import threading

from .errors import APIError
from .validation import canonical, integer, object_value, string


class RouterBackend:
    def __init__(self, state):
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(str(state), check_same_thread=False)
        self.conn.execute("CREATE TABLE IF NOT EXISTS router (id INTEGER PRIMARY KEY, revision INTEGER, epoch INTEGER, routes TEXT, apply_count INTEGER)")
        self.conn.execute("INSERT OR IGNORE INTO router VALUES(1,0,0,'{}',0)")
        self.conn.execute("CREATE TABLE IF NOT EXISTS receipts (action_id TEXT PRIMARY KEY, body TEXT, receipt TEXT)")
        self.conn.execute("CREATE TABLE IF NOT EXISTS calls (id INTEGER PRIMARY KEY, request_id TEXT, attempt INTEGER, model_version TEXT)")
        self.conn.commit()
        self.faults = {"drop_ack_once": False, "fail_before_apply_once": False}

    def state(self):
        with self.lock:
            row = self.conn.execute("SELECT revision,epoch,routes,apply_count FROM router WHERE id=1").fetchone()
            return {"revision": row[0], "epoch": row[1], "routes": json.loads(row[2]), "apply_count": row[3]}

    def fence(self, body):
        epoch = integer(body.get("epoch"), "epoch")
        with self.lock, self.conn:
            if epoch < self.state()["epoch"]: raise APIError(409, "stale_epoch")
            self.conn.execute("UPDATE router SET epoch=? WHERE id=1", (epoch,))
            return self.state()

    def apply(self, body):
        string(body.get("action_id"), "action_id")
        integer(body.get("epoch"), "epoch")
        integer(body.get("expected_revision"), "expected_revision")
        routes = object_value(body.get("routes"), "routes")
        for tenant, pct in routes.items():
            string(tenant, "tenant"); integer(pct, "route_pct", 0, 100)
        with self.lock, self.conn:
            prior = self.conn.execute("SELECT body,receipt FROM receipts WHERE action_id=?", (body["action_id"],)).fetchone()
            if prior:
                if json.loads(prior[0]) != body: raise APIError(409, "action_conflict")
                return json.loads(prior[1]), False
            if self.faults["fail_before_apply_once"]:
                self.faults["fail_before_apply_once"] = False
                raise APIError(503, "temporary_unavailable")
            current = self.state()
            if body["epoch"] < current["epoch"] or body["expected_revision"] != current["revision"]:
                raise APIError(409, "router_conflict")
            receipt = {"action_id": body["action_id"], "state": "APPLIED", "revision": current["revision"] + 1, "routes": routes}
            self.conn.execute("UPDATE router SET revision=?,epoch=?,routes=?,apply_count=apply_count+1 WHERE id=1",
                              (receipt["revision"], body["epoch"], canonical(routes)))
            self.conn.execute("INSERT INTO receipts VALUES (?,?,?)", (body["action_id"], canonical(body), canonical(receipt)))
            drop = self.faults["drop_ack_once"]
            self.faults["drop_ack_once"] = False
            return receipt, drop

    def receipt(self, action_id):
        with self.lock:
            row = self.conn.execute("SELECT receipt FROM receipts WHERE action_id=?", (action_id,)).fetchone()
            if not row: raise APIError(404, "action_not_found")
            return json.loads(row[0])

    def record_call(self, body):
        with self.lock, self.conn:
            self.conn.execute("INSERT INTO calls(request_id,attempt,model_version) VALUES(?,?,?)",
                              (body["request_id"], body["attempt"], body["model_version"]))

    def stats(self):
        with self.lock:
            calls = [{"request_id": r[0], "attempt": r[1], "model_version": r[2]}
                     for r in self.conn.execute("SELECT request_id,attempt,model_version FROM calls ORDER BY id")]
            return {"calls": calls, "router": self.state()}
