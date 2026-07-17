#!/usr/bin/env python3
"""Plan or execute a health-gated single-node release activation.

Dry-run is the default. Execution is host-local and requires root, an explicit
approval reference, and the exact deployment-spec digest. It never runs an OCI
apply or a database migration.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
try:
    import pwd
    import grp
except ImportError:  # Windows supports dry-run validation only
    pwd = None
    grp = None
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[2]
SPEC_SCHEMA = ROOT / "contracts" / "operations" / "deployment-spec-v0.schema.json"
RESULT_SCHEMA = ROOT / "contracts" / "operations" / "activation-result-v0.schema.json"
HEALTH_SCHEMA = ROOT / "contracts" / "operations" / "health-gate-target-v0.schema.json"
HEALTH_GATE = ROOT / "scripts" / "release" / "health_gate.py"
REQUIRED_HOST_CHECKS = {
    "runtime_identities", "runtime_directories", "data_volume_mounted", "swap_disabled",
    "selinux_enforcing", "firewall_policy", "ssh_root_disabled",
    "ssh_password_auth_disabled", "legacy_imds_disabled", "capacity_controls",
    "runtime_service_units", "edge_fail_closed",
}


def utc_now() -> str:
    return datetime.now(tz=timezone.utc).isoformat().replace("+00:00", "Z")


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def schema_failures(schema_path: Path, document: Any, label: str) -> list[str]:
    schema = load(schema_path)
    return [
        f"{label}.{'.'.join(map(str, error.absolute_path)) or '$'}: {error.message}"
        for error in sorted(
            Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(document),
            key=lambda item: list(item.absolute_path),
        )
    ]


def validate_host_readiness(document: Any, spec: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    if not isinstance(document, dict):
        return ["host readiness must be a JSON object"]
    expected = {
        "schema_version": "liqi.platform.host-readiness/v0",
        "host_contract_schema_version": spec["target"]["host_schema_version"],
        "infrastructure_output_version": spec["target"]["host_output_version"],
        "bootstrap_version": spec["target"]["host_bootstrap_version"],
        "status": "ready",
        "architecture": "aarch64",
    }
    for key, value in expected.items():
        if document.get(key) != value:
            failures.append(f"host readiness {key} expected {value!r}, got {document.get(key)!r}")
    checks = document.get("checks")
    if not isinstance(checks, dict):
        failures.append("host readiness checks must be an object")
    else:
        missing = REQUIRED_HOST_CHECKS - set(checks)
        if missing:
            failures.append(f"host readiness missing checks: {sorted(missing)}")
        failed = sorted(name for name in REQUIRED_HOST_CHECKS if checks.get(name) != "pass")
        if failed:
            failures.append(f"host readiness checks not passed: {failed}")
    data_volume = document.get("data_volume", {})
    if data_volume.get("mount_path") != "/var/lib/liqi" or not data_volume.get("source_uuid"):
        failures.append("host readiness does not prove the /var/lib/liqi data volume mount")
    return failures


def normalize_version(value: Any) -> int | None:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def validate_database_readiness(document: Any, spec: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    if not isinstance(document, dict):
        return ["database readiness must be a JSON object"]
    if document.get("contractVersion") != "database-v0":
        failures.append("database readiness contractVersion must be database-v0")
    if document.get("database") != "liqi":
        failures.append("database readiness must target database liqi")
    if document.get("ready") is not True or document.get("reason") != "ready":
        failures.append(f"database is not ready: {document.get('reason')!r}")
    current = normalize_version(document.get("currentVersion"))
    required = normalize_version(document.get("requiredVersion"))
    spec_required = normalize_version(spec["preflight"]["required_database_migration"])
    if None in {current, required, spec_required}:
        failures.append("database migration versions must be decimal integers")
    else:
        if required != spec_required:
            failures.append(f"database requiredVersion {required} does not match deployment {spec_required}")
        if current < required:
            failures.append(f"database currentVersion {current} is below requiredVersion {required}")
    if not document.get("observedAt"):
        failures.append("database readiness observedAt is required")
    return failures


def verify_staged_artifacts(spec: dict[str, Any], staged_root: Path) -> list[str]:
    failures: list[str] = []
    for artifact in spec["artifacts"]:
        path = staged_root / artifact["name"]
        if not path.is_file():
            failures.append(f"staged artifact missing: {path}")
            continue
        if path.stat().st_size != artifact["size_bytes"]:
            failures.append(f"staged artifact size mismatch: {artifact['name']}")
        actual = sha256(path)
        if actual != artifact["sha256"]:
            failures.append(f"staged artifact checksum mismatch: {artifact['name']}")
    return failures


def action(sequence: int, name: str, target: str, status: str = "planned", duration_ms: int = 0, error: str | None = None) -> dict[str, Any]:
    return {"sequence": sequence, "action": name, "target": target, "status": status, "duration_ms": duration_ms, "error": error}


def planned_actions(spec: dict[str, Any]) -> list[dict[str, Any]]:
    items = [action(1, "install-release", spec["target"]["release_path"])]
    seq = 2
    for service in sorted(spec["services"], key=lambda item: item["start_order"], reverse=True):
        items.append(action(seq, "stop-service", service["unit"])); seq += 1
    items.append(action(seq, "select-release", spec["target"]["current_symlink"])); seq += 1
    for service in sorted(spec["services"], key=lambda item: item["start_order"]):
        items.append(action(seq, "start-service", service["unit"])); seq += 1
    items.append(action(seq, "run-health-gate", spec["health_gate"]["target_ref"]))
    return items


def run_bounded(command: list[str], timeout: int) -> tuple[int, str, str, int]:
    started = time.monotonic()
    try:
        completed = subprocess.run(command, text=True, capture_output=True, timeout=timeout, check=False)
        return completed.returncode, completed.stdout[-8192:], completed.stderr[-8192:], int((time.monotonic() - started) * 1000)
    except subprocess.TimeoutExpired as exc:
        return 124, (exc.stdout or "")[-8192:] if isinstance(exc.stdout, str) else "", "command timed out", int((time.monotonic() - started) * 1000)


def atomic_symlink(target: Path, link: Path) -> None:
    link.parent.mkdir(parents=True, exist_ok=True)
    temporary = link.parent / f".{link.name}.tmp.{os.getpid()}"
    try:
        temporary.unlink(missing_ok=True)
        temporary.symlink_to(target)
        os.replace(temporary, link)
    finally:
        temporary.unlink(missing_ok=True)


def install_release(spec: dict[str, Any], staged_root: Path) -> None:
    if pwd is None or grp is None:
        raise RuntimeError("release installation requires POSIX account lookup")
    release_path = Path(spec["target"]["release_path"])
    if release_path.exists():
        raise RuntimeError(f"release path already exists: {release_path}")
    deployment_root = release_path.parent
    deployment_root.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{spec['release_id']}.", dir=deployment_root))
    try:
        bin_dir = temporary / "bin"
        bin_dir.mkdir(mode=0o750)
        for artifact in spec["artifacts"]:
            source = staged_root / artifact["name"]
            destination = bin_dir / artifact["name"]
            shutil.copyfile(source, destination)
            os.chmod(destination, int(artifact["mode"], 8))
            uid = pwd.getpwnam(artifact["owner"]).pw_uid
            gid = grp.getgrnam(artifact["group"]).gr_gid
            os.chown(destination, uid, gid)
            if sha256(destination) != artifact["sha256"]:
                raise RuntimeError(f"installed artifact checksum mismatch: {artifact['name']}")
        os.chown(temporary, 0, grp.getgrnam("liqi").gr_gid)
        os.chmod(temporary, 0o750)
        os.replace(temporary, release_path)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary, ignore_errors=True)


def systemd_units_ready(spec: dict[str, Any], systemctl: str) -> list[str]:
    failures: list[str] = []
    for service in spec["services"]:
        code, stdout, stderr, _ = run_bounded([systemctl, "show", "--property=LoadState", "--value", service["unit"]], 10)
        if code != 0 or stdout.strip() != "loaded":
            failures.append(f"systemd unit unavailable {service['unit']}: {stderr.strip() or stdout.strip()}")
    return failures


def run_health(target: Path, output: Path, deadline: int) -> tuple[bool, int, str | None]:
    code, _, stderr, duration = run_bounded(
        [sys.executable, str(HEALTH_GATE), "--target", str(target), "--output", str(output)],
        deadline + 15,
    )
    return code == 0, duration, stderr.strip() or None


def rollback_health_target(source: Path, previous_release_id: str, destination: Path) -> None:
    target = load(source)
    target["release_id"] = previous_release_id
    for check in target["checks"]:
        for identity_field in ("release_id", "releaseId", "version"):
            if identity_field in check["expected_json"]:
                check["expected_json"][identity_field] = previous_release_id
    errors = schema_failures(HEALTH_SCHEMA, target, "rollback_health_target")
    if errors:
        raise RuntimeError("; ".join(errors))
    destination.write_text(json.dumps(target, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def write_result(path: Path, result: dict[str, Any]) -> None:
    result["completed_at"] = utc_now()
    errors = schema_failures(RESULT_SCHEMA, result, "activation_result")
    if errors:
        raise RuntimeError("invalid activation result: " + "; ".join(errors))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--expected-spec-sha256", required=True)
    parser.add_argument("--host-readiness", type=Path, required=True)
    parser.add_argument("--database-readiness", type=Path, required=True)
    parser.add_argument("--staged-root", type=Path, required=True)
    parser.add_argument("--health-target", type=Path, required=True)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--approval-ref")
    parser.add_argument("--systemctl", default="systemctl")
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()

    started_at = utc_now()
    mode = "execute" if args.execute else "dry-run"
    spec_digest = sha256(args.spec)
    spec = load(args.spec)
    result: dict[str, Any] = {
        "schema_version": "activation-result-v0",
        "release_id": spec.get("release_id", "liqi-invalid"),
        "environment": spec.get("environment", "development"),
        "deployment_spec_sha256": spec_digest,
        "mode": mode,
        "started_at": started_at,
        "completed_at": started_at,
        "status": "failed",
        "approval_ref": args.approval_ref,
        "preflight": {
            "deployment_spec": "failed", "host_readiness": "failed", "database_readiness": "failed",
            "artifacts": "failed", "systemd_units": "not-checked",
        },
        "actions": [],
        "health": {"status": "not-run", "result_ref": None},
        "rollback": {"attempted": False, "target_release_id": spec.get("rollback", {}).get("previous_release_id"), "status": "not-required", "health_result_ref": None},
        "mutation": {"performed": False, "release_installed": False, "systemd_changed": False, "symlink_changed": False},
        "incident_reason": None,
    }
    failures = schema_failures(SPEC_SCHEMA, spec, "deployment_spec")
    if spec_digest != args.expected_spec_sha256:
        failures.append("deployment specification digest does not match expected digest")
    if not failures:
        result["preflight"]["deployment_spec"] = "passed"
    try:
        host = load(args.host_readiness)
        host_failures = validate_host_readiness(host, spec) if not failures else ["deployment spec invalid"]
    except (OSError, json.JSONDecodeError) as exc:
        host_failures = [f"cannot read host readiness: {exc}"]
    if not host_failures:
        result["preflight"]["host_readiness"] = "passed"
    failures.extend(host_failures)
    try:
        database = load(args.database_readiness)
        database_failures = validate_database_readiness(database, spec) if result["preflight"]["deployment_spec"] == "passed" else ["deployment spec invalid"]
    except (OSError, json.JSONDecodeError) as exc:
        database_failures = [f"cannot read database readiness: {exc}"]
    if not database_failures:
        result["preflight"]["database_readiness"] = "passed"
    failures.extend(database_failures)
    artifact_failures = verify_staged_artifacts(spec, args.staged_root) if result["preflight"]["deployment_spec"] == "passed" else ["deployment spec invalid"]
    if not artifact_failures:
        result["preflight"]["artifacts"] = "passed"
    failures.extend(artifact_failures)
    if sha256(args.health_target) != spec.get("health_gate", {}).get("target_digest"):
        failures.append("health target digest does not match deployment specification")

    result["actions"] = planned_actions(spec) if result["preflight"]["deployment_spec"] == "passed" else []
    if failures:
        result["incident_reason"] = "; ".join(failures)[:1000]
        write_result(args.output, result)
        for failure in failures:
            print(f"ERROR activation-preflight: {failure}", file=sys.stderr)
        return 1

    if not args.execute:
        result["status"] = "planned"
        write_result(args.output, result)
        print(f"activation dry-run passed: {args.output}")
        return 0

    expected_approval = spec["preflight"].get("approval_ref")
    if not args.approval_ref or (expected_approval and args.approval_ref != expected_approval):
        result["incident_reason"] = "execution requires the approved reference recorded in the deployment specification"
        write_result(args.output, result)
        print(f"ERROR activation-execute: {result['incident_reason']}", file=sys.stderr)
        return 2
    if os.name != "posix" or not hasattr(os, "geteuid") or os.geteuid() != 0:
        result["incident_reason"] = "execution requires root on the target POSIX host"
        write_result(args.output, result)
        print(f"ERROR activation-execute: {result['incident_reason']}", file=sys.stderr)
        return 2

    unit_failures = systemd_units_ready(spec, args.systemctl)
    if unit_failures:
        result["preflight"]["systemd_units"] = "failed"
        result["incident_reason"] = "; ".join(unit_failures)[:1000]
        write_result(args.output, result)
        return 1
    result["preflight"]["systemd_units"] = "passed"
    health_target = args.health_target.resolve()
    args.state_dir.mkdir(parents=True, exist_ok=True)
    health_result = args.state_dir / f"{spec['release_id']}.health.json"
    current_link = Path(spec["target"]["current_symlink"])
    previous_id = spec["rollback"]["previous_release_id"]
    previous_path = Path(spec["rollback"]["previous_release_path"]) if previous_id else None

    try:
        install_release(spec, args.staged_root)
        result["mutation"]["performed"] = True
        result["mutation"]["release_installed"] = True
        for service in sorted(spec["services"], key=lambda item: item["start_order"], reverse=True):
            code, _, stderr, _ = run_bounded([args.systemctl, "stop", service["unit"]], service["stop_timeout_seconds"] + 5)
            if code != 0:
                raise RuntimeError(f"failed to stop {service['unit']}: {stderr.strip()}")
        result["mutation"]["systemd_changed"] = True
        atomic_symlink(Path(spec["target"]["release_path"]), current_link)
        result["mutation"]["symlink_changed"] = True
        for service in sorted(spec["services"], key=lambda item: item["start_order"]):
            code, _, stderr, _ = run_bounded([args.systemctl, "start", service["unit"]], 30)
            if code != 0:
                raise RuntimeError(f"failed to start {service['unit']}: {stderr.strip()}")
        healthy, _, health_error = run_health(health_target, health_result, spec["health_gate"]["deadline_seconds"])
        result["health"] = {"status": "passed" if healthy else "failed", "result_ref": str(health_result)}
        if not healthy:
            raise RuntimeError(f"activation health gate failed: {health_error or 'health checks failed'}")
        result["status"] = "active"
        result["rollback"]["status"] = "not-required"
        for item in result["actions"]:
            item["status"] = "passed"
        write_result(args.output, result)
        print(f"activation passed: {args.output}")
        return 0
    except Exception as exc:  # operational failure must attempt bounded rollback
        for service in sorted(spec["services"], key=lambda item: item["start_order"], reverse=True):
            run_bounded([args.systemctl, "stop", service["unit"]], service["stop_timeout_seconds"] + 5)
        if previous_id and previous_path and previous_path.is_dir():
            result["rollback"]["attempted"] = True
            result["rollback"]["status"] = "failed"
            try:
                atomic_symlink(previous_path, current_link)
                rollback_target = args.state_dir / f"{previous_id}.rollback-health-target.json"
                rollback_health_target(health_target, previous_id, rollback_target)
                for service in sorted(spec["services"], key=lambda item: item["start_order"]):
                    code, _, stderr, _ = run_bounded([args.systemctl, "start", service["unit"]], 30)
                    if code != 0:
                        raise RuntimeError(f"failed to restart rollback unit {service['unit']}: {stderr.strip()}")
                rollback_health = args.state_dir / f"{previous_id}.rollback-health.json"
                healthy, _, rollback_error = run_health(rollback_target, rollback_health, spec["rollback"]["deadline_seconds"])
                result["rollback"]["health_result_ref"] = str(rollback_health)
                if not healthy:
                    raise RuntimeError(rollback_error or "rollback health gate failed")
                result["rollback"]["status"] = "passed"
                result["status"] = "rolled-back"
                result["incident_reason"] = str(exc)[:1000]
                write_result(args.output, result)
                print(f"activation failed and rollback passed: {args.output}", file=sys.stderr)
                return 3
            except Exception as rollback_exc:
                result["rollback"]["status"] = "failed"
                result["status"] = "incident"
                result["incident_reason"] = f"activation={exc}; rollback={rollback_exc}"[:1000]
        else:
            result["rollback"]["status"] = "not-available"
            result["status"] = "incident"
            result["incident_reason"] = f"activation={exc}; no retained rollback target"[:1000]
        write_result(args.output, result)
        print(f"ERROR activation incident: {result['incident_reason']}", file=sys.stderr)
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
