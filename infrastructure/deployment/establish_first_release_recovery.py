#!/usr/bin/env python3
"""Establish and attest first-release OCI recovery without fabricating an application rollback target."""
from __future__ import annotations

import argparse
import configparser
import hashlib
import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = ROOT / "contracts/infrastructure/first-release-recovery-v1.schema.json"
OCID = re.compile(r"ocid1\.[A-Za-z0-9._-]+")


def utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def sha256_text(value: str | None) -> str | None:
    return hashlib.sha256(value.encode("utf-8")).hexdigest() if value else None


def redact(value: str) -> str:
    return OCID.sub("<oci-id-redacted>", value)[:1000]


def load_region(profile: str) -> str:
    path = Path(os.environ.get("OCI_CLI_CONFIG_FILE", Path.home() / ".oci" / "config"))
    parser = configparser.RawConfigParser()
    parser.read(path, encoding="utf-8")
    section = "DEFAULT" if profile == "DEFAULT" else profile
    if section != "DEFAULT" and not parser.has_section(section):
        raise RuntimeError(f"OCI profile is missing: {profile}")
    region = parser.defaults().get("region") if section == "DEFAULT" else parser.get(section, "region", fallback="")
    if not region:
        raise RuntimeError("OCI profile does not declare a region")
    return region


def normalize_data(data: Any) -> Any:
    if isinstance(data, dict) and isinstance(data.get("items"), list):
        return data["items"]
    return data


