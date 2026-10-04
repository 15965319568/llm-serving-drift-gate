"""Durable prepare/apply/observe with lease fencing and receipt-first recovery."""
import json
import threading
from urllib.parse import quote

from .actions import action_view, get_action, raw_action
from .errors import APIError
from .gate_adapter import proposed_routes
from .transport import json_call
from .validation import canonical, integer, object_value, release_body, string


class Controller:
    def __init__(self, store):
        self.store = store
        self.lock = threading.RLock()

    def remote(self, method, path, body=None):
        return json_call(self.store.config["router_url"], method, path, body,
                         self.store.config["worker_timeout_ms"] / 1000)

    def lease(self, body):
        object_value(body)
        holder = string(body.get("holder"), "holder")
        ttl = integer(body.get("ttl_ms"), "ttl_ms", 1)
        with self.lock, self.store.transaction():
            current, now = self.store.meta("lease"), self.store.meta("clock")
            live = now < current["expires_ms"]
            if live and current["holder"] != holder: raise APIError(409, "lease_held")
            epoch = current["epoch"] if live else current["epoch"] + 1
            try:
                status, _ = self.remote("POST", "/router/fence", {"epoch": epoch})
                if status != 200: raise APIError(503, "router_unavailable")
            except (OSError, ValueError) as exc:
                raise APIError(503, "router_unavailable") from exc
            lease = {"holder": holder, "epoch": epoch, "expires_ms": now + ttl}
            self.store.set_meta("lease", lease)
            return lease

    def valid_owner(self, body):
        lease = self.store.meta("lease")
        return (lease["holder"] == body["holder"] and lease["epoch"] == body["epoch"]
                and self.store.meta("clock") < lease["expires_ms"])

    def submit(self, body):
        body = release_body(body)
        with self.lock:
            with self.store.transaction():
                existing = raw_action(self.store, body["action_id"])
                if existing:
                    if json.loads(existing["body"]) != body: raise APIError(409, "action_conflict")
                    return 200, action_view(existing)
                if not self.valid_owner(body): raise APIError(409, "lease_invalid")
                if self.store.meta("routing")["revision"] != body["expected_revision"]:
                    raise APIError(409, "revision_conflict")
                if self.store.conn.execute("SELECT 1 FROM actions WHERE state='PREPARED'").fetchone():
                    raise APIError(409, "pending_action")
                routes = proposed_routes(self.store, body)
                self.store.set_meta("routing", {"revision": body["expected_revision"] + 1, "routes": routes})
                self.store.conn.execute("INSERT INTO actions VALUES (?,?,?,'PREPARED',NULL,NULL,?)",
                    (body["action_id"], canonical(body), canonical(routes), self.store.meta("clock")))
            self.dispatch(body["action_id"])
            return 202, get_action(self.store, body["action_id"])

    def mark(self, action_id, state, error=None):
        with self.store.transaction():
            self.store.conn.execute("UPDATE actions SET state=?,error=? WHERE action_id=?", (state, error, action_id))

    def accept_receipt(self, row, receipt):
        body, routes = json.loads(row["body"]), json.loads(row["routes"])
        if (receipt.get("action_id") != row["action_id"] or receipt.get("state") != "APPLIED"
            or receipt.get("routes") != routes or receipt.get("revision") != body["expected_revision"] + 1):
            self.mark(row["action_id"], "ABORTED", "router_conflict")
            return
        with self.store.transaction():
            self.store.conn.execute("UPDATE actions SET state='APPLIED',revision=?,error=NULL WHERE action_id=?",
                                    (receipt["revision"], row["action_id"]))
            self.store.set_meta("routing", {"revision": receipt["revision"], "routes": routes})

    def dispatch(self, action_id):
        with self.lock:
            with self.store.lock: row = raw_action(self.store, action_id)
            if row["state"] != "PREPARED": return
            body, routes = json.loads(row["body"]), json.loads(row["routes"])
            wire = {key: body[key] for key in ("action_id", "epoch", "expected_revision")}
            wire["routes"] = routes
            try:
                status, receipt = self.remote("POST", "/router/apply", wire)
                if status == 200: self.accept_receipt(row, receipt)
                elif status == 409: self.mark(action_id, "ABORTED", "router_conflict")
                else: self.mark(action_id, "PREPARED", "router_unavailable")
            except (OSError, ValueError):
                self.mark(action_id, "PREPARED", "router_unavailable")

    def reconcile(self):
        with self.lock:
            with self.store.lock:
                pending = [dict(r) for r in self.store.conn.execute("SELECT * FROM actions WHERE state='PREPARED' ORDER BY created_ms,action_id")]
            for row in pending:
                action_id = row["action_id"]
                with self.store.lock:
                    if not self.valid_owner(json.loads(row["body"])):
                        self.mark(action_id, "ABORTED", "stale_action")
                        continue
                try:
                    status, receipt = self.remote("GET", "/router/actions/" + quote(action_id, safe=""))
                    if status == 200:
                        self.accept_receipt(row, receipt)
                        continue
                    if status != 404:
                        self.mark(action_id, "PREPARED", "router_unavailable")
                        continue
                except (OSError, ValueError):
                    self.mark(action_id, "PREPARED", "router_unavailable")
                    continue
                body = json.loads(row["body"])
                with self.store.lock:
                    valid = self.valid_owner(body) and self.store.meta("routing")["revision"] == body["expected_revision"]
                if not valid:
                    self.mark(action_id, "ABORTED", "stale_action")
                    continue
                try:
                    routes = proposed_routes(self.store, body)
                    if routes != json.loads(row["routes"]): raise APIError(422, "changed")
                except (APIError, ValueError):
                    self.mark(action_id, "ABORTED", "gate_changed")
                    continue
                self.dispatch(action_id)
            return {"actions": [get_action(self.store, row["action_id"]) for row in pending]}
