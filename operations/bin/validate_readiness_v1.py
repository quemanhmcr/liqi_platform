#!/usr/bin/env python3
"""Validate V1 readiness source contracts and fail-closed invariants."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

try:
    from operations.bin.readiness_v1_common import ROOT, exact_set, load_json, validate_document
except ModuleNotFoundError:
    from readiness_v1_common import ROOT, exact_set, load_json, validate_document

READINESS_DIR = ROOT / "contracts" / "readiness"
SLO_SCHEMA = ROOT / "contracts" / "operations" / "slo-v1.schema.json"
ALERT_SCHEMA = ROOT / "contracts" / "operations" / "alert-policy-v1.schema.json"
SLO = ROOT / "operations" / "slo" / "slo-v1.json"
ALERTS = ROOT / "operations" / "alerts" / "alert-policy-v1.json"
PROVIDER_SCHEMA = READINESS_DIR / "provider-gates-v1.schema.json"
PROVIDER_REGISTRY = ROOT / "operations" / "readiness" / "provider-gates-v1.json"
EVIDENCE_POLICY = ROOT / "operations" / "readiness" / "evidence-policy-v1.json"
SCENARIOS = ROOT / "operations" / "resilience" / "scenario-catalog-v1.json"
RESTORE_PLAN = ROOT / "operations" / "recovery" / "restore-drill-v1.json"
CUTOVER_POLICY = ROOT / "operations" / "deployment" / "cutover-policy-v1.json"

EXPECTED_SLO = {
    "api-availability", "durable-write-success", "api-latency-p95", "api-latency-p99",
    "commit-to-deliver-p95", "commit-to-deliver-p99", "outbox-age-p99", "oban-queue-age-p99",
    "realtime-resume-success", "slow-consumer-rate", "actor-mailbox-age-p99", "beam-run-queue-p99",
    "beam-scheduler-utilization-p99", "native-latency-p99", "native-fallback-error-rate",
    "db-pool-wait-p99", "backup-age", "wal-archive-lag", "restore-freshness",
    "disk-exhaustion-forecast", "memory-reserve",
}
EXPECTED_CORRECTNESS = {
    "authorization-bypass", "secret-exposure", "duplicate-durable-identity",
    "event-before-commit", "durable-event-loss",
}
EXPECTED_EVIDENCE = {
    "capacity", "platform-probe", "load", "reconnect", "recovery",
    "resilience", "security", "cutover", "rollback",
}
EXPECTED_CHECKPOINTS = {"source", "integration", "artifact", "live-staging", "promotion", "cutover", "post-cutover"}
EXPECTED_SCENARIOS = {
    "postgresql-restart", "pgbouncer-unavailable", "outbox-backlog", "oban-backlog",
    "realtime-slow-consumers", "reconnect-storm-25pct", "native-artifact-disabled",
    "native-kernel-panic", "telemetry-sink-unavailable", "disk-pressure", "beam-process-crash",
    "actor-supervisor-restart", "release-activation-failure", "v1-rollback-to-v0", "host-reboot",
}
EXPECTED_GATES = {
    "runtime-source", "runtime-integration", "runtime-artifact", "runtime-live-probe",
    "database-source", "database-integration", "database-recovery",
    "native-source", "native-safety", "native-artifact",
    "infrastructure-source", "infrastructure-plan", "host-readiness", "rollback-evidence",
}
FORBIDDEN_COMMANDS = (
    re.compile(r"\b(?:tofu|terraform)\s+apply\b", re.IGNORECASE),
    re.compile(r"\boci\s+[^\n]*(?:create|update|delete|terminate)\b", re.IGNORECASE),
    re.compile(r"\bsystemctl\s+(?:start|stop|restart|enable|disable)\b", re.IGNORECASE),
)


def require_path(value: str, label: str, failures: list[str]) -> None:
    path = ROOT / value
    if not path.exists():
        failures.append(f"{label} references missing path: {value}")


def validate_policy(policy: dict[str, Any], failures: list[str]) -> None:
    if policy.get("schema_version") != "evidence-policy-v1":
        failures.append("evidence policy schema_version must be evidence-policy-v1")
    if policy.get("exact_binding_required") is not True:
        failures.append("evidence policy must require exact SHA/release binding")
    if policy.get("synthetic_evidence_allowed_for_final_verdict") is not False:
        failures.append("synthetic evidence must be forbidden for the final verdict")
    if policy.get("blocked_is_passed") is not False:
        failures.append("blocked must never be classified as passed")
    failures.extend(exact_set((item.get("kind", "") for item in policy.get("evidence", [])), EXPECTED_EVIDENCE, "evidence policy kinds"))
    for item in policy.get("evidence", []):
        require_path(str(item.get("schema", "")), f"evidence policy {item.get('kind')}", failures)
        if not isinstance(item.get("max_age_seconds"), int) or item["max_age_seconds"] <= 0:
            failures.append(f"evidence policy {item.get('kind')} requires a positive max_age_seconds")
    supporting = {item.get("kind"): item for item in policy.get("supporting_evidence", [])}
    if set(supporting) != {"compatibility", "checkpoint", "oci-mutations"}:
        failures.append("supporting evidence must contain compatibility, checkpoint and oci-mutations exactly once")
    checkpoint = supporting.get("checkpoint", {})
    failures.extend(exact_set(checkpoint.get("required_names", []), EXPECTED_CHECKPOINTS, "checkpoint names"))
    for item in supporting.values():
        require_path(str(item.get("schema", "")), f"supporting evidence {item.get('kind')}", failures)


def validate_slo(document: dict[str, Any], failures: list[str]) -> None:
    failures.extend(validate_document(SLO_SCHEMA, document, "slo-v1"))
    failures.extend(exact_set((item.get("id", "") for item in document.get("objectives", [])), EXPECTED_SLO, "SLO objective IDs"))
    failures.extend(exact_set((item.get("id", "") for item in document.get("correctness_events", [])), EXPECTED_CORRECTNESS, "correctness event IDs"))
    for item in [*document.get("objectives", []), *document.get("correctness_events", [])]:
        require_path(str(item.get("runbook", "")), f"SLO {item.get('id')}", failures)
    for event in document.get("correctness_events", []):
        if event.get("budget") != 0 or event.get("tolerance") != "zero":
            failures.append(f"correctness event {event.get('id')} must have zero tolerance and zero budget")


def validate_registry(document: dict[str, Any], failures: list[str]) -> None:
    failures.extend(validate_document(PROVIDER_SCHEMA, document, "provider-gates-v1"))
    failures.extend(exact_set((item.get("id", "") for item in document.get("gates", [])), EXPECTED_GATES, "provider gate IDs"))
    for gate in document.get("gates", []):
        command = " ".join(str(part) for part in gate.get("argv", []))
        if "mock" in command.lower() or "fixture" in command.lower():
            failures.append(f"provider gate {gate.get('id')} cannot use mock or fixture commands")
        for pattern in FORBIDDEN_COMMANDS:
            if pattern.search(command):
                failures.append(f"provider gate {gate.get('id')} contains forbidden mutation command")
        state = gate.get("provider_state")
        branch = gate.get("provider_branch")
        commit = gate.get("provider_commit")
        if state == "available":
            for required in gate.get("required_paths", []):
                require_path(required, f"available provider gate {gate.get('id')}", failures)
            if not commit:
                failures.append(f"available provider gate {gate.get('id')} must retain its provider commit")
        elif state == "pending-integration":
            if not branch or not commit:
                failures.append(f"pending-integration gate {gate.get('id')} requires provider branch and exact commit")
            elif subprocess.run(["git", "cat-file", "-e", f"{commit}^{{commit}}"], cwd=ROOT, check=False, capture_output=True).returncode == 0:
                branch_result = subprocess.run(["git", "rev-parse", "--verify", branch], cwd=ROOT, check=False, capture_output=True, text=True)
                if branch_result.returncode == 0 and branch_result.stdout.strip() != commit:
                    failures.append(f"pending-integration gate {gate.get('id')} branch {branch} no longer points at {commit}")
                for required in gate.get("required_paths", []):
                    exists = subprocess.run(["git", "cat-file", "-e", f"{commit}:{required}"], cwd=ROOT, check=False, capture_output=True).returncode == 0
                    if not exists:
                        failures.append(f"pending-integration gate {gate.get('id')} commit {commit} is missing {required}")
        elif state == "pending-provider-publication" and commit is not None:
            failures.append(f"unpublished provider gate {gate.get('id')} cannot claim a provider commit")
        schema = gate.get("result_schema")
        if schema:
            require_path(schema, f"provider gate {gate.get('id')}", failures)
        if gate.get("mutation_class") == "approved-live-mutation":
            failures.append(f"provider gate {gate.get('id')} cannot register an automatic approved-live-mutation path")


def validate_scenarios(document: dict[str, Any], failures: list[str]) -> None:
    if document.get("schema_version") != "resilience-scenario-catalog-v1":
        failures.append("resilience catalog schema_version mismatch")
    failures.extend(exact_set((item.get("id", "") for item in document.get("scenarios", [])), EXPECTED_SCENARIOS, "resilience scenario IDs"))
    for scenario in document.get("scenarios", []):
        require_path(str(scenario.get("runbook", "")), f"scenario {scenario.get('id')}", failures)
        require_path(str(scenario.get("result_schema", "")), f"scenario {scenario.get('id')}", failures)
        if not scenario.get("data_safety"):
            failures.append(f"scenario {scenario.get('id')} must declare data safety")




def validate_load_harness(failures: list[str]) -> None:
    floor_path = ROOT / "tests" / "load" / "v1-floor.js"
    reconnect_path = ROOT / "tests" / "load" / "reconnect-storm-v1.js"
    for path in (floor_path, reconnect_path):
        if not path.is_file():
            failures.append(f"missing load harness: {path.relative_to(ROOT).as_posix()}")
            continue
        source = path.read_text(encoding="utf-8")
        if "k6/experimental/websockets" in source:
            failures.append(f"{path.name} uses deprecated experimental WebSockets")
        if "k6/websockets" not in source:
            failures.append(f"{path.name} must use k6/websockets")
        if "thresholds" not in source or "handleSummary" not in source:
            failures.append(f"{path.name} must declare thresholds and a machine-readable summary")
        if "Bearer " in source and "LIQI_AUTH_TOKEN" not in source:
            failures.append(f"{path.name} contains a hard-coded bearer credential path")
    if floor_path.is_file():
        source = floor_path.read_text(encoding="utf-8")
        for token in ("target: 2000", "ACTIVE_SUBSCRIPTIONS", "rate: 50", "rate: 500", "'30m'"):
            if token not in source:
                failures.append(f"v1-floor.js is missing acceptance-floor token: {token}")
    if reconnect_path.is_file():
        source = reconnect_path.read_text(encoding="utf-8")
        for token in ("target: 1500", "target: 500", "'60000'", "rate>0.99", "count==0"):
            if token not in source:
                failures.append(f"reconnect-storm-v1.js is missing acceptance token: {token}")

def main() -> int:
    failures: list[str] = []
    schemas = sorted(READINESS_DIR.glob("*.schema.json")) + [SLO_SCHEMA, ALERT_SCHEMA]
    for path in schemas:
        try:
            Draft202012Validator.check_schema(load_json(path))
        except Exception as exc:  # json/schema parse details are useful at this boundary
            failures.append(f"{path.relative_to(ROOT).as_posix()}: invalid schema: {exc}")

    documents: dict[Path, Any] = {}
    for path in (SLO, ALERTS, PROVIDER_REGISTRY, EVIDENCE_POLICY, SCENARIOS, RESTORE_PLAN, CUTOVER_POLICY):
        try:
            documents[path] = load_json(path)
        except (OSError, json.JSONDecodeError) as exc:
            failures.append(f"{path.relative_to(ROOT).as_posix()}: cannot load JSON: {exc}")

    if SLO in documents:
        validate_slo(documents[SLO], failures)
    if ALERTS in documents:
        alert_document = documents[ALERTS]
        failures.extend(validate_document(ALERT_SCHEMA, alert_document, "alert-policy-v1"))
        alert_ids = [item.get("id", "") for item in alert_document.get("alerts", [])]
        duplicates = sorted({value for value in alert_ids if alert_ids.count(value) > 1})
        if duplicates:
            failures.append(f"alert IDs contain duplicates: {duplicates}")
        required_alerts = EXPECTED_SLO | EXPECTED_CORRECTNESS
        missing_alerts = sorted(required_alerts - set(alert_ids))
        if missing_alerts:
            failures.append(f"alert policy is missing SLO/correctness alerts: {missing_alerts}")
        for item in alert_document.get("alerts", []):
            require_path(str(item.get("runbook", "")), f"alert {item.get('id')}", failures)
    if PROVIDER_REGISTRY in documents:
        validate_registry(documents[PROVIDER_REGISTRY], failures)
    if EVIDENCE_POLICY in documents:
        validate_policy(documents[EVIDENCE_POLICY], failures)
    if SCENARIOS in documents:
        validate_scenarios(documents[SCENARIOS], failures)
        if ALERTS in documents:
            alert_ids = {item.get("id") for item in documents[ALERTS].get("alerts", [])}
            missing_scenario_alerts = sorted({item.get("alert_id") for item in documents[SCENARIOS].get("scenarios", [])} - alert_ids)
            if missing_scenario_alerts:
                failures.append(f"alert policy is missing scenario alerts: {missing_scenario_alerts}")
    if RESTORE_PLAN in documents:
        plan = documents[RESTORE_PLAN]
        if plan.get("prohibitions") != ["restore-over-live", "production-traffic-change", "unapproved-oci-mutation"]:
            failures.append("restore drill must preserve all three mutation prohibitions in canonical order")
        require_path(str(plan.get("required_result_schema", "")), "restore drill", failures)
    validate_load_harness(failures)
    if CUTOVER_POLICY in documents:
        policy = documents[CUTOVER_POLICY]
        failures.extend(exact_set(policy.get("phases", []), {"shadow", "internal-only", "canary-route", "limited-client-cohort", "broader-cohort", "v1-default", "v0-rollback-window"}, "cutover phases"))
        if policy.get("big_bang_forbidden") is not True or policy.get("rollback_required_before_canary") is not True:
            failures.append("cutover policy must forbid big bang and require rollback proof before canary")
        require_path(str(policy.get("required_result_schema", "")), "cutover policy", failures)

    if failures:
        for failure in sorted(set(failures)):
            print(f"ERROR readiness-v1: {failure}", file=sys.stderr)
        return 1
    print(f"validated {len(schemas)} V1 schemas and readiness source invariants")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
