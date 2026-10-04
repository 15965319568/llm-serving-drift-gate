"""Small shared HTTP boundary for the loopback service and replay worker."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json

from .errors import APIError


class LocalHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False


class JSONHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def log_message(self, format, *args):
        # Request URLs can contain client material; access logs are opt-in outside this lab.
        return

    def read_json(self):
        try: length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc: raise APIError(400, "invalid_content_length") from exc
        if length < 0: raise APIError(400, "invalid_content_length")
        if length > 1048576: raise APIError(413, "body_too_large")
        try:
            value = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
            if not isinstance(value, dict): raise ValueError()
            return value
        except (UnicodeDecodeError, ValueError) as exc:
            raise APIError(400, "invalid_json") from exc

    def send_json(self, status, payload):
        data = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def guarded(self, function):
        try: function()
        except APIError as exc: self.send_json(exc.status, exc.payload())
        except (BrokenPipeError, ConnectionResetError): pass
        except (ValueError, KeyError, TypeError): self.send_json(400, {"error": "invalid_input"})
