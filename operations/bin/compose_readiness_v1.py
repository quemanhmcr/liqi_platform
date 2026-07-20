#!/usr/bin/env python3
"""Compose exact-release V1 evidence into the sole final readiness verdict."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from operations.bin.readiness_v1_common import (
        ROOT,
        age_seconds,
        blocker,
        combine_status,
        exact_set,
        git_sha,
        load_json,
        parse_datetime,
        relative_ref,
        sha256_file,
        utc_now,
        validate_document,
        write_json,
    )
except ModuleNotFoundError:
    from readiness_v1_common import (
        ROOT,
        age_seconds,
        blocker,
        combine_status,
        exact_set,
        git_sha,
        load_json,
        parse_datetime,
        relative_ref,
        sha256_file,
        utc_now,
        validate_document,
        write_json,
    )

POLICY_PATH = ROOT / "operations" / "readiness" / "evidence-policy-v1.json"
FINAL_SCHEMA = ROOT / "contracts" / "readiness" / "v1-readiness-result.schema.json"
CHECKPOINT_NAMES = ("source", "integration", "artifact", "live-staging", "promotion", "cutover", "post-cutover")
EVIDENCE_OWNER = {
    "capacity": "Senior 5",
    "platform-probe": "Senior 1",
    "load": "Senior 5",
    "reconnect": "Senior 5",
    "recovery": "Senior 2",
    "resilience": "Senior 5",
    "security": "Senior 5",
    "cutover": "Senior 5",
    "release-recovery": "Senior 4",
}

PROBE_CHECKS = {"https-health", "runtime-readiness", "websocket-connect", "websocket-auth", "durable-command-commit", "outbox-handoff", "realtime-delivery", "resume-gap-repair", "worker-job", "native-kernel", "native-fallback"}
RECOVERY_STEPS = {"select-latest-valid-backup", "restore-isolated-target", "wal-pitr", "verify-migrations", "verify-platform-invariants", "elixir-read-only-probe", "cleanup"}
RESILIENCE_SCENARIOS = {"postgresql-restart", "pgbouncer-unavailable", "outbox-backlog", "oban-backlog", "realtime-slow-consumers", "reconnect-storm-25pct", "native-artifact-disabled", "native-kernel-panic", "telemetry-sink-unavailable", "disk-pressure", "beam-process-crash", "actor-supervisor-restart", "release-activation-failure", "first-release-deactivation-recovery", "host-reboot"}
SECURITY_CHECKS = {"public-ports", "tls", "secret-scan", "vault-references", "iam-least-privilege", "instance-principal", "private-database", "private-metrics", "systemd-hardening", "dependency-vulnerabilities", "license-policy", "sbom", "provenance", "log-redaction", "session-token-handling", "websocket-origin-auth", "rate-admission-limits", "native-input-bounds", "crash-dump-secret-hygiene"}
CUTOVER_PRECONDITIONS = {"source", "integration", "artifact", "live-staging", "capacity", "load-floor", "reconnect-storm", "recovery", "security", "release-recovery", "approved-plan", "exact-release-binding"}
CUTOVER_BUNDLE = {"platform-probe", "load", "reconnect", "recovery", "security", "release-recovery", "capacity", "artifact", "oci-plan"}
ROLLBACK_STEPS = {"stop-new-admission", "drain-sessions", "disable-traffic", "stop-release", "verify-no-public-traffic", "observe", "cleanup"}
CAPACITY_OWNERS = {"Senior 1", "Senior 2", "Senior 3", "Senior 4"}

def semantic_evidence_errors(kind: str, document: dict[str, Any]) -> list[str]:
    checks: list[str] = []
    if kind == "capacity":
        checks.extend(exact_set((item.get("owner", "") for item in document.get("provider_evidence", [])), CAPACITY_OWNERS, "capacity provider owners"))
    elif kind == "platform-probe":
        checks.extend(exact_set((item.get("name", "") for item in document.get("checks", [])), PROBE_CHECKS, "platform probe checks"))
        if document.get("observed_release_id") != document.get("release_id"):
            checks.append("platform probe observed_release_id must equal release_id")
    elif kind == "recovery":
        checks.extend(exact_set((item.get("name", "") for item in document.get("steps", [])), RECOVERY_STEPS, "recovery steps"))
    elif kind == "resilience":
        checks.extend(exact_set((item.get("id", "") for item in document.get("scenarios", [])), RESILIENCE_SCENARIOS, "resilience scenarios"))
    elif kind == "security":
        checks.extend(exact_set((item.get("name", "") for item in document.get("checks", [])), SECURITY_CHECKS, "security checks"))
    elif kind == "cutover":
        checks.extend(exact_set((item.get("name", "") for item in document.get("preconditions", [])), CUTOVER_PRECONDITIONS, "cutover preconditions"))
        checks.extend(exact_set((item.get("kind", "") for item in document.get("evidence_bundle", [])), CUTOVER_BUNDLE, "cutover evidence bundle"))
    elif kind == "release-recovery":
        checks.extend(exact_set((item.get("name", "") for item in document.get("steps", [])), ROLLBACK_STEPS, "release recovery steps"))
    return checks

CORRECTNESS_OWNER = {
    "authorization-bypass": "Senior 1",
    "secret-exposure": "Senior 5",
    "duplicate-durable-identity": "Senior 2",
    "event-before-commit": "Senior 2",
    "durable-event-loss": "Senior 2",
}
COMPATIBILITY_OWNER = {
    "migrations-expand-compatible": "Senior 2",
    "realtime-protocol-negotiated": "Senior 1",
    "native-fallback-available": "Senior 3",
    "config-versioned": "Senior 1",
}


def parse_assignments(values: list[str], label: str) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for value in values:
        if "=" not in value:
            raise ValueError(f"{label} must use name=path: {value}")
        name, raw_path = value.split("=", 1)
        if not name or not raw_path:
            raise ValueError(f"{label} must use non-empty name=path: {value}")
        if name in result:
            raise ValueError(f"duplicate {label} name: {name}")
        result[name] = Path(raw_path).resolve()
    return result


def observed_at(document: dict[str, Any]) -> str | None:
    for key in ("observed_at", "completed_at", "generated_at"):
        value = document.get(key)
        if isinstance(value, str):
            return value
    return None


def evidence_placeholder(kind: str, owner: str, sha: str, release_id: str, status: str, now: str) -> dict[str, Any]:
    return {
        "kind": kind,
        "owner": owner,
        "schema_version": f"missing-{kind}-v1",
        "status": status,
        "evidence_mode": "synthetic",
        "git_sha": sha,
        "release_id": release_id,
        "observed_at": now,
        "sha256": "0" * 64,
        "evidence_ref": f"missing:{kind}",
    }


def correctness_counts(documents: dict[str, dict[str, Any]]) -> dict[str, int]:
    counts = {name: 0 for name in CORRECTNESS_OWNER}
    mapping = {name.replace("-", "_"): name for name in counts}
    for document in documents.values():
        direct = document.get("correctness_events")
        if isinstance(direct, dict):
            for key, value in direct.items():
                target = mapping.get(key)
                if target and isinstance(value, int):
                    counts[target] += value
        for container_name in ("outcomes", "data_safety"):
            container = document.get(container_name)
            if isinstance(container, dict):
                for key, value in container.items():
                    target = mapping.get(key)
                    if target and isinstance(value, int):
                        counts[target] += value
    return counts


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--policy", type=Path, default=POLICY_PATH)
    parser.add_argument("--git-sha", default=None)
    parser.add_argument("--release-id", required=True)
    parser.add_argument("--environment", choices=("staging", "production"), required=True)
    parser.add_argument("--evidence", action="append", default=[], help="kind=path")
    parser.add_argument("--checkpoint", action="append", default=[], help="name=path")
    parser.add_argument("--compatibility", type=Path)
    parser.add_argument("--oci-mutations", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--allow-not-ready", action="store_true")
    parser.add_argument("--now", help="UTC date-time override for deterministic tests")
    args = parser.parse_args()

    try:
        evidence_paths = parse_assignments(args.evidence, "evidence")
        checkpoint_paths = parse_assignments(args.checkpoint, "checkpoint")
    except ValueError as exc:
        print(f"ERROR compose-readiness-v1: {exc}", file=sys.stderr)
        return 64

    sha = args.git_sha or git_sha()
    if len(sha) != 40:
        print("ERROR compose-readiness-v1: --git-sha must be a 40-character SHA", file=sys.stderr)
        return 64
    now_dt = parse_datetime(args.now) if args.now else datetime.now(timezone.utc)
    now_text = now_dt.isoformat().replace("+00:00", "Z")
    policy = load_json(args.policy.resolve())
    policy_by_kind = {item["kind"]: item for item in policy["evidence"]}
    supporting = {item["kind"]: item for item in policy["supporting_evidence"]}
    blockers: list[dict[str, str]] = []
    primary_documents: dict[str, dict[str, Any]] = {}
    evidence_entries: list[dict[str, Any]] = []
    status_inputs: list[str] = []

    unexpected = sorted(set(evidence_paths) - set(policy_by_kind))
    if unexpected:
        print(f"ERROR compose-readiness-v1: unexpected evidence kinds: {unexpected}", file=sys.stderr)
        return 64

    for kind, rule in policy_by_kind.items():
        owner = EVIDENCE_OWNER[kind]
        path = evidence_paths.get(kind)
        if path is None:
            status_inputs.append("blocked")
            evidence_entries.append(evidence_placeholder(kind, owner, sha, args.release_id, "blocked", now_text))
            blockers.append(blocker(owner, f"{kind} evidence", "REQUIRED_EVIDENCE_MISSING", "blocked", f"required {kind} evidence is missing", f"Publish live {kind} evidence for {sha}/{args.release_id}."))
            continue
        try:
            document = load_json(path)
        except (OSError, json.JSONDecodeError) as exc:
            status_inputs.append("failed")
            evidence_entries.append(evidence_placeholder(kind, owner, sha, args.release_id, "failed", now_text))
            blockers.append(blocker(owner, relative_ref(path), "EVIDENCE_UNREADABLE", "failed", str(exc), "Repair and republish the owning evidence artifact."))
            continue

        errors = validate_document(ROOT / rule["schema"], document, kind)
        errors.extend(semantic_evidence_errors(kind, document))
        if document.get("git_sha") != sha:
            errors.append(f"{kind}.git_sha must equal {sha}")
        if document.get("release_id") != args.release_id:
            errors.append(f"{kind}.release_id must equal {args.release_id}")
        if document.get("environment") not in {None, args.environment}:
            errors.append(f"{kind}.environment must equal {args.environment}")
        timestamp = observed_at(document)
        if timestamp is None:
            errors.append(f"{kind} has no observed_at/completed_at timestamp")
            timestamp = now_text
        else:
            try:
                parsed = parse_datetime(timestamp)
                if parsed > now_dt.replace(microsecond=0) and (parsed - now_dt).total_seconds() > 300:
                    errors.append(f"{kind} timestamp is more than 300 seconds in the future")
                if age_seconds(timestamp, now=now_dt) > int(rule["max_age_seconds"]):
                    blockers.append(blocker(owner, relative_ref(path), "EVIDENCE_STALE", "blocked", f"{kind} evidence exceeds {rule['max_age_seconds']} seconds", "Regenerate evidence for the exact release."))
            except ValueError as exc:
                errors.append(f"{kind} timestamp invalid: {exc}")

        document_status = document.get("status", "failed")
        if document_status not in {"passed", "blocked", "failed"}:
            document_status = "failed"
        entry_status = "failed" if errors else document_status
        if not errors and document_status == "passed" and document.get("evidence_mode") != "live":
            errors.append(f"{kind} passed evidence must use evidence_mode=live")
            entry_status = "failed"
        if errors:
            blockers.append(blocker(owner, relative_ref(path), "EVIDENCE_INVALID", "failed", "; ".join(errors), "Repair the provider or Senior 5 evidence producer; do not bypass schema or binding checks."))
        elif document_status != "passed":
            blockers.append(blocker(owner, relative_ref(path), "EVIDENCE_NOT_PASSED", document_status, f"{kind} evidence status is {document_status}", "Resolve the recorded evidence failures and regenerate the exact-release result."))
        primary_documents[kind] = document
        status_inputs.append(entry_status)
        evidence_entries.append({
            "kind": kind,
            "owner": owner,
            "schema_version": str(document.get("schema_version", f"unknown-{kind}")),
            "status": entry_status,
            "evidence_mode": str(document.get("evidence_mode", "synthetic")),
            "git_sha": sha,
            "release_id": args.release_id,
            "observed_at": timestamp,
            "sha256": sha256_file(path),
            "evidence_ref": relative_ref(path),
        })

    # Checkpoint evidence.
    checkpoint_rule = supporting["checkpoint"]
    checkpoint_schema = ROOT / checkpoint_rule["schema"]
    checkpoint_summaries: list[dict[str, Any]] = []
    unexpected_checkpoints = sorted(set(checkpoint_paths) - set(CHECKPOINT_NAMES))
    if unexpected_checkpoints:
        print(f"ERROR compose-readiness-v1: unexpected checkpoint names: {unexpected_checkpoints}", file=sys.stderr)
        return 64
    for name in CHECKPOINT_NAMES:
        path = checkpoint_paths.get(name)
        if path is None:
            status_inputs.append("blocked")
            checkpoint_summaries.append({"name": name, "status": "blocked", "evidence_refs": []})
            blockers.append(blocker("Senior 5", f"{name} checkpoint", "CHECKPOINT_EVIDENCE_MISSING", "blocked", f"required {name} checkpoint is missing", "Run the registered provider gates and retain the checkpoint artifact."))
            continue
        try:
            document = load_json(path)
            errors = validate_document(checkpoint_schema, document, name)
        except (OSError, json.JSONDecodeError) as exc:
            document = {}
            errors = [str(exc)]
        if document.get("name") != name:
            errors.append(f"checkpoint name must equal {name}")
        if document.get("git_sha") != sha:
            errors.append(f"checkpoint git_sha must equal {sha}")
        if document.get("release_id") != args.release_id:
            errors.append(f"checkpoint release_id must equal {args.release_id}")
        timestamp = document.get("observed_at")
        if isinstance(timestamp, str):
            try:
                if age_seconds(timestamp, now=now_dt) > int(checkpoint_rule["max_age_seconds"]):
                    blockers.append(blocker("Senior 5", relative_ref(path), "CHECKPOINT_STALE", "blocked", f"{name} checkpoint is stale", "Regenerate the checkpoint for the exact release."))
            except ValueError as exc:
                errors.append(str(exc))
        else:
            errors.append("checkpoint observed_at is missing")
        status = "failed" if errors else document.get("status", "failed")
        status_inputs.append(status)
        checkpoint_summaries.append({"name": name, "status": status, "evidence_refs": document.get("evidence_refs", []) if not errors else [relative_ref(path)]})
        if errors:
            blockers.append(blocker("Senior 5", relative_ref(path), "CHECKPOINT_INVALID", "failed", "; ".join(errors), "Regenerate the checkpoint with exact provider outputs."))
        else:
            blockers.extend(document.get("blockers", []))

    # Cross-version compatibility.
    compatibility_values = {
        "migrations_expand_compatible": False,
        "realtime_protocol_negotiated": False,
        "native_fallback_available": False,
        "config_versioned": False,
    }
    compatibility_status = "blocked"
    if args.compatibility is None:
        blockers.append(blocker("Senior 5", "compatibility-result-v1", "COMPATIBILITY_EVIDENCE_MISSING", "blocked", "release compatibility evidence is missing", "Compose provider compatibility checks for migrations, realtime, native fallback and configuration."))
    else:
        path = args.compatibility.resolve()
        try:
            document = load_json(path)
            errors = validate_document(ROOT / supporting["compatibility"]["schema"], document, "compatibility")
        except (OSError, json.JSONDecodeError) as exc:
            document = {}
            errors = [str(exc)]
        if document.get("git_sha") != sha:
            errors.append(f"compatibility git_sha must equal {sha}")
        if document.get("release_id") != args.release_id:
            errors.append(f"compatibility release_id must equal {args.release_id}")
        timestamp = document.get("observed_at")
        if isinstance(timestamp, str):
            try:
                if age_seconds(timestamp, now=now_dt) > int(supporting["compatibility"]["max_age_seconds"]):
                    blockers.append(blocker("Senior 5", relative_ref(path), "COMPATIBILITY_STALE", "blocked", "compatibility evidence is stale", "Regenerate compatibility evidence for the exact release."))
            except ValueError as exc:
                errors.append(str(exc))
        else:
            errors.append("compatibility observed_at is missing")
        errors.extend(exact_set((item.get("name", "") for item in document.get("checks", [])), set(COMPATIBILITY_OWNER), "compatibility checks"))
        compatibility_status = "failed" if errors else document.get("status", "failed")
        checks = {item.get("name"): item for item in document.get("checks", [])}
        compatibility_values = {
            "migrations_expand_compatible": checks.get("migrations-expand-compatible", {}).get("status") == "passed",
            "realtime_protocol_negotiated": checks.get("realtime-protocol-negotiated", {}).get("status") == "passed",
            "native_fallback_available": checks.get("native-fallback-available", {}).get("status") == "passed",
            "config_versioned": checks.get("config-versioned", {}).get("status") == "passed",
        }
        if errors:
            blockers.append(blocker("Senior 5", relative_ref(path), "COMPATIBILITY_INVALID", "failed", "; ".join(errors), "Repair the owning compatibility evidence."))
        else:
            for check_name, check in checks.items():
                if check.get("status") != "passed":
                    blockers.append(blocker(COMPATIBILITY_OWNER.get(check_name, "Senior 5"), check_name, "COMPATIBILITY_NOT_PASSED", check.get("status", "failed"), f"compatibility check is {check.get('status')}", "Repair the owning provider seam and republish compatibility evidence."))
    status_inputs.append(compatibility_status)

    # Approved OCI/live mutation evidence.
    mutations: list[dict[str, Any]] = []
    mutation_status = "blocked"
    if args.oci_mutations is None:
        blockers.append(blocker("Senior 4", "oci-mutation-log-v1", "MUTATION_EVIDENCE_MISSING", "blocked", "approved OCI/live mutation evidence is missing", "Publish the Senior 4 mutation log with approval references and exact release binding."))
    else:
        path = args.oci_mutations.resolve()
        try:
            document = load_json(path)
            errors = validate_document(ROOT / supporting["oci-mutations"]["schema"], document, "oci-mutations")
        except (OSError, json.JSONDecodeError) as exc:
            document = {}
            errors = [str(exc)]
        if document.get("git_sha") != sha:
            errors.append(f"mutation log git_sha must equal {sha}")
        if document.get("release_id") != args.release_id:
            errors.append(f"mutation log release_id must equal {args.release_id}")
        if document.get("environment") != args.environment:
            errors.append(f"mutation log environment must equal {args.environment}")
        timestamp = document.get("observed_at")
        if isinstance(timestamp, str):
            try:
                if age_seconds(timestamp, now=now_dt) > int(supporting["oci-mutations"]["max_age_seconds"]):
                    blockers.append(blocker("Senior 4", relative_ref(path), "MUTATION_EVIDENCE_STALE", "blocked", "mutation evidence is stale", "Republish the exact-release mutation log."))
            except ValueError as exc:
                errors.append(str(exc))
        else:
            errors.append("mutation log observed_at is missing")
        mutation_status = "failed" if errors else document.get("status", "failed")
        if errors:
            blockers.append(blocker("Senior 4", relative_ref(path), "MUTATION_EVIDENCE_INVALID", "failed", "; ".join(errors), "Repair the Senior 4 mutation log; no approval may be inferred."))
        else:
            for item in document.get("mutations", []):
                mutations.append({key: item[key] for key in ("kind", "approved", "approval_ref", "executed_by", "evidence_ref")})
            if mutation_status != "passed":
                blockers.append(blocker("Senior 4", relative_ref(path), "MUTATION_EVIDENCE_NOT_PASSED", mutation_status, f"mutation log status is {mutation_status}", "Resolve approval or execution evidence before cutover."))
    status_inputs.append(mutation_status)

    counts = correctness_counts(primary_documents)
    correctness = []
    for name, count in counts.items():
        correctness.append({"name": name, "count": count, "evidence_ref": "aggregated:required-v1-evidence"})
        if count > 0:
            blockers.append(blocker(CORRECTNESS_OWNER[name], name, "ZERO_TOLERANCE_EVENT", "failed", f"observed {count} {name} event(s)", "Stop promotion/cutover, remediate the owning seam and generate new exact-release evidence."))
            status_inputs.append("failed")

    capacity = primary_documents.get("capacity", {})
    host = capacity.get("host", {"shape": "VM.Standard.A1.Flex", "ocpu": 4, "memory_mib": 24576, "combined_storage_gib": 200})
    reserve = capacity.get("reserve", {"ocpu": 1, "memory_mib": 4096, "disk_gib": 20})
    release_recovery = primary_documents.get("release-recovery", {})
    recovery_to = release_recovery.get("to")
    retained = isinstance(recovery_to, dict) and recovery_to.get("retained") is True
    recovery_target = {
        "mode": str(release_recovery.get("recovery_mode", "deactivate-first-release")),
        "retained_application_release": retained,
        "release_id": recovery_to.get("release_id") if retained else None,
        "git_sha": recovery_to.get("git_sha") if retained else None,
        "runtime_generation": recovery_to.get("runtime_generation") if retained else None,
        "evidence_ref": next((entry["evidence_ref"] for entry in evidence_entries if entry["kind"] == "release-recovery"), "missing:release-recovery"),
    }

    # Staleness blockers added above must affect status even when the document says passed.
    if any(item["severity"] == "failed" for item in blockers):
        overall = "failed"
    elif blockers:
        overall = "blocked"
    else:
        overall = combine_status(status_inputs)
    if overall == "passed" and any(value is False for value in compatibility_values.values()):
        overall = "failed"
        blockers.append(blocker("Senior 5", "compatibility-result-v1", "COMPATIBILITY_STATUS_INCONSISTENT", "failed", "passed composition contains a false compatibility value", "Repair compatibility composition."))

    unique: dict[tuple[str, str, str, str], dict[str, str]] = {}
    for item in blockers:
        unique[(item["owner"], item["seam"], item["code"], item["message"])] = item
    blockers = sorted(unique.values(), key=lambda item: (item["severity"], item["owner"], item["code"], item["seam"]))

    result = {
        "schema_version": "v1-readiness-result",
        "generated_at": now_text,
        "git_sha": sha,
        "release_id": args.release_id,
        "environment": args.environment,
        "status": overall,
        "verdict": "V1 PRODUCTION-SHAPED ON OCI" if overall == "passed" else "V1 NOT READY",
        "host": {
            "shape": host.get("shape", "VM.Standard.A1.Flex"),
            "ocpu": host.get("ocpu", 4),
            "memory_mib": host.get("memory_mib", 24576),
            "combined_storage_gib": host.get("combined_storage_gib", 200),
            "reserved_ocpu": reserve.get("ocpu", 1),
            "reserved_memory_mib": reserve.get("memory_mib", 4096),
            "reserved_disk_gib": reserve.get("disk_gib", 20),
        },
        "checkpoints": checkpoint_summaries,
        "evidence": evidence_entries,
        "correctness_events": correctness,
        "compatibility": compatibility_values,
        "recovery_target": recovery_target,
        "oci_mutations": mutations,
        "blockers": blockers,
    }
    final_errors = validate_document(FINAL_SCHEMA, result, "v1-readiness-result")
    if final_errors:
        for message in final_errors:
            print(f"ERROR compose-readiness-v1: {message}", file=sys.stderr)
        return 65
    write_json(args.output.resolve(), result)
    print(f"{result['verdict']} ({overall}) -> {args.output}")
    if overall == "failed":
        return 0 if args.allow_not_ready else 1
    if overall == "blocked":
        return 0 if args.allow_not_ready else 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
