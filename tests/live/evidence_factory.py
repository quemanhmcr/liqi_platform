"""Test-only V1 evidence builders.

These documents exercise the readiness control plane and are created only in
temporary directories. They are never a provider command or production evidence
path and are deliberately not committed as live JSON artifacts.
"""
from __future__ import annotations

SHA = "a" * 40
ROLLBACK_SHA = "b" * 40
RELEASE_ID = "liqi-v1-test-aaaaaaaaaaaa"
ROLLBACK_RELEASE_ID = "liqi-v0-rollback"
NOW = "2026-07-18T00:00:00Z"
START = "2026-07-17T23:00:00Z"
ENVIRONMENT = "production"

PROBE_CHECKS = [
    "https-health", "runtime-readiness", "websocket-connect", "websocket-auth",
    "durable-command-commit", "outbox-handoff", "realtime-delivery",
    "resume-gap-repair", "worker-job", "native-kernel", "native-fallback",
]
SECURITY_CHECKS = [
    "public-ports", "tls", "secret-scan", "vault-references", "iam-least-privilege",
    "instance-principal", "private-database", "private-metrics", "systemd-hardening",
    "dependency-vulnerabilities", "license-policy", "sbom", "provenance", "log-redaction",
    "session-token-handling", "websocket-origin-auth", "rate-admission-limits",
    "native-input-bounds", "crash-dump-secret-hygiene",
]
SCENARIOS = [
    "postgresql-restart", "pgbouncer-unavailable", "outbox-backlog", "oban-backlog",
    "realtime-slow-consumers", "reconnect-storm-25pct", "native-artifact-disabled",
    "native-kernel-panic", "telemetry-sink-unavailable", "disk-pressure", "beam-process-crash",
    "actor-supervisor-restart", "release-activation-failure", "first-release-deactivation-recovery", "host-reboot",
]
RECOVERY_STEPS = [
    "select-latest-valid-backup", "restore-isolated-target", "wal-pitr", "verify-migrations",
    "verify-platform-invariants", "elixir-read-only-probe", "cleanup",
]
ROLLBACK_STEPS = [
    "stop-new-admission", "drain-sessions", "disable-traffic", "stop-release",
    "verify-no-public-traffic", "observe", "cleanup",
]
CHECKPOINTS = ["source", "integration", "artifact", "live-staging", "promotion", "cutover", "post-cutover"]


def metric(unit: str, value: float = 1.0) -> dict:
    return {"p50": value, "p95": value, "p99": value, "max": value, "unit": unit}


def zero_correctness() -> dict:
    return {"authorization_bypass": 0, "secret_exposure": 0, "duplicate_durable_identity": 0, "event_before_commit": 0, "durable_event_loss": 0}


def capacity() -> dict:
    return {
        "schema_version": "capacity-result-v1", "evidence_mode": "live", "git_sha": SHA,
        "release_id": RELEASE_ID, "environment": ENVIRONMENT, "observed_at": NOW, "status": "passed",
        "host": {"shape": "VM.Standard.A1.Flex", "ocpu": 4, "memory_mib": 24576, "combined_storage_gib": 200},
        "totals": {"ocpu": 2.8, "memory_mib": 19000, "disk_gib": 170},
        "reserve": {"ocpu": 1.2, "memory_mib": 5576, "disk_gib": 30},
        "beam": {"schedulers": 3, "dirty_cpu_schedulers": 2, "dirty_io_schedulers": 2, "async_threads": 8, "scheduler_bind_type": "default", "max_processes": 1048576},
        "database": {"postgres_memory_mib": 8192, "max_connections": 80, "pgbouncer_pool_mode": "transaction", "pgbouncer_server_connections": 32, "ecto_pool_size": 16, "oban_concurrency": 8},
        "native": {"memory_mib": 512, "max_concurrency": 4, "regular_nif_budget_us": 500, "dirty_cpu_max_concurrency": 2, "fallback_enabled": True},
        "realtime": {"websocket_per_connection_bytes": 32768, "max_connections": 5000, "outbound_queue_capacity": 256, "outbound_max_age_ms": 5000, "overflow_policy": "disconnect-and-resume"},
        "actors": {"mailbox_capacity": 256, "mailbox_max_age_ms": 1000, "partition_count": 32, "dynamic_supervisor_child_limit": 10000, "ets_memory_mib": 1024},
        "oban": {"total_concurrency": 8, "queue_limits": {"default": 4, "maintenance": 4}, "retry_budget": 10},
        "telemetry": {"memory_mib": 256, "log_disk_gib": 10, "metric_cardinality_limit": 10000, "drop_policy": "sample-or-drop-before-workload-impact"},
        "provider_evidence": [{"owner": f"Senior {n}", "status": "passed", "evidence_ref": f"provider:{n}"} for n in range(1, 5)],
        "failures": [],
    }


