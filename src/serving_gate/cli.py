from __future__ import annotations

import argparse
import json
from pathlib import Path

from .engine import evaluate_bundle
from .io import write_csv, write_json


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Evaluate an offline LLM serving release bundle")
    parser.add_argument("--input", required=True, type=Path, help="directory containing raw evidence files")
    parser.add_argument("--output", required=True, type=Path, help="directory for deterministic decision artifacts")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    result = evaluate_bundle(args.input)
    args.output.mkdir(parents=True, exist_ok=True)
    write_csv(
        args.output / "decision_matrix.csv",
        result["decisions"],
        ["scenario_id", "evaluation_time", "status", "candidate_version", "stable_version", "candidate_canary_pct", "stable_route_pct", "traffic_rps", "approval", "route_flag_active", "active_incident", "quality_passed", "tenant_slos_passed", "input_psi", "candidate_capacity_rps", "stable_capacity_rps", "reasons"],
    )
    write_csv(args.output / "capacity_report.csv", result["capacity"], ["scenario_id", "evaluation_time", "model_version", "usable_replicas", "capacity_rps", "excluded", "telemetry_replicas"])
    write_csv(args.output / "capacity_replica_detail.csv", result["replicas"], ["scenario_id", "evaluation_time", "model_version", "replica_id", "included", "reason", "capacity_rps"])
    write_csv(args.output / "drift_findings.csv", result["drift"], ["scenario_id", "slice", "evaluated_rows", "valid_rows", "weighted_quality_delta", "psi", "critical", "passed", "reasons"])
    write_csv(args.output / "tenant_health.csv", result["tenants"], ["scenario_id", "evaluation_time", "tenant_class", "requests", "error_rate", "p95_ttft_ms", "passed"])
    write_csv(args.output / "cost_report.csv", result["costs"], ["scenario_id", "evaluation_time", "model_version", "requests", "input_tokens", "output_tokens", "estimated_usd"])
    write_csv(args.output / "execution_plan.csv", result["execution"], ["scenario_id", "status", "window_owner", "rollback_target", "window_valid"])
    write_csv(args.output / "source_reconciliation.csv", result["source_reconciliation"], ["claim_id", "subject", "claimed_value", "claimed_at", "source", "resolution"])
    write_json(args.output / "observability_contract.json", result["observability"])
    write_json(args.output / "run_manifest.json", result["manifest"])
    write_json(args.output / "input_validation.json", result["validation"])
    write_csv(args.output / "trace_audit.csv", result["trace_audit"], ["request_id", "received_at", "model_version", "tenant_class", "status", "visible_scenario_count", "first_visible_scenario"])
    (args.output / "audit.ndjson").write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in result["audit"]), encoding="utf-8")
    print(json.dumps({"scenarios": len(result["decisions"]), "output": str(args.output)}, ensure_ascii=False, sort_keys=True))
    return 0
