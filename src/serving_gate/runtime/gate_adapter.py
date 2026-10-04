"""The runtime consumes the same formal evidence as the offline release tool."""
from ..engine import evaluate_bundle
from .errors import APIError
from .monitor import live_reasons


def proposed_routes(store, body):
    evidence = evaluate_bundle(store.config["evidence_dir"])
    sid = body["scenario_id"]
    decisions = {row["scenario_id"]: row for row in evidence["decisions"]}
    if sid not in decisions: raise APIError(400, "unknown_scenario")
    decision = decisions[sid]
    reasons = set()
    if body["operation"] == "PROMOTE":
        if decision["status"] != "CANARY": reasons.add("offline_gate_closed")
        reasons.update(())
        routes = {row["tenant_class"]: int(row["candidate_pct"]) for row in evidence["routing"] if row["scenario_id"] == sid}
    else:
        execution = next(row for row in evidence["execution"] if row["scenario_id"] == sid)
        zero = any(row["scenario_id"] == sid and row["feasible"] and row["candidate_rps"] == 0 for row in evidence["route_alternatives"])
        if not execution["window_valid"] or not execution["rollback_capacity_safe"] or not zero:
            reasons.add("rollback_unsafe")
        routes = dict.fromkeys(store.config["tenants"], 0)
    if set(routes) != set(store.config["tenants"]): raise APIError(400, "evidence_tenant_mismatch")
    if reasons: raise APIError(422, "release_blocked", reasons=sorted(reasons))
    return routes