def platform_probe() -> dict:
    return {
        "schema_version": "live-platform-probe-v1", "evidence_mode": "live", "git_sha": SHA,
        "release_id": RELEASE_ID, "observed_release_id": RELEASE_ID, "environment": ENVIRONMENT,
        "endpoint": "https://staging.invalid", "started_at": START, "completed_at": NOW, "status": "passed",
        "checks": [{"name": name, "owner": "Senior 1", "status": "passed", "duration_ms": 1, "evidence_ref": f"probe:{name}", "failure_class": None} for name in PROBE_CHECKS],
        "correctness_events": zero_correctness(), "errors": [],
    }


def load_result() -> dict:
    return {
        "schema_version": "load-result-v1", "evidence_mode": "live", "git_sha": SHA, "release_id": RELEASE_ID,
        "environment": ENVIRONMENT, "profile": "floor",
        "host": {"shape": "VM.Standard.A1.Flex", "ocpu": 4, "memory_mib": 24576, "combined_storage_gib": 200},
        "dataset": {"id": "readiness-v1", "revision": "1", "seed": 42, "records": 10000},
        "generator": {"tool": "k6", "version": "2.0.0", "source_sha256": "c" * 64},
        "started_at": START, "completed_at": NOW, "status": "passed",
        "workload": {"concurrent_websocket_sessions": 2000, "active_subscriptions": 200, "durable_commands_per_second": 50, "realtime_events_per_second": 500, "reconnect_fraction": 0.25, "reconnect_window_seconds": 60, "steady_duration_seconds": 1800, "arrival_pattern": "ramp-then-steady"},
        "latency": {"api_ms": metric("milliseconds", 10), "commit_to_deliver_ms": metric("milliseconds", 20), "db_pool_wait_ms": metric("milliseconds", 2), "native_ms": metric("milliseconds", 0.2)},
        "resources": {
            "cpu_percent": metric("percent", 60), "memory_mib": metric("mebibytes", 18000), "disk_used_gib": metric("gibibytes", 160),
            "reserve_ocpu": 1.2, "reserve_memory_mib": 5000, "disk_free_gib": 30,
            "beam_scheduler_utilization_percent": metric("percent", 70), "beam_run_queue": metric("count", 2),
            "actor_mailbox_depth": metric("count", 4), "actor_mailbox_age_ms": metric("milliseconds", 10),
            "ets_memory_mib": metric("mebibytes", 500), "postgres_connections": metric("count", 30),
            "pgbouncer_pool_wait_ms": metric("milliseconds", 2), "outbox_age_ms": metric("milliseconds", 20),
            "oban_queue_age_ms": metric("milliseconds", 30), "native_memory_mib": metric("mebibytes", 100),
            "native_inflight": metric("count", 2), "telemetry_dropped": 0,
        },
        "outcomes": {"requests": 1000000, "errors": 0, "rejections": 100, "durable_event_loss": 0, "authorization_bypass": 0, "secret_exposure": 0},
        "recovery_after_load": {"status": "passed", "recovered_within_seconds": 60, "outbox_age_ms": 0, "run_queue": 0, "memory_returned_to_baseline_percent": 95},
        "evidence_refs": ["k6:summary", "otel:host"], "failures": [],
    }


def reconnect() -> dict:
    return {
        "schema_version": "reconnect-storm-v1", "evidence_mode": "live", "git_sha": SHA, "release_id": RELEASE_ID,
        "environment": ENVIRONMENT, "started_at": START, "completed_at": NOW, "status": "passed",
        "baseline_sessions": 2000, "disconnected_sessions": 500, "reconnect_window_seconds": 60,
        "reconnected_sessions": 500, "resume_success_ratio": 1.0, "gap_repairs": 10, "duplicate_deliveries": 0,
        "durable_event_loss": 0, "command_plane_error_ratio": 0.0, "recovered_within_seconds": 120,
        "metrics_ref": "otel:reconnect", "failures": [],
    }


