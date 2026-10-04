"""As-of monitoring groups logical requests rather than wire attempts."""
from collections import defaultdict
import math

from .errors import APIError
from .telemetry import read_events
from .validation import integer


def psi(baseline, candidate):
    if not baseline or not candidate: return None
    bins = (0, 256, 512, 1024, 2048, float("inf"))
    score = 0.0
    for lower, upper in zip(bins, bins[1:]):
        b = max(sum(lower <= n < upper for n in baseline) / len(baseline), 1e-6)
        c = max(sum(lower <= n < upper for n in candidate) / len(candidate), 1e-6)
        score += (c - b) * math.log(c / b)
    return score


def aggregate(events, config, start, end, as_of):
    for name, value in (("start_ms", start), ("end_ms", end), ("as_of_ms", as_of)):
        integer(value, name)
    if not 0 <= start < end <= as_of: raise APIError(400, "invalid_metric_window")
    visible = [e for e in events if e["event_ms"] <= as_of]
    by_request = defaultdict(list)
    for event in visible: by_request[event["request_id"]].append(event)
    grouped = defaultdict(list)
    for request_events in by_request.values():
        ordered = sorted(request_events, key=lambda e: (e["event_ms"], e["event_id"]))
        terminal = next((e for e in ordered if e["kind"] == "finished"), None)
        if terminal is None or not start <= terminal["event_ms"] < end: continue
        # Identity belongs to the terminal business fact, not any late duplicate.
        same = [e for e in ordered if (e["tenant"], e["model_version"]) == (terminal["tenant"], terminal["model_version"])]
        accepted = next((e for e in same if e["kind"] == "accepted"), None)
        first = next((e for e in same if e["kind"] == "first_token"), None)
        ttft = None
        if accepted and first and first["event_ms"] >= accepted["event_ms"]:
            ttft = first["event_ms"] - accepted["event_ms"]
        attempts = len({e["attempt"] for e in same if e["kind"] == "attempt_started"})
        grouped[(terminal["tenant"], terminal["model_version"])].append((terminal["data"], ttft, attempts))
    groups, inputs = [], {}
    for tenant in sorted(config["tenants"]):
        for model in sorted((config["stable_version"], config["candidate_version"])):
            records = grouped[(tenant, model)]
            statuses = [r[0]["status"] for r in records]
            successes = statuses.count("SUCCEEDED")
            failures, expired, cancelled = (statuses.count(s) for s in ("FAILED", "EXPIRED", "CANCELLED"))
            denominator = sum(r[2] for r in records)
            latency = sorted(r[1] for r in records if r[0]["status"] == "SUCCEEDED" and r[1] is not None)
            successful = [r[0] for r in records if r[0]["status"] == "SUCCEEDED"]
            inputs[(tenant, model)] = [r["input_tokens"] for r in successful]
            groups.append({"tenant": tenant, "model_version": model, "requests": len(records),
                "successes": successes, "failures": failures, "expired": expired, "cancelled": cancelled,
                "attempts": sum(r[2] for r in records), "billed_input_tokens": sum(r["input_tokens"] for r in successful),
                "billed_output_tokens": sum(r["output_tokens"] for r in successful),
                "error_rate": (failures + expired) / denominator if denominator else None,
                "p95_ttft_ms": latency[math.ceil(0.95 * len(latency)) - 1] if latency else None})
    drift = [{"tenant": tenant, "value": psi(inputs[(tenant, config["stable_version"])],
                                               inputs[(tenant, config["candidate_version"])])}
             for tenant in sorted(config["tenants"])]
    return {"start_ms": start, "end_ms": end, "as_of_ms": as_of, "groups": groups, "input_psi": drift}


def metrics(store, start=None, end=None, as_of=None):
    now = store.now()
    end = now if end is None else end
    as_of = now if as_of is None else as_of
    start = max(0, end - store.config["monitor"]["window_ms"]) if start is None else start
    return aggregate(read_events(store), store.config, start, end, as_of)


def live_reasons(store):
    policy = store.config["monitor"]
    try:
        report = metrics(store)
    except APIError:
        return {"live_evidence_incomplete"}
    reasons = set()
    for group in report["groups"]:
        if group["model_version"] != store.config["candidate_version"]: continue
        if group["successes"] + group["failures"] + group["expired"] < policy["min_completed"]:
            reasons.add("live_evidence_incomplete")
        if group["error_rate"] is None or group["p95_ttft_ms"] is None:
            reasons.add("live_evidence_incomplete")
        if group["error_rate"] is not None and group["error_rate"] > policy["max_error_rate"]:
            reasons.add("live_error_rate")
        if group["p95_ttft_ms"] is not None and group["p95_ttft_ms"] > policy["max_p95_ttft_ms"]:
            reasons.add("live_ttft")
    for drift in report["input_psi"]:
        if drift["value"] is None: reasons.add("live_evidence_incomplete")
        elif drift["value"] > policy["max_input_psi"]: reasons.add("live_drift")
    return reasons
