from __future__ import annotations

import math
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

from .io import load_csv, load_json, load_ndjson
from .metrics import BoundedMetrics
from .redaction import redact
from .timeutil import iso, parse_time


class ContractError(ValueError):
    """Raised when an evidence bundle violates the input contract."""


def _number(value: Any) -> float:
    return float(str(value).strip())


def _integer(value: Any) -> int:
    return int(float(str(value).strip()))


def _truthy(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "authoritative"}


def _rows_until(rows: Iterable[dict[str, Any]], field: str, cutoff) -> list[dict[str, Any]]:
    # Starter defect: future evidence is included in every scenario.
    return list(rows)


def _p95(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * 0.95) - 1)]


def _psi(base: list[float], candidate: list[float]) -> float:
    if not base or not candidate:
        return 0.0
    buckets = ((0, 256), (256, 512), (512, 1024), (1024, 2048), (2048, float("inf")))
    epsilon = 1e-6
    base_total = len(base)
    candidate_total = len(candidate)
    score = 0.0
    for lower, upper in buckets:
        base_share = sum(lower <= value < upper for value in base) / base_total
        candidate_share = sum(lower <= value < upper for value in candidate) / candidate_total
        b = max(base_share, epsilon)
        c = max(candidate_share, epsilon)
        score += (c - b) * math.log(c / b)
    return round(score, 6)


def _count_psi(base_counts: list[float], candidate_counts: list[float]) -> float:
    """PSI for already-bucketed counts; bins are aligned by row order."""
    if not base_counts or not candidate_counts or len(base_counts) != len(candidate_counts):
        return 0.0
    epsilon = 1e-6
    base_total = max(sum(base_counts), epsilon)
    candidate_total = max(sum(candidate_counts), epsilon)
    score = 0.0
    for base_count, candidate_count in zip(base_counts, candidate_counts):
        b = max(base_count / base_total, epsilon)
        c = max(candidate_count / candidate_total, epsilon)
        score += (c - b) * math.log(c / b)
    return round(score, 6)


def _weighted_average(rows: list[dict[str, Any]], value_field: str, weight_field: str) -> float | None:
    valid = [row for row in rows if _truthy(row.get("valid", True))]
    weight = sum(max(_number(row.get(weight_field, 1)), 0) for row in valid)
    if not valid or weight <= 0:
        return None
    return sum(_number(row[value_field]) * max(_number(row.get(weight_field, 1)), 0) for row in valid) / weight


def _load_bundle(input_dir: Path) -> dict[str, Any]:
    required = {
        "policy": "policies.json",
        "deployment": "deployment_manifest.json",
        "autoscaling": "autoscaling_policy.json",
        "flags": "feature_flags.json",
        "trace": "request_trace.ndjson",
        "eval": "eval_results.csv",
        "telemetry": "gpu_telemetry.csv",
        "inventory": "replica_inventory.csv",
        "nodes": "node_inventory.csv",
        "tenant_budgets": "tenant_budgets.csv",
        "cost_rates": "cost_rates.csv",
        "rollback_windows": "rollback_windows.csv",
        "approvals": "approval_records.ndjson",
        "claims": "operator_claims.ndjson",
        "incidents": "incident_events.ndjson",
        "routes": "route_history.csv",
    }
    missing = [name for name in required.values() if not (input_dir / name).is_file()]
    if missing:
        raise ContractError(f"missing input attachment(s): {', '.join(missing)}")
    return {
        "policy": load_json(input_dir / required["policy"]),
        "deployment": load_json(input_dir / required["deployment"]),
        "autoscaling": load_json(input_dir / required["autoscaling"]),
        "flags": load_json(input_dir / required["flags"]),
        "trace": load_ndjson(input_dir / required["trace"]),
        "eval": load_csv(input_dir / required["eval"]),
        "telemetry": load_csv(input_dir / required["telemetry"]),
        "inventory": load_csv(input_dir / required["inventory"]),
        "nodes": load_csv(input_dir / required["nodes"]),
        "tenant_budgets": load_csv(input_dir / required["tenant_budgets"]),
        "cost_rates": load_csv(input_dir / required["cost_rates"]),
        "rollback_windows": load_csv(input_dir / required["rollback_windows"]),
        "approvals": load_ndjson(input_dir / required["approvals"]),
        "claims": load_ndjson(input_dir / required["claims"]),
        "incidents": load_ndjson(input_dir / required["incidents"]),
        "routes": load_csv(input_dir / required["routes"]),
    }


