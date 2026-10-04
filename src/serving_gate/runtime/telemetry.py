"""Transactional ingestion of bounded, non-sensitive production evidence."""
import json

from .errors import APIError
from .validation import canonical, integer, object_value, string

FIELDS = {"event_id", "request_id", "tenant", "model_version", "kind", "event_ms", "recorded_ms", "attempt", "data"}
KINDS = {"accepted", "attempt_started", "first_token", "finished"}


def validate_event(event, config):
    object_value(event, "event")
    if set(event) != FIELDS: raise APIError(400, "invalid_event_fields")
    for key in ("event_id", "request_id", "tenant", "model_version", "kind"):
        string(event[key], key)
    if event["event_id"].startswith("internal:"): raise APIError(400, "reserved_event_namespace")
    if event["tenant"] not in config["tenants"]: raise APIError(400, "unknown_tenant")
    if event["model_version"] not in config["worker_urls"]: raise APIError(400, "unknown_model")
    if event["kind"] not in KINDS: raise APIError(400, "invalid_event_kind")
    for key in ("event_ms", "recorded_ms", "attempt"): integer(event[key], key)
    data = object_value(event["data"], "event_data")
    if not set(data) <= {"status", "input_tokens", "output_tokens"}: raise APIError(400, "unsafe_event_data")
    for key in ("input_tokens", "output_tokens"):
        if key in data: integer(data[key], key)
    if "status" in data and data["status"] not in {"SUCCEEDED", "FAILED", "CANCELLED", "EXPIRED"}:
        raise APIError(400, "invalid_event_status")
    if event["kind"] == "finished" and set(data) != {"status", "input_tokens", "output_tokens"}:
        raise APIError(400, "missing_terminal_data")
    return event


def ingest(store, events):
    if not isinstance(events, list): raise APIError(400, "invalid_events")
    validated = [validate_event(event, store.config) for event in events]
    added = 0
    with store.transaction():
        for event in validated:
            payload = canonical(event)
            prior = store.conn.execute("SELECT payload FROM telemetry WHERE event_id=?", (event["event_id"],)).fetchone()
            if prior:
                if prior[0] != payload:
                    store.conn.execute("UPDATE telemetry SET payload=? WHERE event_id=?", (payload, event["event_id"]))
            else:
                store.conn.execute("INSERT INTO telemetry VALUES (?,?)", (event["event_id"], payload))
                added += 1
    return {"accepted": added, "duplicates": len(events) - added}


def read_events(store):
    with store.lock:
        return [json.loads(row[0]) for row in store.conn.execute("SELECT payload FROM telemetry")]