def recovery() -> dict:
    return {
        "schema_version": "recovery-result-v1", "evidence_mode": "live", "git_sha": SHA, "release_id": RELEASE_ID,
        "environment": ENVIRONMENT, "exercise_id": "restore-v1-test", "started_at": START, "completed_at": NOW,
        "status": "passed", "approval_ref": "approval:restore",
        "source": {"database_id": "liqi-live", "schema_version": "v1", "release_id": RELEASE_ID, "mutated": False},
        "target": {"database_id": "liqi_restore_test", "isolated": True, "publicly_reachable": False},
        "backup": {"backup_id": "backup-test", "created_at": START, "sha256": "d" * 64, "wal_end": "0/ABC", "repository_ref": "object:backup"},
        "objectives": {"max_rpo_seconds": 300, "max_rto_seconds": 3600},
        "observed": {"rpo_seconds": 10, "rto_seconds": 600, "backup_age_seconds": 3600, "restore_freshness_seconds": 0},
        "steps": [{"name": name, "owner": "Senior 2", "status": "passed", "duration_ms": 10, "evidence_ref": f"restore:{name}"} for name in RECOVERY_STEPS],
        "compatibility": {"release_schema_compatible": True, "migrations_expand_compatible": True, "v0_rollback_readable": True},
        "mutations": {"isolated_target_mutated": True, "source_database_mutated": False, "production_traffic_changed": False, "oci_mutated": False},
        "cleanup": {"required": True, "status": "passed", "evidence_ref": "restore:cleanup"}, "failures": [],
    }


def scenario_result(identifier: str) -> dict:
    return {
        "schema_version": "resilience-result-v1", "evidence_mode": "live", "git_sha": SHA, "release_id": RELEASE_ID,
        "environment": ENVIRONMENT, "scenario_id": identifier, "started_at": START, "completed_at": NOW, "status": "passed",
        "expected": {"degradation": "bounded", "user_visible_effect": "documented", "data_safety": "zero loss", "max_recovery_seconds": 600},
        "observed": {"degradation": "bounded", "user_visible_effect": "documented", "recovered": True},
        "data_safety": {"durable_event_loss": 0, "event_before_commit": 0, "duplicate_durable_identity": 0},
        "alert": {"fired": True, "alert_id": "test-alert", "evidence_ref": "alert:test"},
        "runbook": "operations/runbooks/cutover-rollback-v1.md", "recovery_seconds": 60,
        "evidence_refs": [f"scenario:{identifier}"], "failures": [],
    }


def resilience_suite() -> dict:
    return {
        "schema_version": "resilience-suite-result-v1", "evidence_mode": "live", "git_sha": SHA,
        "release_id": RELEASE_ID, "environment": ENVIRONMENT, "started_at": START, "completed_at": NOW,
        "status": "passed", "scenarios": [{"id": name, "owner": "Senior 5", "status": "passed", "sha256": "e" * 64, "evidence_ref": f"scenario:{name}"} for name in SCENARIOS],
        "correctness_events": zero_correctness(), "failures": [],
    }


def security() -> dict:
    return {
        "schema_version": "security-result-v1", "evidence_mode": "live", "git_sha": SHA, "release_id": RELEASE_ID,
        "environment": ENVIRONMENT, "started_at": START, "completed_at": NOW, "status": "passed",
        "checks": [{"name": name, "owner": "Senior 5", "status": "passed", "evidence_ref": f"security:{name}"} for name in SECURITY_CHECKS],
        "findings": [], "correctness_events": zero_correctness(), "failures": [],
    }


