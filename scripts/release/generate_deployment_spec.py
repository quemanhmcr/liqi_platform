#!/usr/bin/env python3
"""Generate a deterministic, non-executing deployment specification after preflight."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[2]
MANIFEST_SCHEMA = ROOT / "contracts" / "operations" / "release-manifest-v0.schema.json"
INTEGRATION_SCHEMA = ROOT / "contracts" / "operations" / "integration-result-v0.schema.json"
HEALTH_SCHEMA = ROOT / "contracts" / "operations" / "health-gate-target-v0.schema.json"
DEPLOYMENT_SCHEMA = ROOT / "contracts" / "operations" / "deployment-spec-v0.schema.json"


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def schema_errors(schema_path: Path, document: Any, label: str) -> list[str]:
    return [
        f"{label}.{'.'.join(map(str, error.absolute_path)) or '$'}: {error.message}"
        for error in sorted(
            Draft202012Validator(load(schema_path), format_checker=FormatChecker()).iter_errors(document),
            key=lambda item: list(item.absolute_path),
        )
    ]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--integration-result", type=Path, required=True)
    parser.add_argument("--host-output", type=Path, required=True)
    parser.add_argument("--health-target", type=Path, required=True)
    parser.add_argument("--approval-ref")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    manifest = load(args.manifest)
    integration = load(args.integration_result)
    host = load(args.host_output)
    health = load(args.health_target)
    failures: list[str] = []
    failures.extend(schema_errors(MANIFEST_SCHEMA, manifest, "manifest"))
    failures.extend(schema_errors(INTEGRATION_SCHEMA, integration, "integration"))
    failures.extend(schema_errors(HEALTH_SCHEMA, health, "health"))

    release_id = manifest.get("release_id")
    environment = manifest.get("deployment", {}).get("environment")
    if integration.get("overall_status") != "passed" or integration.get("mode") != "provider":
        failures.append("deployment requires a passed provider-mode integration result")
    if integration.get("release_id") != release_id or health.get("release_id") != release_id:
        failures.append("release ID mismatch across manifest, integration result and health target")
    if integration.get("git_sha") != manifest.get("git_sha"):
        failures.append("Git SHA mismatch between manifest and integration result")
    if integration.get("environment") != environment or health.get("environment") != environment or host.get("environment") != environment:
        failures.append("environment mismatch across deployment inputs")
    if integration.get("capacity", {}).get("status") != "passed":
        failures.append("capacity preflight is not passed")
    if integration.get("platform_probe", {}).get("status") != "passed":
        failures.append("platform probe preflight is not passed")
    if environment in {"staging", "production"} and integration.get("recovery", {}).get("status") != "passed":
        failures.append("staging/production deployment requires passed recovery evidence")
    if manifest.get("database_migration", {}).get("destructive_allowed") is not False:
        failures.append("destructive database migration is forbidden")

    manifest_cost = manifest.get("infrastructure", {}).get("cost_classification")
    host_cost = host.get("capacity_profile", {}).get("cost_classification")
    if manifest_cost != host_cost:
        failures.append(f"cost classification mismatch: manifest={manifest_cost} host={host_cost}")
    if manifest_cost in {"free-trial-only", "paid", "unknown"} and not args.approval_ref:
        failures.append(f"{manifest_cost} infrastructure requires explicit approval reference")

    release_target = host.get("release_target", {})
    required_target = {
        "transport_user": "opc",
        "deployment_path": "/opt/liqi/releases",
        "current_symlink": "/opt/liqi/current",
        "staging_path": "/var/tmp/liqi/releases",
        "installation_semantics": "upload-to-staging-then-root-owned-atomic-install",
    }
    for key, expected in required_target.items():
        if release_target.get(key) != expected:
            failures.append(f"host release target {key} must be {expected!r}")

    if failures:
        for failure in failures:
            print(f"ERROR deployment-preflight: {failure}", file=sys.stderr)
        return 1

    release_path = f"{release_target['deployment_path']}/{release_id}"
    identity = {item["service"]: item["user"] for item in host.get("identities", {}).get("services", [])}
    artifact_by_name = {artifact["name"]: artifact for artifact in manifest["artifacts"]}
    service_order = ["liqi-api", "liqi-realtime", "liqi-worker"]
    artifacts = [
        {
            "name": name,
            "sha256": artifact_by_name[name]["sha256"],
            "size_bytes": artifact_by_name[name]["size_bytes"],
            "target_path": f"{release_path}/bin/{name}",
            "owner": identity[name],
            "group": "liqi",
            "mode": "0550",
        }
        for name in sorted(service_order)
    ]
    services = [
        {
            "name": name,
            "unit": f"{name}.service",
            "artifact": name,
            "start_order": index + 1,
            "stop_timeout_seconds": 30 if name != "liqi-worker" else 60,
            "shutdown_signal": "SIGTERM",
        }
        for index, name in enumerate(service_order)
    ]
    previous = manifest["rollback"]["previous_release_id"]
    spec = {
        "schema_version": "deployment-spec-v0",
        "release_id": release_id,
        "git_sha": manifest["git_sha"],
        "environment": environment,
        "strategy": "health-gated-replacement",
        "claims": {"high_availability": False, "zero_downtime": False, "canary": False},
        "target": {
            "host_schema_version": host["schema_version"],
            "host_output_version": host["infrastructure_output_version"],
            "host_bootstrap_version": host["bootstrap_version"],
            "host_instance_ref": host["host"]["instance_id"],
            "transport_user": release_target["transport_user"],
            "staging_path": release_target["staging_path"],
            "deployment_root": release_target["deployment_path"],
            "release_path": release_path,
            "current_symlink": release_target["current_symlink"],
            "installation_semantics": release_target["installation_semantics"],
        },
        "artifacts": artifacts,
        "services": services,
        "preflight": {
            "integration_id": integration["integration_id"],
            "integration_status": integration["overall_status"],
            "capacity_status": integration["capacity"]["status"],
            "recovery_status": integration["recovery"]["status"],
            "platform_probe_status": integration["platform_probe"]["status"],
            "required_database_migration": manifest["database_migration"]["maximum"],
            "database_compatibility": manifest["database_migration"]["compatibility"],
            "infrastructure_cost_classification": manifest_cost,
            "approval_ref": args.approval_ref,
        },
        "health_gate": {
            "target_ref": args.health_target.as_posix(),
            "target_digest": digest(args.health_target),
            "deadline_seconds": health["deadline_seconds"],
            "failure_action": "stop-new-release-select-predeclared-rollback-or-incident",
        },
        "rollback": {
            "compatible": manifest["rollback"]["compatible"],
            "previous_release_id": previous,
            "previous_release_path": f"{release_target['deployment_path']}/{previous}" if previous else None,
            "deadline_seconds": manifest["rollback"]["deadline_seconds"],
            "database_rollback_allowed": False,
            "first_release_failure_action": "stop-services-and-enter-incident",
        },
        "mutation_policy": {
            "owner_approval_required": True,
            "oci_apply": False,
            "destructive_migration": False,
            "automatic_activation": False,
        },
    }
    output_errors = schema_errors(DEPLOYMENT_SCHEMA, spec, "deployment_spec")
    if output_errors:
        for failure in output_errors:
            print(f"ERROR deployment-spec: {failure}", file=sys.stderr)
        return 65
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(spec, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    print(f"generated deployment specification: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