def _active_approval(approvals: list[dict[str, Any]], policy: dict[str, Any], cutoff) -> tuple[bool, str]:
    candidate = policy["candidate_version"]
    scope = policy["approval_scope"]
    matching = [
        row
        for row in approvals
        if row.get("model_version") == candidate
        and row.get("scope") == scope
        and parse_time(str(row["signed_at"])) <= cutoff
    ]
    matching.sort(key=lambda row: (parse_time(str(row["signed_at"])), str(row.get("approval_id", ""))))
    if not matching:
        return False, "approval_missing_at_cutoff"
    latest = matching[-1]
    if str(latest.get("status")) != "approved":
        return False, "approval_not_approved"
    # Starter defect: revocation is ignored.
    return True, "approval_active"


def _active_flag(flags: dict[str, Any], cutoff) -> bool:
    record = flags.get("candidate_route", {})
    enabled_at = record.get("enabled_at")
    disabled_at = record.get("disabled_at")
    if not enabled_at or parse_time(str(enabled_at)) > cutoff:
        return False
    return not disabled_at or parse_time(str(disabled_at)) > cutoff


def _active_incident(incidents: list[dict[str, Any]], cutoff) -> str | None:
    active = []
    for row in incidents:
        occurred = parse_time(str(row["occurred_at"]))
        resolved = row.get("resolved_at")
        if occurred <= cutoff and (not resolved or parse_time(str(resolved)) > cutoff) and str(row.get("severity")) == "critical":
            active.append(str(row.get("component", "critical_incident")))
    return sorted(active)[0] if active else None


def _request_summary(trace: list[dict[str, Any]], model_version: str, cutoff) -> dict[str, Any]:
    rows = [row for row in _rows_until(trace, "received_at", cutoff) if row.get("model_version") == model_version]
    if not rows:
        return {"requests": 0, "error_rate": None, "p95_ttft_ms": None, "p95_e2e_ms": None, "prompt_tokens": []}
    errors = sum(1 for row in rows if str(row.get("status", "")).lower() not in {"ok", "200", "success"})
    return {
        "requests": len(rows),
        "error_rate": round(errors / len(rows), 6),
        "p95_ttft_ms": _p95([_number(row["ttft_ms"]) for row in rows]),
        "p95_e2e_ms": _p95([_number(row["e2e_ms"]) for row in rows]),
        "prompt_tokens": [_number(row["prompt_tokens"]) for row in rows],
    }


def _tenant_summary(trace: list[dict[str, Any]], model_version: str, budgets: list[dict[str, str]], cutoff) -> tuple[list[dict[str, Any]], bool]:
    rows = [row for row in _rows_until(trace, "received_at", cutoff) if row.get("model_version") == model_version]
    by_class: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_class[str(row.get("tenant_class", "unknown"))].append(row)
    budget_map = {str(row["tenant_class"]): row for row in budgets}
    findings: list[dict[str, Any]] = []
    passed = True
    for tenant_class in sorted(budget_map):
        group = by_class.get(tenant_class, [])
        budget = budget_map[tenant_class]
        error_rate = None if not group else sum(str(row.get("status", "")).lower() not in {"ok", "200", "success"} for row in group) / len(group)
        p95 = _p95([_number(row["ttft_ms"]) for row in group])
        ok = bool(group) and error_rate <= _number(budget["error_rate"]) and p95 is not None and p95 <= _number(budget["p95_ttft_ms"])
        passed = passed and ok
        findings.append({"tenant_class": tenant_class, "requests": len(group), "error_rate": None if error_rate is None else round(error_rate, 6), "p95_ttft_ms": p95, "passed": ok})
    return findings, passed


def _cost_summary(trace: list[dict[str, Any]], rates: list[dict[str, str]], model_version: str, cutoff) -> dict[str, Any]:
    rows = [row for row in _rows_until(trace, "received_at", cutoff) if row.get("model_version") == model_version]
    rate = next((row for row in rates if row.get("model_version") == model_version), None)
    if not rate:
        raise ContractError(f"missing cost rate for {model_version}")
    input_tokens = sum(_number(row["prompt_tokens"]) for row in rows)
    output_tokens = sum(_number(row["output_tokens"]) for row in rows)
    cost = input_tokens / 1_000_000 * _number(rate["input_usd_per_million"]) + output_tokens / 1_000_000 * _number(rate["output_usd_per_million"])
    return {"model_version": model_version, "requests": len(rows), "input_tokens": int(input_tokens), "output_tokens": int(output_tokens), "estimated_usd": round(cost, 8)}


