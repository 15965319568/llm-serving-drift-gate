"""Direct loopback HTTP; process proxy variables never intercept local workers."""
import http.client
import json
from urllib.parse import urlsplit

from .errors import WorkerError
from .protocol import WorkerProtocol
from .sse import SSEDecoder


def connection(url, timeout):
    parts = urlsplit(url)
    return http.client.HTTPConnection(parts.hostname, parts.port or 80, timeout=timeout)


def json_call(url, method, path, body=None, timeout=2):
    conn = connection(url, timeout)
    try:
        payload = None if body is None else json.dumps(body).encode()
        conn.request(method, path, body=payload, headers={"Content-Type": "application/json"})
        response = conn.getresponse()
        data = response.read()
        return response.status, json.loads(data) if data else {}
    finally:
        conn.close()


def generate(config, request, cancelled, on_delta):
    conn = connection(config["worker_urls"][request["model_version"]], config["worker_timeout_ms"] / 1000)
    try:
        body = {key: request[key] for key in ("request_id", "model_version", "prompt", "max_tokens")}
        body["attempt"] = request["attempts"]
        conn.request("POST", "/generate", body=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
        response = conn.getresponse()
        if response.status != 200:
            raise WorkerError("worker_http_error", response.status >= 500)
        wire = WorkerProtocol(response.getheader("X-Worker-Protocol"), request["max_tokens"])
        decoder = SSEDecoder()
        while not cancelled():
            chunk = response.read1(1024)
            for event, data, _ in decoder.feed(chunk, final=not chunk):
                result = wire.decode(event, data)
                if result is None: continue
                kind, payload = result
                if kind == "complete": return payload
                if not on_delta(payload): return None
            if not chunk: break
        if cancelled(): return None
        raise WorkerError("worker_disconnected", True)
    except (OSError, http.client.HTTPException) as exc:
        raise WorkerError("worker_disconnected", True) from exc
    finally:
        conn.close()
