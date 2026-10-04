"""Deterministic loopback worker with explicit barriers, not latency padding."""
import json
from pathlib import Path
import socket
import threading
from urllib.parse import unquote, urlsplit

from .errors import APIError
from .http_base import JSONHandler, LocalHTTPServer
from .router_backend import RouterBackend
from .server import write_ready
from .sse import frame
from .validation import integer


class ReplayWorker:
    def __init__(self, config, state):
        self.config = config
        self.backend = RouterBackend(state)
        self.signals = {}
        self.lock = threading.RLock()

    def signal(self, name):
        with self.lock: return self.signals.setdefault(name, threading.Event())


def handler_class(worker):
    class Handler(JSONHandler):
        def do_GET(self): self.guarded(self.get)
        def do_POST(self): self.guarded(self.post)

        def path_name(self): return unquote(urlsplit(self.path).path).rstrip("/")

        def get(self):
            path = self.path_name()
            if path == "/health": return self.send_json(200, {"status": "ok"})
            if path == "/admin/stats": return self.send_json(200, worker.backend.stats())
            if path == "/router/state": return self.send_json(200, worker.backend.state())
            if path.startswith("/router/actions/"): return self.send_json(200, worker.backend.receipt(path.rsplit("/", 1)[1]))
            raise APIError(404, "endpoint_not_found")

        def post(self):
            path, body = self.path_name(), self.read_json()
            if path == "/generate": return self.generate(body)
            if path == "/router/fence": return self.send_json(200, worker.backend.fence(body))
            if path == "/router/apply":
                receipt, drop = worker.backend.apply(body)
                if drop:
                    self.connection.shutdown(socket.SHUT_RDWR)
                    self.connection.close()
                    return
                return self.send_json(200, receipt)
            if path.startswith("/admin/signals/"):
                worker.signal(path.rsplit("/", 1)[1]).set()
                return self.send_json(200, {"released": True})
            if path == "/admin/faults":
                with worker.backend.lock:
                    for key, value in body.items():
                        if key not in worker.backend.faults or type(value) is not bool: raise APIError(400, "invalid_fault")
                    worker.backend.faults.update(body)
                return self.send_json(200, {"configured": True})
            raise APIError(404, "endpoint_not_found")

        def generate(self, body):
            attempt = integer(body.get("attempt"), "attempt", 1)
            case = worker.config.get("cases", {}).get(body.get("prompt"), worker.config["default_case"])
            recipe = case["attempts"][min(attempt - 1, len(case["attempts"]) - 1)]
            worker.backend.record_call(body)
            protocol = worker.config["protocol"]
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("X-Worker-Protocol", protocol)
            self.end_headers()
            self.wfile.write(b": worker ready\r\n\r\n"); self.wfile.flush()
            if recipe.get("gate_before"): worker.signal(recipe["gate_before"]).wait()
            fragmentation = recipe.get("fragment_bytes", 4096)

            def emit(kind, data):
                if protocol == "v2":
                    if kind == "token": data = {"type": "delta", "index": data["seq"], "content": data["text"], "token_count": data["tokens"]}
                    elif kind == "done": data = {"type": "complete", "usage": data}
                    else: data = {"type": "failure", "code": data["code"], "can_retry": data["retryable"]}
                    kind = "message"
                encoded = frame(kind, data)
                for i in range(0, len(encoded), fragmentation):
                    self.wfile.write(encoded[i:i + fragmentation]); self.wfile.flush()

            total = 0
            for seq, chunk in enumerate(recipe.get("chunks", [])):
                data = {"seq": seq, "text": chunk["text"], "tokens": chunk["tokens"]}
                if recipe.get("outcome") == "bad_sequence": data["seq"] += 1
                emit("token", data); total += chunk["tokens"]
                if chunk.get("duplicate"): emit("token", data)
                if recipe.get("outcome") == "conflicting_duplicate": emit("token", {**data, "text": "different"})
            if recipe.get("gate_after"): worker.signal(recipe["gate_after"]).wait()
            outcome = recipe.get("outcome", "success")
            if outcome == "disconnect": return
            if outcome in {"retryable_error", "fatal_error"}:
                emit("error", {"code": "overloaded", "retryable": outcome == "retryable_error"})
            else:
                emit("done", {"input_tokens": recipe.get("input_tokens", 4), "output_tokens": total + int(outcome == "bad_usage")})
    return Handler


def serve_worker(config_path, state, host, port, ready_file):
    config = json.loads(Path(config_path).read_text(encoding="utf-8"))
    if config.get("protocol") not in {"v1", "v2"}: raise ValueError("unknown worker protocol")
    worker = ReplayWorker(config, state)
    server = LocalHTTPServer((host, port), handler_class(worker))
    write_ready(ready_file, host, server.server_port)
    try: server.serve_forever(poll_interval=0.1)
    finally: server.server_close()