def _quality_summary(eval_rows: list[dict[str, Any]], policy: dict[str, Any], cutoff) -> dict[str, Any]:
    candidate = policy["candidate_version"]
    rows = [
        row
        for row in eval_rows
        if row.get("model_version") == candidate and parse_time(str(row["evaluated_at"])) <= cutoff
    ]
    by_slice: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_slice[str(row["slice"])].append(row)
    findings: list[dict[str, Any]] = []
    quality_policy = policy["drift"]
    all_pass = True
    for slice_name in sorted(set(by_slice) | set(policy.get("required_slices", []))):
        slice_rows = by_slice[slice_name]
        valid = [row for row in slice_rows if _truthy(row.get("valid", True))]
        delta = _weighted_average(
            [{**row, "delta": _number(row["candidate_score"]) - _number(row["baseline_score"])} for row in valid],
            "delta",
            "weight",
        )
        base_counts = [_number(row.get("baseline_count", 0)) for row in valid]
        candidate_counts = [_number(row.get("candidate_count", 0)) for row in valid]
        psi = _count_psi(base_counts, candidate_counts)
        reasons: list[str] = []
        if len(valid) < int(quality_policy["min_valid_rows"]):
            reasons.append("insufficient_valid_eval_rows")
        if delta is None or delta < _number(quality_policy["max_quality_delta"]):
            reasons.append("quality_delta_below_gate")
        if psi > _number(quality_policy["max_psi"]):
            reasons.append("quality_distribution_drift")
        passed = not reasons
        all_pass = all_pass and passed
        findings.append(
            {
                "slice": slice_name,
                "evaluated_rows": len(slice_rows),
                "valid_rows": len(valid),
                "weighted_quality_delta": None if delta is None else round(delta, 6),
                "psi": psi,
                "critical": slice_name in set(policy.get("critical_slices", [])) or (_truthy(slice_rows[0].get("critical", False)) if slice_rows else False) or (delta is not None and delta < _number(quality_policy.get("critical_quality_delta", quality_policy["max_quality_delta"]))),
                "passed": passed,
                "reasons": reasons,
            }
        )
    critical_fail = any(row["critical"] and not row["passed"] for row in findings)
    evidence_incomplete = any("insufficient_valid_eval_rows" in row["reasons"] for row in findings)
    return {"passed": all_pass and not critical_fail, "critical_fail": critical_fail, "evidence_incomplete": evidence_incomplete, "findings": findings}


