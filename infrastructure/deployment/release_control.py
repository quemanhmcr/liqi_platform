#!/usr/bin/env python3
"""Dry-run, activate, roll back, or deactivate a LIQI release with exact recovery gates."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[2]
CONTRACT_ROOTS = {
    "deployment": (ROOT / "contracts/deployment", Path("/usr/local/share/liqi/contracts/deployment")),
    "infrastructure": (ROOT / "contracts/infrastructure", Path("/usr/local/share/liqi/contracts/infrastructure")),
}


def utc() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def contract(name: str, namespace: str = "deployment") -> Path:
    for root in CONTRACT_ROOTS[namespace]:
        path = root / name
        if path.is_file():
            return path
    raise FileNotFoundError(f"{namespace}/{name}")


def validate(name: str, document: Any, namespace: str = "deployment") -> list[str]:
    schema = load(contract(name, namespace))
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    return [
        f"{'.'.join(map(str, error.absolute_path)) or '$'}: {error.message}"
        for error in sorted(validator.iter_errors(document), key=lambda value: list(value.absolute_path))
    ]


def check(name: str, status: str, detail: str, duration_ms: int = 0) -> dict[str, Any]:
    return {"name": name, "status": status, "duration_ms": duration_ms, "detail": detail[:2048]}


def recovery_check(name: str, status: str, detail: str) -> dict[str, str]:
    return {"name": name, "status": status, "detail": detail[:2048]}


def run(argv: list[str], timeout: int) -> tuple[bool, str, int]:
    started = time.monotonic()
    try:
        result = subprocess.run(
            argv, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            check=False, timeout=timeout,
        )
        detail = (result.stderr or result.stdout or "")[-2048:].strip()
        return result.returncode == 0, detail, int((time.monotonic() - started) * 1000)
    except subprocess.TimeoutExpired:
        return False, "command timed out", int((time.monotonic() - started) * 1000)


def atomic_symlink(target: Path, link: Path) -> None:
    link.parent.mkdir(parents=True, exist_ok=True)
    temporary = link.parent / f".{link.name}.new.{os.getpid()}"
    temporary.unlink(missing_ok=True)
    temporary.symlink_to(target)
    os.replace(temporary, link)


def descriptor(path: Path) -> dict[str, Any]:
    document = load(path)
    failures = validate("release-target-v1.schema.json", document)
    if failures:
        raise RuntimeError(f"invalid descriptor {path}: {'; '.join(failures)}")
    return document


def current_release_id(link: Path) -> str | None:
    if not link.exists() and not link.is_symlink():
        return None
    if not link.is_symlink():
        raise RuntimeError(f"current link is not a symlink: {link}")
    resolved = link.resolve(strict=True)
    if resolved.parent != Path("/opt/liqi/releases"):
        raise RuntimeError("current release escapes /opt/liqi/releases")
    return resolved.name


def database_version(path: Path) -> int:
    document = load(path)
    if document.get("schemaVersion") == "migration-readiness-v1":
        exact = (
            document.get("status") == "passed"
            and document.get("ready") is True
            and document.get("writeReady") is True
            and document.get("reason") == "ready"
            and document.get("currentVersion") == document.get("requiredVersion")
            and document.get("obanMigrationVersion") == document.get("requiredObanMigrationVersion") == 14
            and document.get("inRecovery") is False
        )
        if not exact:
            raise RuntimeError("V1 migration readiness is not exact and write-ready")
        return int(document["currentVersion"])
    if (
        document.get("schemaVersion") == "database-readiness-v0"
        and document.get("ready") is True
        and document.get("reason") == "ready"
    ):
        return int(document["currentVersion"])
    raise RuntimeError("database readiness provider is unsupported or not ready")


def compatibility_one(label: str, document: dict[str, Any], version: int) -> None:
    boundary = document["database_compatibility"]
    if not boundary["minimum_migration"] <= version <= boundary["rollback_safe_through"]:
        raise RuntimeError(f"database version is outside {label} release compatibility range")
    if boundary["database_rollback_allowed"]:
        raise RuntimeError("database down migration is forbidden")


def compatibility(target: dict[str, Any], fallback: dict[str, Any], version: int) -> None:
    compatibility_one("target", target, version)
    compatibility_one("fallback", fallback, version)


def evidence_ready(document: dict[str, Any]) -> None:
    evidence = document["database_compatibility_evidence"]
    path = Path(evidence["retained_path"])
    if not path.is_file() or digest(path) != evidence["sha256"]:
        raise RuntimeError(f"database compatibility evidence missing/tampered for {document['release_id']}")


def configs_ready(document: dict[str, Any]) -> None:
    missing = [path for path in document["configuration_paths"] if not Path(path).exists()]
    if missing:
        raise RuntimeError(f"required configuration paths missing: {missing}")
    evidence_ready(document)
    directory = document["credential_directory"]
    required = document["required_credentials"]
    if directory is None:
        if required:
            raise RuntimeError("release declares credentials without a credential directory")
        return
    root = Path(directory)
    for name in required:
        path = root / name
        if not path.is_file() or not os.access(path, os.R_OK):
            raise RuntimeError(f"required credential unavailable: {path}")
        if path.stat().st_size not in range(1, 25 * 1024 + 1):
            raise RuntimeError(f"required credential size is invalid: {path}")
        if path.stat().st_mode & 0o077:
            raise RuntimeError(f"required credential permissions are too broad: {path}")


def recovery_evidence(path: Path | None, git_sha: str) -> dict[str, Any]:
    if path is None:
        raise RuntimeError("first-release recovery evidence is required")
    document = load(path)
    failures = validate("first-release-recovery-v1.schema.json", document, "infrastructure")
    if failures:
        raise RuntimeError("invalid first-release recovery evidence: " + "; ".join(failures))
    if document.get("git_sha") != git_sha or document.get("status") != "passed" or document.get("blockers"):
        raise RuntimeError("first-release recovery evidence is not exact-SHA and passed")
    return document


def services(document: dict[str, Any], reverse: bool = False) -> list[dict[str, Any]]:
    return sorted(document["services"], key=lambda item: item["start_order"], reverse=reverse)


def stop_release(document: dict[str, Any], systemctl: str, *, drain: bool = True) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    if drain and document["drain"]["argv"]:
        ok, detail, duration = run(document["drain"]["argv"], document["drain"]["timeout_seconds"])
        results.append(check("drain-current", "passed" if ok else "failed", detail or "drain completed", duration))
        if not ok:
            raise RuntimeError(f"drain failed: {detail}")
    for item in services(document, True):
        ok, detail, duration = run([systemctl, "stop", item["unit"]], item["stop_timeout_seconds"] + 5)
        results.append(check("stop-service", "passed" if ok else "failed", f"{item['unit']}: {detail or 'stopped'}", duration))
        if not ok:
            raise RuntimeError(f"stop failed for {item['unit']}: {detail}")
    return results


def start_release(document: dict[str, Any], systemctl: str) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for item in services(document):
        ok, detail, duration = run([systemctl, "start", item["unit"]], 60)
        results.append(check("start-service", "passed" if ok else "failed", f"{item['unit']}: {detail or 'started'}", duration))
        if not ok:
            raise RuntimeError(f"start failed for {item['unit']}: {detail}")
    ok, detail, duration = run(document["health"]["argv"], document["health"]["timeout_seconds"])
    results.append(check("health-gate", "passed" if ok else "failed", detail or "health passed", duration))
    if not ok:
        raise RuntimeError(f"health gate failed: {detail}")
    return results


def select_release(document: dict[str, Any], current_link: Path, runtime_link: Path) -> None:
    atomic_symlink(Path(document["release_path"]), current_link)
    runtime = document["runtime_config_path"]
    if runtime is None:
        runtime_link.unlink(missing_ok=True)
    else:
        atomic_symlink(Path(runtime), runtime_link)


def clear_selection(current_link: Path, runtime_link: Path) -> None:
    current_link.unlink(missing_ok=True)
    runtime_link.unlink(missing_ok=True)


def root_posix() -> bool:
    return os.name == "posix" and hasattr(os, "geteuid") and os.geteuid() == 0


def parse() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("activate", "rollback"))
    parser.add_argument("--target-release-id")
    parser.add_argument("--deployment-id", required=True)
    parser.add_argument("--descriptor-dir", type=Path, default=Path("/var/lib/liqi/recovery/release-targets"))
    parser.add_argument("--current-link", type=Path, default=Path("/opt/liqi/current"))
    parser.add_argument("--runtime-config-link", type=Path, default=Path("/etc/liqi/runtime/current.json"))
    parser.add_argument("--host-readiness", required=True, type=Path)
    parser.add_argument("--database-readiness", required=True, type=Path)
    parser.add_argument("--first-release-recovery", type=Path)
    parser.add_argument("--systemctl", default="systemctl")
    parser.add_argument("--maximum-duration-seconds", type=int, default=300)
    parser.add_argument("--approval-reference")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--activation-output", type=Path)
    parser.add_argument("--rollback-output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse()
    started = utc()
    checks: list[dict[str, Any]] = []
    if not 1 <= args.maximum_duration_seconds <= 900:
        raise SystemExit("maximum duration must be 1..900 seconds")

    current_id = current_release_id(args.current_link)
    current = descriptor(args.descriptor_dir / f"{current_id}.json") if current_id else None
    target: dict[str, Any] | None = None
    target_id: str | None = None
    first_transition = False

    if args.mode == "activate":
        if not args.target_release_id:
            raise SystemExit("activate requires --target-release-id")
        target_id = args.target_release_id
        target = descriptor(args.descriptor_dir / f"{target_id}.json")
        first_transition = current is None
        if first_transition and target.get("rollback_target_release_id") is not None:
            raise SystemExit("first activation target must declare no application rollback release")
        if not first_transition and target.get("rollback_target_release_id") != current_id:
            raise SystemExit("activation target does not declare the current release as rollback target")
    else:
        if current is None:
            raise SystemExit("no active release is selected")
        target_id = args.target_release_id or current.get("rollback_target_release_id")
        first_transition = target_id is None
        if not first_transition:
            if current.get("rollback_target_release_id") != target_id:
                raise SystemExit("requested rollback target is not predeclared")
            target = descriptor(args.descriptor_dir / f"{target_id}.json")

    host = load(args.host_readiness)
    host_failures = validate("host-runtime-v1.schema.json", host, "infrastructure")
    if host_failures or host.get("status") != "ready":
        raise SystemExit("host readiness is not ready: " + "; ".join(host_failures))
    version = database_version(args.database_readiness)
    if target is not None:
        compatibility_one("target", target, version)
        configs_ready(target)
        if not Path(target["release_path"]).is_dir():
            raise SystemExit("target release directory missing")
    if current is not None:
        compatibility_one("current", current, version)
        configs_ready(current)
        if not Path(current["release_path"]).is_dir():
            raise SystemExit("current release directory missing")
    if first_transition:
        bound = target if target is not None else current
        assert bound is not None
        recovery_evidence(args.first_release_recovery, bound["git_sha"])

    checks.extend([
        check("host-readiness", "passed", "host readiness passed"),
        check("database-compatibility", "passed", f"migration {version} is compatible and forward-only"),
        check("configuration", "passed", "configuration, evidence and credentials present"),
        check("recovery-target", "passed", "first-release infrastructure recovery is exact-SHA" if first_transition else f"retained target {target_id}"),
    ])
    evidence_class = "live-approved" if args.execute else "dry-run"
    approval = args.approval_reference if args.execute else None
    if args.execute and not root_posix():
        raise SystemExit("release mutation requires root on POSIX")
    if args.execute and (not approval or len(approval.strip()) < 3):
        raise SystemExit("release mutation requires approval reference")

    recovery_mode = "deactivate-first-release" if first_transition else "release-switch"
    fallback = target if args.mode == "rollback" and target else current if args.mode == "activate" and current else None
    rollback_doc = {
        "schema_version": "liqi.deployment.rollback/v1",
        "rollback_id": f"{args.deployment_id}-rollback",
        "from_release_id": current_id or target_id,
        "target_release_id": fallback and fallback["release_id"],
        "recovery_mode": recovery_mode,
        "target_git_sha": fallback and fallback["git_sha"],
        "target_manifest_sha256": fallback and fallback["source_manifest"]["sha256"],
        "database_compatibility": "compatible",
        "config_compatibility": "compatible",
        "maximum_duration_seconds": args.maximum_duration_seconds,
        "status": "engineering-complete-evidence-pending",
        "started_at": started,
        "completed_at": utc(),
        "approval_reference": approval,
        "checks": [recovery_check("preflight", "passed", "release and infrastructure recovery preflight validated")],
        "evidence_class": evidence_class,
    }
    activation_doc: dict[str, Any] | None = None
    if args.mode == "activate":
        if not args.activation_output:
            raise SystemExit("activate requires --activation-output")
        assert target is not None and target_id is not None
        activation_doc = {
            "schema_version": "liqi.deployment.activation/v1",
            "deployment_id": args.deployment_id,
            "release_id": target_id,
            "git_sha": target["git_sha"],
            "manifest_sha256": target["source_manifest"]["sha256"],
            "previous_release_id": current_id,
            "rollback_target_release_id": current_id,
            "state": "preflight-passed",
            "status": "engineering-complete-evidence-pending",
            "started_at": started,
            "completed_at": utc(),
            "approval_reference": approval,
            "checks": checks,
            "traffic_enabled": False,
            "evidence_class": evidence_class,
        }

    if not args.execute:
        args.rollback_output.parent.mkdir(parents=True, exist_ok=True)
        args.rollback_output.write_text(json.dumps(rollback_doc, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
        if activation_doc is not None and args.activation_output is not None:
            args.activation_output.parent.mkdir(parents=True, exist_ok=True)
            args.activation_output.write_text(json.dumps(activation_doc, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
        print("release control dry-run passed; no mutation performed")
        return 0

    operation_started = time.monotonic()
    recovery_checks: list[dict[str, str]] = []
    try:
        if args.mode == "activate":
            assert target is not None and activation_doc is not None
            if current is not None:
                checks.extend(stop_release(current, args.systemctl))
            select_release(target, args.current_link, args.runtime_config_link)
            checks.append(check("select-release", "passed", f"{target['release_path']} and {target['runtime_config_path']}"))
            checks.extend(start_release(target, args.systemctl))
            if int(time.monotonic() - operation_started) > args.maximum_duration_seconds:
                raise RuntimeError("release operation exceeded maximum duration")
            activation_doc.update({"state": "health-gated", "status": "passed", "completed_at": utc(), "checks": checks})
            rollback_doc["checks"].append(recovery_check("not-executed", "not-run", "recovery was not required"))
        elif first_transition:
            assert current is not None
            recovery_checks.extend(stop_release(current, args.systemctl))
            clear_selection(args.current_link, args.runtime_config_link)
            rollback_doc.update({
                "status": "passed", "completed_at": utc(),
                "checks": [recovery_check("drain-stop-deactivate", "passed", "first release deactivated; traffic must remain fail-closed")],
            })
        else:
            assert current is not None and target is not None
            recovery_checks.extend(stop_release(current, args.systemctl))
            select_release(target, args.current_link, args.runtime_config_link)
            recovery_checks.extend(start_release(target, args.systemctl))
            rollback_doc.update({
                "status": "passed", "completed_at": utc(),
                "checks": [recovery_check("drain-stop-switch-start-health", "passed", "rollback completed")],
            })
    except Exception as error:
        recovery_checks.append(recovery_check("operation-failure", "failed", str(error)))
        try:
            if args.mode != "activate":
                raise
            assert target is not None and activation_doc is not None
            stop_release(target, args.systemctl, drain=False)
            if current is None:
                clear_selection(args.current_link, args.runtime_config_link)
                recovery_checks.append(recovery_check("automatic-recovery", "passed", "deactivated failed first release"))
            else:
                select_release(current, args.current_link, args.runtime_config_link)
                start_release(current, args.systemctl)
                recovery_checks.append(recovery_check("automatic-recovery", "passed", f"restored {current_id}"))
            rollback_doc.update({"status": "passed", "completed_at": utc(), "checks": recovery_checks})
            activation_doc.update({"state": "rolled-back", "status": "failed", "completed_at": utc(), "checks": checks + [check("automatic-rollback", "passed", str(error))]})
        except Exception as recovery_error:
            recovery_checks.append(recovery_check("automatic-recovery", "failed", str(recovery_error)))
            rollback_doc.update({"status": "failed", "completed_at": utc(), "checks": recovery_checks})
            if activation_doc is not None:
                activation_doc.update({"state": "activation-failed", "status": "failed", "completed_at": utc(), "checks": checks + [check("automatic-rollback", "failed", str(recovery_error))]})

    for schema_name, document, path in (
        ("rollback-v1.schema.json", rollback_doc, args.rollback_output),
        ("activation-v1.schema.json", activation_doc, args.activation_output),
    ):
        if document is None or path is None:
            continue
        failures = validate(schema_name, document)
        if failures:
            raise RuntimeError(f"invalid evidence {schema_name}: {'; '.join(failures)}")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")

    print(json.dumps({"mode": args.mode, "activation_status": activation_doc and activation_doc["status"], "rollback_status": rollback_doc["status"]}, sort_keys=True))
    if activation_doc and activation_doc["status"] == "failed":
        return 3
    return 0 if rollback_doc["status"] in {"passed", "engineering-complete-evidence-pending"} else 4


if __name__ == "__main__":
    raise SystemExit(main())
