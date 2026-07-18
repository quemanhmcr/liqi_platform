#!/usr/bin/env python3
"""Retain an installed V0 release with exact migration-8 compatibility evidence."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[2]
SEARCH_ROOTS = (ROOT / "contracts", Path("/usr/local/share/liqi/contracts"))


def contract(relative: str) -> Path:
    for root in SEARCH_ROOTS:
        path = root / relative
        if path.is_file():
            return path
    raise FileNotFoundError(relative)


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def errors(schema: Path, document: Any) -> list[str]:
    validator = Draft202012Validator(load(schema), format_checker=FormatChecker())
    return [
        f"{'.'.join(map(str, item.absolute_path)) or '$'}: {item.message}"
        for item in sorted(validator.iter_errors(document), key=lambda value: list(value.absolute_path))
    ]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--release-manifest", required=True, type=Path)
    parser.add_argument("--deployment-spec", required=True, type=Path)
    parser.add_argument("--health-target", required=True, type=Path)
    parser.add_argument("--upgrade-compatibility-result", required=True, type=Path)
    parser.add_argument("--database-provider-git-sha", required=True)
    parser.add_argument("--release-path", required=True, type=Path)
    parser.add_argument("--recovery-root", type=Path, default=Path("/var/lib/liqi/recovery"))
    parser.add_argument("--approval-reference")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    manifest = load(args.release_manifest)
    spec = load(args.deployment_spec)
    health = load(args.health_target)
    upgrade = load(args.upgrade_compatibility_result)
    failures: list[str] = []
    failures += errors(contract("operations/release-manifest-v0.schema.json"), manifest)
    failures += errors(contract("operations/deployment-spec-v0.schema.json"), spec)
    failures += errors(contract("operations/health-gate-target-v0.schema.json"), health)
    release_id = manifest.get("release_id")
    if spec.get("release_id") != release_id or health.get("release_id") != release_id:
        failures.append("V0 release ID mismatch")
    if spec.get("git_sha") != manifest.get("git_sha"):
        failures.append("V0 Git SHA mismatch")
    if Path(spec.get("target", {}).get("release_path", "/invalid")) != args.release_path:
        failures.append("V0 installed release path does not match deployment spec")
    if not args.release_path.is_dir():
        failures.append("V0 installed release directory is missing")
    for artifact in spec.get("artifacts", []):
        path = args.release_path / "bin" / artifact["name"]
        if not path.is_file():
            failures.append(f"V0 artifact missing: {path}")
    if not (
        upgrade == {
            "test": "v0-upgrade-compatibility-v1",
            "fromMigration": 4,
            "toMigration": 8,
            "v0FunctionsRetained": True,
            "passed": True,
        }
    ):
        failures.append("Senior 2 V0 upgrade compatibility result is not exact/passed")
    if not isinstance(args.database_provider_git_sha, str) or len(args.database_provider_git_sha) != 40 or any(ch not in "0123456789abcdef" for ch in args.database_provider_git_sha):
        failures.append("database provider Git SHA is invalid")
    if failures:
        for failure in failures:
            print(f"ERROR v0-retain: {failure}", file=os.sys.stderr)
        return 1
    if args.execute and (os.name != "posix" or os.geteuid() != 0):
        raise SystemExit("V0 retention mutation requires root on POSIX")
    if args.execute and (not args.approval_reference or len(args.approval_reference.strip()) < 3):
        raise SystemExit("V0 retention mutation requires approval reference")

    observed = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    compatibility = {
        "schema_version": "liqi.deployment.v0-rollback-compatibility/v1",
        "status": "passed",
        "database_provider_git_sha": args.database_provider_git_sha,
        "v0_release_id": release_id,
        "from_migration": 4,
        "to_migration": 8,
        "v0_functions_retained": True,
        "source_result_sha256": digest(args.upgrade_compatibility_result),
        "observed_at": observed,
    }
    compatibility_failures = errors(contract("deployment/v0-rollback-compatibility-v1.schema.json"), compatibility)
    if compatibility_failures:
        raise SystemExit("invalid V0 compatibility evidence: " + "; ".join(compatibility_failures))
    compatibility_bytes = (json.dumps(compatibility, indent=2, sort_keys=True) + "\n").encode()

    input_root = args.recovery_root / "release-inputs"
    descriptor_path = args.recovery_root / "release-targets" / f"{release_id}.json"
    retained_manifest = input_root / f"{release_id}.release-manifest-v0.json"
    retained_spec = input_root / f"{release_id}.deployment-spec-v0.json"
    retained_health = input_root / f"{release_id}.health-target-v0.json"
    retained_compatibility = input_root / f"{release_id}.v0-rollback-compatibility-v1.json"
    health_output = args.recovery_root / "health" / f"{release_id}.json"
    descriptor = {
        "schema_version": "liqi.deployment.release-target/v1",
        "release_id": release_id,
        "runtime_generation": "rust-v0",
        "git_sha": manifest["git_sha"],
        "release_path": str(args.release_path),
        "source_manifest": {"schema_version": manifest["schema_version"], "sha256": digest(args.release_manifest), "retained_path": str(retained_manifest)},
        "services": [{"unit": item["unit"], "start_order": item["start_order"], "stop_timeout_seconds": item["stop_timeout_seconds"]} for item in spec["services"]],
        "drain": {"argv": None, "timeout_seconds": 1},
        "health": {"argv": ["/usr/bin/python3", "/usr/local/lib/liqi-v0/scripts/release/health_gate.py", "--target", str(retained_health), "--output", str(health_output)], "timeout_seconds": health["deadline_seconds"] + 15},
        "database_compatibility": {"minimum_migration": 4, "maximum_migration": 8, "rollback_safe_through": 8, "database_rollback_allowed": False},
        "database_compatibility_evidence": {"schema_version": compatibility["schema_version"], "sha256": hashlib.sha256(compatibility_bytes).hexdigest(), "retained_path": str(retained_compatibility)},
        "rollback_target_release_id": manifest["rollback"]["previous_release_id"],
        "runtime_config_path": None,
        "credential_directory": None,
        "required_credentials": [],
        "configuration_paths": ["/etc/liqi/api.json", "/etc/liqi/realtime.json", "/etc/liqi/worker.json", "/run/liqi/secrets/liqi-api", "/run/liqi/secrets/liqi-realtime", "/run/liqi/secrets/liqi-worker"],
        "created_at": manifest["source_timestamp"],
    }
    descriptor_failures = errors(contract("deployment/release-target-v1.schema.json"), descriptor)
    if descriptor_failures:
        raise SystemExit("invalid V0 release descriptor: " + "; ".join(descriptor_failures))
    descriptor_bytes = (json.dumps(descriptor, indent=2, sort_keys=True) + "\n").encode()

    status = "validated"
    if args.execute:
        verify_units = ["/etc/systemd/system/" + item["unit"] for item in spec["services"]]
        verify = subprocess.run(["systemd-analyze", "verify", *verify_units], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, timeout=60)
        if verify.returncode:
            raise RuntimeError(verify.stderr.strip() or verify.stdout.strip() or "V0 systemd verification failed")
        for directory in (input_root, descriptor_path.parent, health_output.parent):
            directory.mkdir(parents=True, exist_ok=True)
        for source, target in ((args.release_manifest, retained_manifest), (args.deployment_spec, retained_spec), (args.health_target, retained_health)):
            shutil.copyfile(source, target); os.chmod(target, 0o440)
        retained_compatibility.write_bytes(compatibility_bytes); os.chmod(retained_compatibility, 0o440)
        descriptor_path.write_bytes(descriptor_bytes); os.chmod(descriptor_path, 0o440)
        status = "retained"

    result = {
        "schema_version": "liqi.deployment.v0-rollback-retention-result/v1",
        "release_id": release_id,
        "git_sha": manifest["git_sha"],
        "status": status,
        "descriptor_sha256": hashlib.sha256(descriptor_bytes).hexdigest(),
        "compatibility_evidence_sha256": hashlib.sha256(compatibility_bytes).hexdigest(),
        "approval_reference": args.approval_reference if args.execute else None,
        "observed_at": observed,
        "mutation_performed": args.execute,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