def rollback() -> dict:
    return {
        "schema_version": "rollback-result-v1", "evidence_mode": "live", "git_sha": SHA, "release_id": RELEASE_ID,
        "environment": ENVIRONMENT, "exercise_id": "rollback-v1-test", "started_at": START, "completed_at": NOW, "status": "passed",
        "from": {"release_id": RELEASE_ID, "git_sha": SHA, "runtime_generation": "v1-beam"},
        "recovery_mode": "deactivate-first-release",
        "to": None,
        "trigger": "exercise", "steps": [{"name": name, "status": "passed", "duration_ms": 10, "evidence_ref": f"recovery:{name}"} for name in ROLLBACK_STEPS],
        "resume": None,
        "database": {"schema_compatible": True, "rollback_migration_required": False, "authority_unchanged": True},
        "data_safety": {"durable_event_loss": 0, "duplicate_durable_identity": 0, "event_before_commit": 0},
        "recovered_within_seconds": 60, "observation_seconds": 300, "evidence_refs": ["rollback:test"], "failures": [],
    }


def cutover() -> dict:
    preconditions = ["source", "integration", "artifact", "live-staging", "capacity", "load-floor", "reconnect-storm", "recovery", "security", "release-recovery", "approved-plan", "exact-release-binding"]
    bundle = ["platform-probe", "load", "reconnect", "recovery", "security", "release-recovery", "capacity", "artifact", "oci-plan"]
    return {
        "schema_version": "cutover-result-v1", "evidence_mode": "live", "git_sha": SHA, "release_id": RELEASE_ID,
        "environment": "production", "cutover_id": "cutover-v1-test", "phase": "v1-default", "previous_phase": "broader-cohort",
        "started_at": START, "completed_at": NOW, "status": "passed",
        "traffic": {"route": "default", "cohort": "all", "percent": 100, "session_drain_used": True, "resume_aware_reconnect": True},
        "preconditions": [{"name": name, "status": "passed", "evidence_ref": f"precondition:{name}"} for name in preconditions],
        "recovery": {"mode": "deactivate-first-release", "status": "passed", "target_release_id": None, "result_ref": "release-recovery:test"},
        "observation": {"duration_seconds": 1800, "slo_status": "passed", "api_errors": 0, "realtime_latency_p99_ms": 100, "resume_success_ratio": 1.0, "db_pool_wait_p99_ms": 10, "outbox_age_p99_ms": 100, "beam_run_queue_p99": 2, "native_p99_us": 400, "memory_growth_mib": 0, "disk_free_gib": 30, "security_events": 0},
        "correctness_events": zero_correctness(),
        "evidence_bundle": [{"kind": name, "status": "passed", "git_sha": SHA, "release_id": RELEASE_ID, "sha256": "f" * 64, "ref": f"bundle:{name}"} for name in bundle],
        "approval": {"approved": True, "approved_by": "Senior 5", "approval_ref": "approval:cutover"}, "failures": [],
    }


def compatibility() -> dict:
    checks = [("migrations-expand-compatible", "Senior 2"), ("realtime-protocol-negotiated", "Senior 1"), ("native-fallback-available", "Senior 3"), ("config-versioned", "Senior 1")]
    return {"schema_version": "compatibility-result-v1", "git_sha": SHA, "release_id": RELEASE_ID, "observed_at": NOW, "status": "passed", "checks": [{"name": name, "owner": owner, "status": "passed", "evidence_ref": f"compat:{name}"} for name, owner in checks], "failures": []}


def checkpoint(name: str) -> dict:
    return {"schema_version": "checkpoint-result-v1", "name": name, "git_sha": SHA, "release_id": RELEASE_ID, "observed_at": NOW, "status": "passed", "provider_results": [{"owner": "Senior 5", "seam": f"test:{name}", "status": "passed", "evidence_ref": f"checkpoint:{name}"}], "evidence_refs": [f"checkpoint:{name}"], "blockers": []}


def mutation_log() -> dict:
    return {"schema_version": "oci-mutation-log-v1", "git_sha": SHA, "release_id": RELEASE_ID, "environment": "production", "observed_at": NOW, "status": "passed", "mutations": [{"kind": "traffic", "approved": True, "approval_ref": "approval:traffic", "executed_by": "Senior 4", "started_at": START, "completed_at": NOW, "evidence_ref": "mutation:traffic"}], "failures": []}


def primary_documents() -> dict[str, dict]:
    return {"capacity": capacity(), "platform-probe": platform_probe(), "load": load_result(), "reconnect": reconnect(), "recovery": recovery(), "resilience": resilience_suite(), "security": security(), "cutover": cutover(), "release-recovery": rollback()}