def oci(profile: str, region: str, *args: str, timeout: int = 180) -> Any:
    command = ["oci", *args, "--profile", profile, "--region", region, "--output", "json"]
    completed = subprocess.run(
        command,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=timeout,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or f"OCI command failed: {' '.join(command[:4])}"
        raise RuntimeError(redact(detail))
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError("OCI command returned invalid JSON") from error
    return normalize_data(payload.get("data"))


def one_active(items: list[dict[str, Any]], field: str, value: str, label: str) -> dict[str, Any]:
    matches = [
        item for item in items
        if item.get(field) == value and item.get("lifecycle-state") not in {"TERMINATED", "DELETED"}
    ]
    if len(matches) != 1:
        raise RuntimeError(f"{label} must resolve to exactly one active resource")
    return matches[0]


def instance_network_posture(profile: str, region: str, compartment_id: str, instance: dict[str, Any]) -> tuple[bool, bool]:
    attachments = oci(
        profile, region, "compute", "vnic-attachment", "list",
        "--compartment-id", compartment_id, "--instance-id", instance["id"], "--all",
    )
    active = [item for item in attachments if item.get("lifecycle-state") != "DETACHED"]
    if not active:
        raise RuntimeError("instance has no active VNIC attachment")
    no_public_ip = True
    public_ip_prohibited = True
    for attachment in active:
        vnic = oci(profile, region, "network", "vnic", "get", "--vnic-id", attachment["vnic-id"])
        if vnic.get("public-ip"):
            no_public_ip = False
        subnet = oci(profile, region, "network", "subnet", "get", "--subnet-id", vnic["subnet-id"])
        if subnet.get("prohibit-public-ip-on-vnic") is not True:
            public_ip_prohibited = False
    return no_public_ip, public_ip_prohibited


def fallback_capacity(instance: dict[str, Any]) -> tuple[str | None, float | None, float | None]:
    shape = instance.get("shape-config") or {}
    return instance.get("shape"), shape.get("ocpus"), shape.get("memory-in-gbs")


def reviewed_fallback_capacity(instance: dict[str, Any]) -> bool:
    return fallback_capacity(instance) == ("VM.Standard.E5.Flex", 4, 24)


def traffic_is_off(profile: str, region: str, compartment_id: str, nlb_name: str) -> bool:
    nlbs = oci(
        profile, region, "nlb", "network-load-balancer", "list",
        "--compartment-id", compartment_id, "--all",
    )
    matches = [
        item for item in nlbs
        if item.get("display-name") == nlb_name and item.get("lifecycle-state") not in {"DELETED", "FAILED"}
    ]
    if not matches:
        return True
    if len(matches) != 1:
        raise RuntimeError("public network load balancer name is ambiguous")
    nlb = matches[0]
    backend_sets = oci(
        profile, region, "nlb", "backend-set", "list",
        "--network-load-balancer-id", nlb["id"], "--all",
    )
    for backend_set in backend_sets:
        name = backend_set.get("name")
        if not name:
            raise RuntimeError("network load balancer backend set has no stable name")
        backends = oci(
            profile, region, "nlb", "backend", "list",
            "--network-load-balancer-id", nlb["id"], "--backend-set-name", name, "--all",
        )
        if any(item.get("is-offline") is not True for item in backends):
            return False
    return True


def primary_boot_volume(profile: str, region: str, compartment_id: str, instance: dict[str, Any]) -> str:
    attachments = oci(
        profile, region, "compute", "boot-volume-attachment", "list",
        "--compartment-id", compartment_id,
        "--instance-id", instance["id"],
        "--availability-domain", instance["availability-domain"],
    )
    active = [item for item in attachments if item.get("lifecycle-state") != "DETACHED"]
    if len(active) != 1:
        raise RuntimeError("primary instance must have exactly one active boot volume attachment")
    return active[0]["boot-volume-id"]


def matching_backup(
    profile: str,
    region: str,
    compartment_id: str,
    boot_volume_id: str,
    backup_name: str | None,
) -> dict[str, Any] | None:
    backups = oci(
        profile, region, "bv", "boot-volume-backup", "list",
        "--compartment-id", compartment_id,
        "--boot-volume-id", boot_volume_id,
        "--all",
    )
    active = [item for item in backups if item.get("lifecycle-state") != "TERMINATED"]
    if backup_name is not None:
        matches = [item for item in active if item.get("display-name") == backup_name]
        if len(matches) > 1:
            raise RuntimeError("boot-volume backup name is ambiguous for the primary boot volume")
        return matches[0] if matches else None
    reusable = [
        item for item in active
        if item.get("lifecycle-state") == "AVAILABLE"
        and item.get("type") == "FULL"
        and (item.get("freeform-tags") or {}).get("liqi_purpose") == "predeploy-restore-point"
    ]
    reusable.sort(key=lambda item: item.get("time-created") or "", reverse=True)
    return reusable[0] if reusable else None


def backup_age_seconds(backup: dict[str, Any], now: datetime | None = None) -> int:
    raw = backup.get("time-created")
    if not isinstance(raw, str):
        raise RuntimeError("boot-volume backup has no creation timestamp")
    created = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if created.tzinfo is None:
        raise RuntimeError("boot-volume backup timestamp has no timezone")
    observed = now or datetime.now(timezone.utc)
    age = int((observed - created.astimezone(timezone.utc)).total_seconds())
    if age < -300:
        raise RuntimeError("boot-volume backup timestamp is in the future")
    return max(0, age)


def ensure_full_backup(
    profile: str,
    region: str,
    compartment_id: str,
    boot_volume_id: str,
    backup_name: str | None,
    git_sha: str,
    execute: bool,
    max_wait_seconds: int,
) -> tuple[dict[str, Any] | None, bool]:
    existing = matching_backup(profile, region, compartment_id, boot_volume_id, backup_name)
    if existing is not None:
        if existing.get("lifecycle-state") != "AVAILABLE" or existing.get("type") != "FULL":
            raise RuntimeError("named boot-volume backup is not AVAILABLE and FULL")
        return existing, False
    if not execute:
        return None, False
    create_name = backup_name or f"liqi-v1-predeploy-{git_sha[:12]}"
    tags = json.dumps({"liqi_git_sha": git_sha, "liqi_purpose": "predeploy-restore-point"}, separators=(",", ":"))
    created = oci(
        profile, region, "bv", "boot-volume-backup", "create",
        "--boot-volume-id", boot_volume_id,
        "--display-name", create_name,
        "--type", "FULL",
        "--freeform-tags", tags,
        "--wait-for-state", "AVAILABLE",
        "--max-wait-seconds", str(max_wait_seconds),
        timeout=max_wait_seconds + 120,
    )
    if created.get("lifecycle-state") != "AVAILABLE" or created.get("type") != "FULL":
        raise RuntimeError("created boot-volume backup did not become AVAILABLE and FULL")
    return created, True


def exercise_fallback(
    profile: str,
    region: str,
    instance: dict[str, Any],
    execute: bool,
    max_wait_seconds: int,
) -> tuple[str, bool, bool]:
    initial = instance.get("lifecycle-state")
    if initial != "STOPPED":
        raise RuntimeError("fallback instance must be STOPPED before the recovery exercise")
    if not execute:
        return "not-run", True, False
    started = False
    mutation = False
    restored = False
    try:
        running = oci(
            profile, region, "compute", "instance", "action",
            "--instance-id", instance["id"], "--action", "START",
            "--wait-for-state", "RUNNING",
            "--max-wait-seconds", str(max_wait_seconds),
            timeout=max_wait_seconds + 120,
        )
        mutation = True
        started = running.get("lifecycle-state") == "RUNNING"
        if not started:
            raise RuntimeError("fallback instance did not reach RUNNING")
        stopped = oci(
            profile, region, "compute", "instance", "action",
            "--instance-id", instance["id"], "--action", "STOP",
            "--wait-for-state", "STOPPED",
            "--max-wait-seconds", str(max_wait_seconds),
            timeout=max_wait_seconds + 120,
        )
        restored = stopped.get("lifecycle-state") == "STOPPED"
        if not restored:
            raise RuntimeError("fallback instance did not return to STOPPED")
        return "passed", True, mutation
    except Exception:
        if started and not restored:
            try:
                stopped = oci(
                    profile, region, "compute", "instance", "action",
                    "--instance-id", instance["id"], "--action", "STOP",
                    "--wait-for-state", "STOPPED",
                    "--max-wait-seconds", str(max_wait_seconds),
                    timeout=max_wait_seconds + 120,
                )
                restored = stopped.get("lifecycle-state") == "STOPPED"
            except Exception:
                restored = False
        raise


def write_evidence(path: Path, document: dict[str, Any]) -> None:
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    errors = list(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(document))
    if errors:
        raise RuntimeError(f"generated recovery evidence is invalid: {errors[0].message}")
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.new.{os.getpid()}")
    temporary.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", default="DEFAULT")
    parser.add_argument("--compartment-name", default="liqi-live")
    parser.add_argument("--primary-instance-name", default="liqi-live-primary")
    parser.add_argument("--fallback-instance-name", default="liqi-live-primary-fallback-stopped")
    parser.add_argument("--network-load-balancer-name", default="liqi-live-edge-nlb")
    parser.add_argument("--backup-name")
    parser.add_argument("--approval-reference")
    parser.add_argument("--max-wait-seconds", type=int, default=1800)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    if args.output.exists() or args.output.is_symlink():
        raise SystemExit("--output must name a new regular file")
    output = args.output.resolve()
    try:
        output.relative_to(ROOT.resolve())
    except ValueError:
        pass
    else:
        raise SystemExit("--output must remain outside the source repository")
    if not 60 <= args.max_wait_seconds <= 7200:
        raise SystemExit("--max-wait-seconds must be 60..7200")
    if args.execute and (not args.approval_reference or len(args.approval_reference.strip()) < 3):
        raise SystemExit("--execute requires a non-secret approval reference")
    if not args.execute and args.approval_reference:
        raise SystemExit("approval reference is valid only with --execute")

    status = subprocess.check_output(
        ["git", "status", "--porcelain", "--untracked-files=all"], cwd=ROOT, text=True, timeout=30
    ).strip()
    if status:
        raise SystemExit("clean exact-SHA worktree is required")
    git_sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, timeout=30).strip()
    backup_name = args.backup_name
    region = load_region(args.profile)
    if region != "ap-singapore-2":
        raise SystemExit("first-release recovery is restricted to ap-singapore-2")

    document: dict[str, Any] = {
        "schema_version": "liqi.infrastructure.first-release-recovery/v1",
        "git_sha": git_sha,
        "environment": "production",
        "status": "blocked",
        "strategy": "traffic-disable-and-boot-volume-restore",
        "public_traffic_enabled": False,
        "primary": {
            "instance_id_sha256": None,
            "public_ip_present": None,
            "subnet_public_ip_prohibited": None,
            "boot_volume_backup_id_sha256": None,
            "backup_state": "MISSING",
            "backup_type": None,
            "backup_age_seconds": None,
            "backup_purpose": "predeploy-restore-point",
        },
        "fallback": {
            "instance_id_sha256": None,
            "lifecycle_state": "UNKNOWN",
            "public_ip_present": None,
            "subnet_public_ip_prohibited": None,
            "start_stop_test_status": "not-run",
            "restored_original_state": False,
            "shape": None,
            "ocpus": None,
            "memory_gib": None,
        },
        "database": {"down_migration_allowed": False, "recovery_mode": "forward-only"},
        "approval_reference": args.approval_reference or "dry-run-no-approval",
        "observed_at": utc(),
        "oci_mutation_performed": False,
        "blockers": [],
    }

    try:
        compartments = oci(args.profile, region, "iam", "compartment", "list", "--all")
        compartment = one_active(compartments, "name", args.compartment_name, "production compartment")
        instances = oci(
            args.profile, region, "compute", "instance", "list",
            "--compartment-id", compartment["id"], "--all",
        )
        primary = one_active(instances, "display-name", args.primary_instance_name, "primary instance")
        fallback = one_active(instances, "display-name", args.fallback_instance_name, "fallback instance")
        if primary["id"] == fallback["id"]:
            raise RuntimeError("primary and fallback instances must be distinct")
        if primary.get("lifecycle-state") != "RUNNING":
            raise RuntimeError("primary instance must be RUNNING while the predeploy restore point is established")
        document["primary"]["instance_id_sha256"] = sha256_text(primary["id"])
        document["fallback"]["instance_id_sha256"] = sha256_text(fallback["id"])
        document["fallback"]["lifecycle_state"] = fallback.get("lifecycle-state", "UNKNOWN")
        fallback_shape, fallback_ocpus, fallback_memory = fallback_capacity(fallback)
        document["fallback"].update({
            "shape": fallback_shape,
            "ocpus": fallback_ocpus,
            "memory_gib": fallback_memory,
        })
        if not reviewed_fallback_capacity(fallback):
            raise RuntimeError("fallback instance must retain the reviewed E5 4 OCPU/24 GiB capacity")

        primary_private, primary_subnet_private = instance_network_posture(args.profile, region, compartment["id"], primary)
        document["primary"]["public_ip_present"] = not primary_private
        document["primary"]["subnet_public_ip_prohibited"] = primary_subnet_private
        if not primary_private:
            raise RuntimeError("primary instance has a public IP before cutover")
        fallback_private, fallback_subnet_private = instance_network_posture(args.profile, region, compartment["id"], fallback)
        document["fallback"]["public_ip_present"] = not fallback_private
        document["fallback"]["subnet_public_ip_prohibited"] = fallback_subnet_private
        if not fallback_private:
            raise RuntimeError("fallback instance has a public IP")
        if not traffic_is_off(args.profile, region, compartment["id"], args.network_load_balancer_name):
            document["public_traffic_enabled"] = True
            raise RuntimeError("public network load balancer still has an online backend")

        boot_volume_id = primary_boot_volume(args.profile, region, compartment["id"], primary)
        if args.execute:
            # From this point an OCI mutation may have been accepted even if a later wait times out.
            document["oci_mutation_performed"] = True
        backup, backup_mutation = ensure_full_backup(
            args.profile, region, compartment["id"], boot_volume_id, backup_name,
            git_sha, args.execute, args.max_wait_seconds,
        )
        if backup is None:
            document["blockers"].append("Fresh full predeploy boot-volume backup is missing; rerun with --execute and approval.")
        else:
            age = backup_age_seconds(backup)
            if age > 86400:
                raise RuntimeError("full boot-volume backup is older than the 24-hour production recovery window")
            document["primary"].update({
                "boot_volume_backup_id_sha256": sha256_text(backup["id"]),
                "backup_state": backup.get("lifecycle-state", "UNKNOWN"),
                "backup_type": backup.get("type"),
                "backup_age_seconds": age,
            })

        test_status, restored, fallback_mutation = exercise_fallback(
            args.profile, region, fallback, args.execute, args.max_wait_seconds
        )
        document["fallback"].update({
            "lifecycle_state": "STOPPED" if restored else fallback.get("lifecycle-state", "UNKNOWN"),
            "start_stop_test_status": test_status,
            "restored_original_state": restored,
        })
        document["oci_mutation_performed"] = document["oci_mutation_performed"] or backup_mutation or fallback_mutation
        if not args.execute:
            document["blockers"].append("Fallback start/stop recovery exercise was not executed.")
        document["status"] = "passed" if not document["blockers"] and args.execute else "blocked"
    except Exception as error:
        document["status"] = "failed"
        document["blockers"].append(redact(str(error)))
    document["observed_at"] = utc()
    write_evidence(output, document)
    print(json.dumps({
        "status": document["status"],
        "git_sha": git_sha,
        "backup_state": document["primary"]["backup_state"],
        "fallback_state": document["fallback"]["lifecycle_state"],
        "traffic_enabled": document["public_traffic_enabled"],
        "oci_mutation_performed": document["oci_mutation_performed"],
        "output": str(output),
    }, sort_keys=True))
    return 0 if document["status"] == "passed" else 2 if document["status"] == "blocked" else 3


if __name__ == "__main__":
    raise SystemExit(main())
