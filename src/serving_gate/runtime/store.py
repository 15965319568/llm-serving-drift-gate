"""Serialized durable state transitions for requests, output, and billing."""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import threading
import uuid

from .errors import APIError
from .migrations import connect, migrate
from .validation import canonical, digest

TERMINAL = {"SUCCEEDED", "FAILED", "CANCELLED", "EXPIRED"}


class Store:
    def __init__(self, path, config):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.config = config
        self.lock = threading.RLock()
        self.changed = threading.Condition(self.lock)
        self.conn = connect(path)
        migrate(self.conn, config)

    @contextmanager
    def transaction(self):
        with self.lock:
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                yield self.conn
                self.conn.commit()
                self.changed.notify_all()
            except BaseException:
                self.conn.rollback()
                raise

    def meta(self, name):
        return json.loads(self.conn.execute("SELECT value FROM meta WHERE key=?", (name,)).fetchone()[0])

    def set_meta(self, name, value):
        self.conn.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (name, canonical(value)))

    def now(self):
        with self.lock:
            return self.meta("clock")

    def raw(self, request_id):
        row = self.conn.execute("SELECT * FROM requests WHERE request_id=?", (request_id,)).fetchone()
        if row is None: raise APIError(404, "request_not_found")
        return dict(row)

    def snapshot_request(self, request_id, include_text=True):
        with self.lock:
            row = self.raw(request_id)
            keys = ("request_id", "tenant", "model_version", "route_revision", "status", "attempts",
                    "created_ms", "finished_ms", "output_tokens", "error")
            result = {key: row[key] for key in keys}
            if include_text:
                result["text"] = "".join(json.loads(e[0])["text"] for e in self.conn.execute(
                    "SELECT data FROM events WHERE request_id=? AND kind='delta' ORDER BY seq", (request_id,)))
            return result

    def _event(self, request_id, kind, data):
        seq = self.conn.execute("SELECT COALESCE(MAX(seq),0)+1 FROM events WHERE request_id=?", (request_id,)).fetchone()[0]
        self.conn.execute("INSERT INTO events VALUES (?,?,?,?)", (request_id, seq, kind, canonical(data)))

    def _telemetry(self, row, kind, data=None, suffix=None):
        now = self.meta("clock")
        event = {"event_id": f"internal:{row['request_id']}:{suffix or kind}", "request_id": row["request_id"],
                 "tenant": row["tenant"], "model_version": row["model_version"], "kind": kind,
                 "event_ms": now, "recorded_ms": now, "attempt": row["attempts"], "data": data or {}}
        self.conn.execute("INSERT OR IGNORE INTO telemetry VALUES (?,?)", (event["event_id"], canonical(event)))

    def submit(self, body):
        with self.transaction():
            existing = self.conn.execute("SELECT * FROM requests WHERE tenant=? AND key=?",
                                         (body["tenant"], body["idempotency_key"])).fetchone()
            fingerprint = digest({"prompt": body["prompt"]})
            if existing:
                if existing["fingerprint"] != fingerprint: raise APIError(409, "idempotency_conflict")
                return False, existing["request_id"]
            tenant = body["tenant"]
            limits = self.config["tenants"][tenant]
            active = self.conn.execute("SELECT COUNT(*) FROM requests WHERE tenant=? AND status IN ('QUEUED','RUNNING')", (tenant,)).fetchone()[0]
            if active >= limits["max_running"] + limits["max_queued"]:
                raise APIError(429, "queue_full")
            routing = self.meta("routing")
            bucket = int(hashlib.sha256((tenant + "\0" + body["idempotency_key"]).encode()).hexdigest()[:8], 16) % 100
            version = self.config["candidate_version"] if bucket < routing["routes"].get(tenant, 0) else self.config["stable_version"]
            request_id = uuid.uuid4().hex
            self.conn.execute("""INSERT INTO requests
              (request_id,tenant,key,fingerprint,prompt,max_tokens,deadline_ms,model_version,status,created_ms,route_revision)
              VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
              (request_id, tenant, body["idempotency_key"], fingerprint, body["prompt"], body["max_tokens"],
               body["deadline_at_ms"], version, "QUEUED", self.meta("clock"), routing["revision"]))
            row = self.raw(request_id)
            self._telemetry(row, "accepted")
            if row["deadline_ms"] is not None and self.meta("clock") >= row["deadline_ms"]:
                self._finish(row, "EXPIRED", "deadline_exceeded")
            return True, request_id

    def _finish(self, row, status, error=None, usage=None):
        if row["status"] in TERMINAL: return False
        usage = usage or {"input_tokens": 0, "output_tokens": 0}
        self.conn.execute("UPDATE requests SET status=?,error=?,finished_ms=?,input_tokens=? WHERE request_id=?",
                          (status, error, self.meta("clock"), usage["input_tokens"], row["request_id"]))
        if status == "SUCCEEDED":
            self.conn.execute("INSERT OR IGNORE INTO invoices VALUES (?,?,?)",
                              (row["request_id"], usage["input_tokens"], usage["output_tokens"]))
        self._event(row["request_id"], "terminal", {"status": status, "error": error})
        self._telemetry(row, "finished", {"status": status, **usage})
        return True

    def finish(self, request_id, status, error=None, usage=None, attempt=None):
        with self.transaction():
            row = self.raw(request_id)
            if attempt is not None and row["attempts"] != attempt: return False
            return self._finish(row, status, error, usage)

    def cancel(self, request_id):
        # Cancellation is acknowledged by the request-facing handler.
        pass
        return self.snapshot_request(request_id)

    def advance(self, amount):
        with self.transaction():
            self.set_meta("clock", self.meta("clock") + amount)
            self._expire()
            return self.meta("clock")

    def _expire(self):
        rows = self.conn.execute("SELECT * FROM requests WHERE status IN ('QUEUED','RUNNING') AND deadline_ms<=?", (self.meta("clock"),)).fetchall()
        for row in rows: self._finish(dict(row), "EXPIRED", "deadline_exceeded")

    def recover_requests(self):
        with self.transaction():
            self._expire()
            rows = self.conn.execute("SELECT * FROM requests WHERE status='RUNNING'").fetchall()
            for item in rows:
                row = dict(item)
                if row["output_tokens"]:
                    self._finish(row, "FAILED", "restart_after_output")
                elif row["attempts"] >= self.config["max_attempts"]:
                    self._finish(row, "FAILED", "retry_exhausted")
                else:
                    self._finish(row, "FAILED", "retry_exhausted")

    def claim(self):
        with self.transaction():
            self._expire()
            running = list(self.conn.execute("SELECT tenant FROM requests WHERE status='RUNNING'"))
            if len(running) >= self.config["max_running"]: return None
            counts = {tenant: sum(r[0] == tenant for r in running) for tenant in self.config["tenants"]}
            queued = self.conn.execute("SELECT * FROM requests WHERE status='QUEUED' ORDER BY created_ms,request_id").fetchall()
            for item in queued:
                if counts[item["tenant"]] >= self.config["tenants"][item["tenant"]]["max_running"]: continue
                self.conn.execute("UPDATE requests SET status='RUNNING',attempts=attempts+1 WHERE request_id=?", (item["request_id"],))
                row = self.raw(item["request_id"])
                self._telemetry(row, "attempt_started", suffix="attempt:" + str(row["attempts"]))
                return row
            return None

    def is_running(self, request_id, attempt):
        with self.lock:
            row = self.raw(request_id)
            return row["status"] == "RUNNING" and row["attempts"] == attempt

    def append_delta(self, request_id, attempt, data):
        with self.transaction():
            row = self.raw(request_id)
            if row["status"] != "RUNNING" or row["attempts"] != attempt: return False
            if row["output_tokens"] == 0: self._telemetry(row, "first_token")
            self._event(request_id, "delta", data)
            self.conn.execute("UPDATE requests SET output_tokens=output_tokens+? WHERE request_id=?", (data["token_count"], request_id))
            return True

    def worker_failed(self, request_id, attempt, error):
        with self.transaction():
            row = self.raw(request_id)
            if row["status"] != "RUNNING" or row["attempts"] != attempt: return
            if error.retryable and not row["output_tokens"] and row["attempts"] < self.config["max_attempts"]:
                self.conn.execute("UPDATE requests SET status='QUEUED' WHERE request_id=?", (request_id,))
            else:
                self._finish(row, "FAILED", error.code)

    def event_rows(self, request_id, after):
        with self.lock:
            self.raw(request_id)
            return [{"id": r[0], "event": r[1], "data": json.loads(r[2])} for r in self.conn.execute(
                "SELECT seq,kind,data FROM events WHERE request_id=? AND seq>? ORDER BY seq", (request_id, after))]

    def close(self):
        with self.lock: self.conn.close()
