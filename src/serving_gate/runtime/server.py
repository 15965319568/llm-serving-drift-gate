"""Public data-plane and release HTTP endpoints."""
import json
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from .actions import get_action
from .config import load_config
from .controller import Controller
from .errors import APIError
from .http_base import JSONHandler, LocalHTTPServer
from .monitor import metrics
from .reporting import live_snapshot
from .scheduler import Scheduler
from .sse import frame
from .store import Store, TERMINAL
from .telemetry import ingest
from .validation import integer, request_body


class Gateway:
    def __init__(self, config, state):
        self.config = config
        self.store = Store(state, config)
        self.controller = Controller(self.store)
        self.scheduler = Scheduler(self.store, config)

    def start(self):
        self.controller.reconcile()
        self.scheduler.start()


def handler_class(app):
    class Handler(JSONHandler):
        def do_GET(self): self.guarded(self.get)
        def do_POST(self): self.guarded(self.post)

        def parts(self):
            parsed = urlsplit(self.path)
            return unquote(parsed.path).rstrip("/") or "/", parse_qs(parsed.query)

        def get(self):
            path, query = self.parts()
            if path == "/health": return self.send_json(200, {"status": "ok"})
            if path == "/v1/snapshot": return self.send_json(200, live_snapshot(app.store))
            if path == "/v1/metrics":
                args = {}
                for key, name in (("start_ms", "start"), ("end_ms", "end"), ("as_of_ms", "as_of")):
                    if key in query:
                        try: args[name] = int(query[key][0])
                        except ValueError as exc: raise APIError(400, "invalid_metric_window") from exc
                return self.send_json(200, metrics(app.store, **args))
            if path.startswith("/v1/control/actions/"):
                return self.send_json(200, get_action(app.store, path.rsplit("/", 1)[1]))
            if path.startswith("/v1/requests/"):
                pieces = path.split("/")
                if len(pieces) == 4: return self.send_json(200, app.store.snapshot_request(pieces[3]))
                if len(pieces) == 5 and pieces[4] == "events":
                    try: after = int(query.get("after", [self.headers.get("Last-Event-ID", "0")])[0])
                    except ValueError as exc: raise APIError(400, "invalid_event_id") from exc
                    integer(after, "event_id")
                    return self.stream(pieces[3], after)
            raise APIError(404, "endpoint_not_found")

        def stream(self, request_id, after):
            app.store.snapshot_request(request_id)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            while True:
                events = app.store.event_rows(request_id, after)
                for event in events:
                    self.wfile.write(frame(event["event"], event["data"], event["id"]))
                    self.wfile.flush()
                    after = event["id"]
                status = app.store.snapshot_request(request_id)["status"]
                if status in TERMINAL and not app.store.event_rows(request_id, after): return
                with app.store.changed: app.store.changed.wait(timeout=0.1)

        def post(self):
            path, _ = self.parts()
            body = self.read_json()
            if path == "/v1/requests":
                created, request_id = app.store.submit(request_body(body, app.config["tenants"]))
                return self.send_json(202 if created else 200, app.store.snapshot_request(request_id))
            if path.startswith("/v1/requests/") and path.endswith("/cancel"):
                return self.send_json(200, app.store.cancel(path.split("/")[3]))
            if path == "/v1/clock":
                now = app.store.advance(integer(body.get("advance_ms"), "advance_ms"))
                return self.send_json(200, {"now_ms": now})
            if path == "/v1/telemetry": return self.send_json(200, ingest(app.store, body.get("events")))
            if path == "/v1/control/lease": return self.send_json(200, app.controller.lease(body))
            if path == "/v1/control/releases":
                code, action = app.controller.submit(body)
                return self.send_json(code, action)
            if path == "/v1/control/reconcile": return self.send_json(200, app.controller.reconcile())
            raise APIError(404, "endpoint_not_found")
    return Handler


def write_ready(path, host, port):
    if path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(target.suffix + ".tmp")
        temporary.write_text(json.dumps({"url": f"http://{host}:{port}"}) + "\n", encoding="utf-8")
        temporary.replace(target)


def serve(config_path, state, host, port, ready_file):
    app = Gateway(load_config(config_path), state)
    server = LocalHTTPServer((host, port), handler_class(app))
    app.start()
    write_ready(ready_file, host, server.server_port)
    try: server.serve_forever(poll_interval=0.1)
    finally:
        app.scheduler.stop()
        server.server_close()
