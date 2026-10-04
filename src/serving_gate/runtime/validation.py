"""Boundary validation shared by HTTP adapters, never by the verifier."""
import hashlib
import json
import math

from .errors import APIError


def integer(value, name, minimum=0, maximum=None):
    if type(value) is not int or value < minimum or (maximum is not None and value > maximum):
        raise APIError(400, "invalid_" + name)
    return value


def number(value, name, minimum=0, maximum=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise APIError(400, "invalid_" + name)
    if value < minimum or (maximum is not None and value > maximum):
        raise APIError(400, "invalid_" + name)
    return value


def string(value, name):
    if not isinstance(value, str) or not value:
        raise APIError(400, "invalid_" + name)
    return value


def object_value(value, name="body"):
    if not isinstance(value, dict):
        raise APIError(400, "invalid_" + name)
    return value


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def request_body(body, tenants):
    object_value(body)
    tenant = string(body.get("tenant"), "tenant")
    if tenant not in tenants:
        raise APIError(400, "unknown_tenant")
    result = {"tenant": tenant, "idempotency_key": string(body.get("idempotency_key"), "idempotency_key"),
              "prompt": string(body.get("prompt"), "prompt"),
              "max_tokens": integer(body.get("max_tokens"), "max_tokens", 1, 4096),
              "deadline_at_ms": body.get("deadline_at_ms")}
    if result["deadline_at_ms"] is not None:
        integer(result["deadline_at_ms"], "deadline_at_ms")
    return result


def release_body(body):
    object_value(body)
    result = {name: string(body.get(name), name) for name in ("action_id", "holder", "scenario_id", "operation")}
    for name in ("epoch", "expected_revision"):
        result[name] = integer(body.get(name), name)
    if result["operation"] not in {"PROMOTE", "ROLLBACK"}:
        raise APIError(400, "invalid_operation")
    return result