def _capacity_summary(
    inventory: list[dict[str, Any]], telemetry: list[dict[str, Any]], nodes: list[dict[str, str]], deployment: dict[str, Any], policy: dict[str, Any], model_version: str, cutoff
) -> dict[str, Any]:
    cap_policy = policy["capacity"]
    active_inventory = [
        row
        for row in inventory
        if row.get("model_version") == model_version
        and _truthy(row.get("authoritative", True))
        and parse_time(str(row["ready_at"])) <= cutoff
        and (not row.get("retired_at") or parse_time(str(row["retired_at"])) > cutoff)
    ]
    latest: dict[str, dict[str, Any]] = {}
    authoritative_nodes = {str(row["node_id"]) for row in nodes if _truthy(row.get("authoritative", False))}
    for row in telemetry:
        # Starter defect: shadow telemetry is accepted as authoritative.
        if row.get("model_version") != model_version:
            continue
        if parse_time(str(row["sampled_at"])) > cutoff:
            continue
        replica = str(row["replica_id"])
        previous = latest.get(replica)
        if previous is None or parse_time(str(row["sampled_at"])) >= parse_time(str(previous["sampled_at"])):
            latest[replica] = row
    total = 0.0
    usable = 0
    excluded: list[dict[str, str]] = []
    replica_details: list[dict[str, Any]] = []
    for row in active_inventory:
        replica = str(row["replica_id"])
        if str(row.get("node_id")) not in authoritative_nodes:
            excluded.append({"replica_id": replica, "reason": "node_not_authoritative"})
            replica_details.append({"replica_id": replica, "model_version": model_version, "included": False, "reason": "node_not_authoritative", "capacity_rps": 0.0})
            continue
        sample = latest.get(replica)
        if sample is None:
            excluded.append({"replica_id": replica, "reason": "telemetry_missing"})
            replica_details.append({"replica_id": replica, "model_version": model_version, "included": False, "reason": "telemetry_missing", "capacity_rps": 0.0})
            continue
        mem = _number(sample["gpu_mem_pct"])
        queue = _number(sample["queue_depth"])
        if mem > _number(cap_policy["max_gpu_mem_pct"]):
            excluded.append({"replica_id": replica, "reason": "gpu_memory_over_limit"})
            replica_details.append({"replica_id": replica, "model_version": model_version, "included": False, "reason": "gpu_memory_over_limit", "capacity_rps": 0.0})
            continue
        if queue > _number(cap_policy["max_queue_depth"]):
            excluded.append({"replica_id": replica, "reason": "queue_over_limit"})
            replica_details.append({"replica_id": replica, "model_version": model_version, "included": False, "reason": "queue_over_limit", "capacity_rps": 0.0})
            continue
        throughput = min(_number(row["prefill_tokens_s"]), _number(row["decode_tokens_s"]))
        capacity_rps = throughput * _number(deployment.get("throughput_multiplier", 1.0)) / _number(cap_policy["mean_tokens_per_request"])
        total += capacity_rps
        usable += 1
        replica_details.append({"replica_id": replica, "model_version": model_version, "included": True, "reason": "eligible", "capacity_rps": round(capacity_rps, 6)})
    return {
        "model_version": model_version,
        "usable_replicas": usable,
        "capacity_rps": round(total, 6),
        "excluded": excluded,
        "replica_details": replica_details,
        "telemetry_replicas": len(latest),
    }


def _reconcile_claims(claims: list[dict[str, Any]], policy: dict[str, Any]) -> list[dict[str, Any]]:
    formal = {
        "candidate_version": str(policy["candidate_version"]),
        "target_canary_pct": str(policy["target_canary_pct"]),
        "approval_scope": str(policy["approval_scope"]),
    }
    result = []
    for claim in sorted(claims, key=lambda row: str(row.get("claim_id", ""))):
        subject = str(claim.get("subject", ""))
        claimed = str(claim.get("value", ""))
        status = "untrusted"
        if subject in formal and claimed == formal[subject]:
            status = "agrees_with_formal_policy"
        elif subject in formal:
            status = "overridden_by_formal_policy"
        result.append(
            {
                "claim_id": str(claim.get("claim_id", "")),
                "subject": subject,
                "claimed_value": claimed,
                "claimed_at": str(claim.get("claimed_at", "")),
                "source": str(claim.get("source", "operator")),
                "resolution": status,
            }
        )
    return result


