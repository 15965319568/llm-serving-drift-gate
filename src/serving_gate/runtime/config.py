"""Configuration resolves relative evidence paths at the configuration boundary."""
import json
from pathlib import Path
from urllib.parse import urlsplit

from .errors import APIError
from .validation import integer, number, object_value, string


def load_config(path):
    path = Path(path).resolve()
    config = object_value(json.loads(path.read_text(encoding="utf-8")), "config")
    for key in ("stable_version", "candidate_version"):
        string(config.get(key), key)
    if config["stable_version"] == config["candidate_version"]:
        raise APIError(400, "identical_versions")
    tenants = object_value(config.get("tenants"), "tenants")
    if not tenants:
        raise APIError(400, "empty_tenants")
    for name, limits in tenants.items():
        string(name, "tenant")
        object_value(limits, "tenant_limits")
        for key in ("max_running", "max_queued"):
            integer(limits.get(key), key, 1)
    for key in ("max_running", "max_attempts", "worker_timeout_ms"):
        integer(config.get(key), key, 1)
    integer(config.get("clock_start_ms"), "clock_start_ms")
    workers = object_value(config.get("worker_urls"), "worker_urls")
    for name in (config["stable_version"], config["candidate_version"]):
        validate_url(workers.get(name))
    validate_url(config.get("router_url"))
    evidence = string(config.get("evidence_dir"), "evidence_dir")
    config["evidence_dir"] = str((path.parent / evidence).resolve())
    if not Path(config["evidence_dir"]).is_dir():
        raise APIError(400, "missing_evidence")
    monitor = object_value(config.get("monitor"), "monitor")
    for key in ("window_ms", "min_completed"):
        integer(monitor.get(key), key, 1)
    number(monitor.get("max_error_rate"), "max_error_rate", maximum=1)
    for key in ("max_p95_ttft_ms", "max_input_psi"):
        number(monitor.get(key), key)
    return config


def validate_url(value):
    parts = urlsplit(string(value, "url"))
    if parts.scheme != "http" or parts.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise APIError(400, "nonlocal_worker_url")
    if parts.username or parts.password or parts.query or parts.fragment or parts.path not in {"", "/"}:
        raise APIError(400, "invalid_worker_url")