def evaluate_bundle(input_dir: str | Path) -> dict[str, Any]:
    """Evaluate every scenario independently using only evidence available at its cutoff."""
    bundle = _load_bundle(Path(input_dir))
    policy = bundle["policy"]
    required_policy = {"baseline_version", "candidate_version", "scenarios", "slo", "drift", "capacity", "approval_scope", "target_canary_pct"}
    missing = required_policy - set(policy)
    if missing:
        raise ContractError(f"policy missing field(s): {', '.join(sorted(missing))}")
    metrics = BoundedMetrics()
    decisions: list[dict[str, Any]] = []
    capacity_rows: list[dict[str, Any]] = []
    replica_rows: list[dict[str, Any]] = []
    drift_rows: list[dict[str, Any]] = []
    tenant_rows: list[dict[str, Any]] = []
    cost_rows: list[dict[str, Any]] = []
    execution_rows: list[dict[str, Any]] = []
    audit: list[dict[str, Any]] = []
    baseline = str(policy["baseline_version"])
    candidate = str(policy["candidate_version"])
    for scenario in sorted(policy["scenarios"], key=lambda row: str(row["scenario_id"])):
        scenario_id = str(scenario["scenario_id"])
        cutoff = parse_time(str(scenario["evaluation_time"]))
        approval_ok, approval_reason = _active_approval(bundle["approvals"], policy, cutoff)
        flag_ok = _active_flag(bundle["flags"], cutoff)
        active_incident = _active_incident(bundle["incidents"], cutoff)
        stable = _request_summary(bundle["trace"], baseline, cutoff)
        canary = _request_summary(bundle["trace"], candidate, cutoff)
        tenant_findings, tenant_ok = _tenant_summary(bundle["trace"], candidate, bundle["tenant_budgets"], cutoff)
        quality = _quality_summary(bundle["eval"], policy, cutoff)
        candidate_capacity = _capacity_summary(bundle["inventory"], bundle["telemetry"], bundle["nodes"], bundle["deployment"], policy, candidate, cutoff)
        stable_capacity = _capacity_summary(bundle["inventory"], bundle["telemetry"], bundle["nodes"], bundle["deployment"], policy, baseline, cutoff)
        prompt_psi = _psi(stable["prompt_tokens"], canary["prompt_tokens"])
        slo = policy["slo"]
        latency_ok = canary["p95_ttft_ms"] is not None and canary["p95_e2e_ms"] is not None and canary["p95_ttft_ms"] <= _number(slo["p95_ttft_ms"]) and canary["p95_e2e_ms"] <= _number(slo["p95_e2e_ms"])
        error_ok = canary["error_rate"] is not None and canary["error_rate"] <= _number(slo["error_rate"])
        drift_ok = prompt_psi <= _number(policy["drift"]["max_psi"])
        route_cap = min(_number(policy["target_canary_pct"]), _number(policy.get("target_canary_pct", 100)), _number(bundle["autoscaling"].get("max_canary_pct", 100)), _number(scenario.get("route_cap_pct", policy["target_canary_pct"])))
        allowed_pcts = sorted({0.0, 5.0, 10.0, 20.0, route_cap})
        selected_pct = 0.0
        capacity_reason = "capacity_sufficient"
        if approval_ok and flag_ok and quality["passed"] and latency_ok and error_ok and drift_ok and tenant_ok and not active_incident and not _truthy(scenario.get("force_rollback", False)):
            for pct in reversed([value for value in allowed_pcts if value <= route_cap]):
                requested_rps = _number(scenario["traffic_rps"]) * pct / 100.0
                required_rps = requested_rps * (1 + _number(policy["capacity"]["headroom"]))
                if candidate_capacity["capacity_rps"] >= required_rps:
                    selected_pct = pct
                    break
            if selected_pct == 0:
                capacity_reason = "candidate_capacity_insufficient"
        reasons: list[str] = []
        if not approval_ok:
            reasons.append(approval_reason)
        if not flag_ok:
            reasons.append("candidate_route_flag_inactive")
        if not quality["passed"]:
            reasons.append("quality_gate_failed")
        if quality["evidence_incomplete"]:
            reasons.append("quality_evidence_incomplete")
        if not latency_ok:
            reasons.append("latency_slo_failed")
        if not error_ok:
            reasons.append("error_rate_slo_failed")
        if not tenant_ok:
            reasons.append("tenant_slo_failed")
        if not drift_ok:
            reasons.append("input_distribution_drift")
        if _truthy(scenario.get("force_rollback", False)):
            reasons.append("scenario_forces_rollback")
        if active_incident:
            reasons.append("critical_incident_active")
        if selected_pct == 0 and not reasons:
            reasons.append(capacity_reason)
        if selected_pct > 0:
            status = "CANARY"
        elif any(reason in {"quality_gate_failed", "latency_slo_failed", "error_rate_slo_failed", "tenant_slo_failed", "input_distribution_drift", "scenario_forces_rollback", "approval_revoked_at_cutoff", "critical_incident_active"} for reason in reasons) and not quality["evidence_incomplete"]:
            status = "ROLLBACK"
        else:
            status = "HOLD"
        decision = {
            "scenario_id": scenario_id,
            "evaluation_time": iso(cutoff),
            "status": status,
            "candidate_version": candidate,
            "stable_version": baseline,
            "candidate_canary_pct": int(selected_pct),
            "stable_route_pct": int(100 - selected_pct),
            "traffic_rps": _number(scenario["traffic_rps"]),
            "approval": approval_reason,
            "route_flag_active": flag_ok,
            "active_incident": active_incident or "",
            "quality_passed": quality["passed"],
            "tenant_slos_passed": tenant_ok,
            "input_psi": prompt_psi,
            "candidate_capacity_rps": candidate_capacity["capacity_rps"],
            "stable_capacity_rps": stable_capacity["capacity_rps"],
            "reasons": sorted(set(reasons)),
        }
        decisions.append(decision)
        for finding in quality["findings"]:
            drift_rows.append({"scenario_id": scenario_id, **finding})
        for finding in tenant_findings:
            tenant_rows.append({"scenario_id": scenario_id, "evaluation_time": iso(cutoff), **finding})
        for version in (candidate, baseline):
            cost_rows.append({"scenario_id": scenario_id, "evaluation_time": iso(cutoff), **_cost_summary(bundle["trace"], bundle["cost_rates"], version, cutoff)})
        for detail in candidate_capacity["replica_details"] + stable_capacity["replica_details"]:
            replica_rows.append({"scenario_id": scenario_id, "evaluation_time": iso(cutoff), **detail})
        window = next((row for row in bundle["rollback_windows"] if row.get("scenario_id") == scenario_id), None)
        execution_rows.append({
            "scenario_id": scenario_id, "status": status, "window_owner": str(window.get("owner", "")) if window else "",
            "rollback_target": str(window.get("rollback_target", baseline)) if window else baseline,
            "window_valid": bool(window and parse_time(str(window["window_start"])) <= cutoff < parse_time(str(window["window_end"]))),
        })
        capacity_rows.extend(
            [
                {"scenario_id": scenario_id, "evaluation_time": iso(cutoff), **candidate_capacity},
                {"scenario_id": scenario_id, "evaluation_time": iso(cutoff), **stable_capacity},
            ]
        )
        metrics.inc("serving_gate_decisions_total", {"model_family": "orion", "status": status, "scenario": scenario_id})
        metrics.observe_ms("serving_gate_candidate_ttft_ms", canary["p95_ttft_ms"] or 0, {"model_family": "orion", "model_version": candidate})
        audit.append({"event": "scenario_evaluated", "scenario": scenario_id, "status": status, "reason_count": len(reasons)})
    trace_audit = []
    scenario_rows = sorted(policy["scenarios"], key=lambda row: str(row["scenario_id"]))
    for row in sorted(bundle["trace"], key=lambda item: str(item.get("request_id", ""))):
        received = parse_time(str(row["received_at"]))
        visible = [str(item["scenario_id"]) for item in scenario_rows if received <= parse_time(str(item["evaluation_time"]))]
        trace_audit.append({
            "request_id": str(row.get("request_id", "")), "received_at": str(row["received_at"]), "model_version": str(row.get("model_version", "")),
            "tenant_class": str(row.get("tenant_class", "")), "status": str(row.get("status", "")),
            "visible_scenario_count": len(visible), "first_visible_scenario": visible[0] if visible else "",
        })
    decisions.sort(key=lambda row: row["scenario_id"])
    return {
        "decisions": decisions,
        "capacity": sorted(capacity_rows, key=lambda row: (row["scenario_id"], row["model_version"])),
        "replicas": sorted(replica_rows, key=lambda row: (row["scenario_id"], row["model_version"], row["replica_id"])),
        "drift": sorted(drift_rows, key=lambda row: (row["scenario_id"], row["slice"])),
        "tenants": sorted(tenant_rows, key=lambda row: (row["scenario_id"], row["tenant_class"])),
        "costs": sorted(cost_rows, key=lambda row: (row["scenario_id"], row["model_version"])),
        "execution": sorted(execution_rows, key=lambda row: row["scenario_id"]),
        "trace_audit": trace_audit,
        "validation": {
            "trace_rows": len(bundle["trace"]), "eval_rows": len(bundle["eval"]), "telemetry_rows": len(bundle["telemetry"]),
            "duplicate_request_ids": len(bundle["trace"]) - len({str(row.get("request_id")) for row in bundle["trace"]}),
            "required_scenarios": len(policy["scenarios"]), "required_slices": len(policy.get("required_slices", [])),
        },
        "source_reconciliation": _reconcile_claims(bundle["claims"], policy),
        "observability": metrics.snapshot(),
        "audit": [redact(row) for row in audit],
        "manifest": {
            "schema_version": "1",
            "baseline_version": baseline,
            "candidate_version": candidate,
            "scenario_count": len(decisions),
            "input_files": sorted(path.name for path in Path(input_dir).iterdir() if path.is_file()),
            "route_history_rows": len(bundle["routes"]),
        },
    }
