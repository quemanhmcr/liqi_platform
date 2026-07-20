#!/usr/bin/env python3
"""Aggregate temporary-E5 operator inputs before state adoption planning or OCI apply."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from infrastructure.deployment.discover_v1_live_adoption import ADDRESSES  # noqa: E402

SCHEMAS = {
    "adoption": ROOT / "contracts/infrastructure/adoption-manifest-v1.schema.json",
    "state_backend": ROOT / "contracts/infrastructure/state-backend-evidence-v1.schema.json",
    "adoption_result": ROOT / "contracts/infrastructure/adoption-result-v1.schema.json",
    "build_result": ROOT / "contracts/runtime/linux-release-build-result-v1.schema.json",
    "recovery": ROOT / "contracts/infrastructure/first-release-recovery-v1.schema.json",
    "output": ROOT / "contracts/infrastructure/pre-apply-readiness-v1.schema.json",
}
PUBLICATION_VERIFIER = ROOT / "beam/scripts/validate_linux_release_build_result.py"
CHECK_ORDER = (
    "oci-adoption-handoff",
    "state-backend",
    "state-adoption",
    "protected-tfvars",
    "signed-x86-release",
    "recovery-target",
    "protected-environment",
)
OWNERS = {
    "oci-adoption-handoff": "infrastructure-lead",
    "state-backend": "deployment-operator",
    "state-adoption": "deployment-operator",
    "protected-tfvars": "deployment-operator",
    "signed-x86-release": "deployment-operator",
    "recovery-target": "deployment-operator",
    "protected-environment": "deployment-operator",
}
EXPECTED_ADDRESSES = {address for address, _kind in ADDRESSES.values()}
FORBIDDEN_KEY_MATERIAL = re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")
ASSIGNMENT = r"(?m)^\s*{name}\s*=\s*\"([^\"]*)\"\s*(?:#.*)?$"


def sha256(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def load_json(path: Path, schema: Path, label: str) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"{label} must be a regular non-symlink file")
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError(f"{label} JSON root must be an object")
    schema_document = json.loads(schema.read_text(encoding="utf-8"))
    errors = sorted(
        Draft202012Validator(schema_document, format_checker=FormatChecker()).iter_errors(document),
        key=lambda item: list(item.absolute_path),
    )
    if errors:
        error = errors[0]
        location = ".".join(map(str, error.absolute_path)) or "$"
        raise ValueError(f"{label} invalid at {location}: {error.message}")
    return document


def digest_if_regular(path: Path | None) -> str | None:
    if path is None or not path.is_file() or path.is_symlink():
        return None
    return sha256(path)


def assignment(text: str, name: str) -> str | None:
    match = re.search(ASSIGNMENT.format(name=re.escape(name)), text)
    return match.group(1).strip() if match else None


def bool_assignment(text: str, name: str) -> bool | None:
    match = re.search(rf"(?m)^\s*{re.escape(name)}\s*=\s*(true|false)\s*(?:#.*)?$", text)
    return None if match is None else match.group(1) == "true"


def check(name: str, status: str, detail: str) -> dict[str, str]:
    return {"name": name, "owner": OWNERS[name], "status": status, "detail": detail[:1000]}


def adoption_check(path: Path | None, git_sha: str) -> tuple[dict[str, str], dict[str, Any] | None]:
    if path is None:
        return check("oci-adoption-handoff", "blocked", "OCI adoption manifest is missing; wait for the infrastructure lead handoff."), None
    try:
        document = load_json(path, SCHEMAS["adoption"], "adoption manifest")
    except (OSError, ValueError, json.JSONDecodeError) as error:
        return check("oci-adoption-handoff", "failed", str(error)), None
    if document.get("git_sha") != git_sha or document.get("capacity_profile") != "e5-temporary":
        return check("oci-adoption-handoff", "failed", "Adoption manifest source SHA or capacity profile does not match this checkout."), document
    addresses = {item["address"] for item in document["imports"]}
    missing = set(document["missing_addresses"])
    graph_is_exact = not (addresses & missing) and addresses | missing == EXPECTED_ADDRESSES
    if document["status"] != "passed" or document["blockers"] or not graph_is_exact:
        parts = list(document["blockers"])
        if not graph_is_exact:
            parts.append("imported and missing addresses do not partition the exact source graph")
        return check("oci-adoption-handoff", "blocked", "; ".join(parts) or "OCI handoff is not complete."), document
    detail = "Compatible existing OCI resources are ready for no-replacement adoption."
    if missing:
        detail += f" The reviewed plan may create {len(missing)} explicitly missing source-managed resources."
    return check("oci-adoption-handoff", "passed", detail), document


def state_backend_check(path: Path | None, git_sha: str) -> tuple[dict[str, str], dict[str, Any] | None]:
    if path is None:
        return check("state-backend", "blocked", "Independent PostgreSQL state-backend evidence is missing."), None
    try:
        document = load_json(path, SCHEMAS["state_backend"], "state-backend evidence")
    except (OSError, ValueError, json.JSONDecodeError) as error:
        return check("state-backend", "failed", str(error)), None
    if document.get("git_sha") != git_sha:
        return check("state-backend", "failed", "State-backend evidence Git SHA does not match this checkout."), document
    return check("state-backend", "passed", "TLS verify-full, advisory locking, encrypted backup and isolated restore evidence passed."), document


def tfvars_check(path: Path | None, git_sha: str, now: datetime) -> tuple[dict[str, str], str | None]:
    if path is None:
        return check("protected-tfvars", "blocked", "Protected temporary-E5 tfvars file is missing."), None
    if path.is_symlink():
        return check("protected-tfvars", "failed", "Protected tfvars must be a regular non-symlink file."), None
    try:
        resolved = path.resolve(strict=True)
    except OSError as error:
        return check("protected-tfvars", "failed", f"Protected tfvars is unreadable: {error}"), None
    if not resolved.is_file():
        return check("protected-tfvars", "failed", "Protected tfvars must be a regular non-symlink file."), None
    try:
        resolved.relative_to(ROOT.resolve())
    except ValueError:
        pass
    else:
        return check("protected-tfvars", "failed", "Live tfvars must remain outside the source repository."), None
    if os.name == "posix" and resolved.stat().st_mode & 0o077:
        return check("protected-tfvars", "failed", "Protected tfvars permissions must not grant group or world access."), None
    text = resolved.read_text(encoding="utf-8")
    if FORBIDDEN_KEY_MATERIAL.search(text):
        return check("protected-tfvars", "failed", "Private key material is forbidden in tfvars."), None

    blockers: list[str] = []
    if assignment(text, "capacity_profile") != "e5-temporary":
        blockers.append("capacity_profile must be e5-temporary")
    if bool_assignment(text, "acknowledge_capacity_availability_and_cost") is not True:
        blockers.append("capacity/cost acknowledgement must be true")
    if bool_assignment(text, "acknowledge_host_bundle_signing_key") is not True:
        blockers.append("host-bundle trust acknowledgement must be true")
    if assignment(text, "operation_mode") != "plan":
        blockers.append("operation_mode must remain plan before saved-plan approval")
    if assignment(text, "source_git_sha") != git_sha:
        blockers.append("source_git_sha must match the exact checkout")
    availability_domain = assignment(text, "availability_domain") or ""
    if not (availability_domain == "AP-SINGAPORE-2-AD-1" or availability_domain.endswith(":AP-SINGAPORE-2-AD-1")):
        blockers.append("availability_domain must resolve to AP-SINGAPORE-2-AD-1")
    image = assignment(text, "oracle_linux_image_ocid") or ""
    if not image.startswith("ocid1.image.oc1.ap-singapore-2.") or "REPLACE" in image.upper():
        blockers.append("reviewed Singapore x86_64 Oracle Linux image OCID is missing")
    tenancy = assignment(text, "tenancy_ocid") or ""
    if not tenancy.startswith("ocid1.tenancy.oc1..") or "replace" in tenancy.lower():
        blockers.append("live tenancy OCID is missing")
    for name in ("management_plane_evidence_id", "state_backend_lock_evidence_id"):
        value = assignment(text, name) or ""
        if len(value) < 3:
            blockers.append(f"{name} is missing")
    key_id = assignment(text, "host_bundle_signing_key_id") or ""
    if len(key_id) < 3 or key_id == "source-validation-v1" or "replace" in key_id.lower():
        blockers.append("reviewed production host-bundle key ID is missing")
    if "REPLACE_WITH_REVIEWED_ED25519_PUBLIC_KEY" in text or "-----BEGIN PUBLIC KEY-----" not in text:
        blockers.append("reviewed host-bundle Ed25519 public key is missing")
    bastion_sources = {"10.42.20.100/32", "10.42.20.109/32"}
    if not all(source in text for source in bastion_sources):
        blockers.append("both technically accepted OCI Bastion /32 sources are required")
    bastion_assignment = re.search(r"(?ms)^\s*bastion_ssh_source_cidrs\s*=\s*\[(.*?)\]", text)
    if bastion_assignment is None or "0.0.0.0/0" in bastion_assignment.group(1):
        blockers.append("Bastion SSH sources must remain exact and non-world")
    if "management_wireguard_peer_cidr" in text:
        blockers.append("superseded WireGuard management input is forbidden")
    expires = assignment(text, "temporary_e5_expires_at") or ""
    try:
        expiry = datetime.fromisoformat(expires.replace("Z", "+00:00"))
        if expiry.tzinfo is None:
            raise ValueError("timezone missing")
        if not (now < expiry <= now.replace(microsecond=0) + timedelta(days=90)):
            blockers.append("temporary E5 expiry must be in the future and within 90 days")
    except ValueError:
        blockers.append("temporary E5 expiry is missing or invalid")
    if blockers:
        return check("protected-tfvars", "blocked", "; ".join(blockers)), sha256(resolved)
    return check("protected-tfvars", "passed", "Protected E5 tfvars contain exact-SHA reviewed inputs and no private key material."), sha256(resolved)


def adoption_result_check(
    path: Path | None,
    git_sha: str,
    adoption_sha: str | None,
    var_sha: str | None,
    expected_addresses: set[str] | None,
) -> tuple[dict[str, str], dict[str, Any] | None]:
    if path is None:
        return check("state-adoption", "blocked", "Executed OpenTofu state-adoption result is missing."), None
    try:
        document = load_json(path, SCHEMAS["adoption_result"], "adoption result")
    except (OSError, ValueError, json.JSONDecodeError) as error:
        return check("state-adoption", "failed", str(error)), None
    if document.get("git_sha") != git_sha or document.get("capacity_profile") != "e5-temporary":
        return check("state-adoption", "failed", "Adoption result source SHA or profile mismatch."), document
    if adoption_sha is None or var_sha is None or expected_addresses is None:
        return check("state-adoption", "blocked", "Adoption result cannot be bound until manifest and protected tfvars are present."), document
    if document.get("manifest_sha256") != adoption_sha or document.get("var_file_sha256") != var_sha:
        return check("state-adoption", "failed", "Adoption result input digest binding mismatch."), document
    addresses = set(document.get("imported_addresses", [])) | set(document.get("already_present_addresses", []))
    if addresses != expected_addresses:
        return check("state-adoption", "failed", "Adoption result does not cover the exact manifest import address set."), document
    if (
        document.get("operation") != "execute"
        or document.get("status") != "passed"
        or document.get("blockers")
        or not isinstance(document.get("state_mutation_performed"), bool)
        or document.get("oci_mutation_performed") is not False
    ):
        return check("state-adoption", "blocked", "State adoption has not completed as an executed pass."), document
    return check("state-adoption", "passed", "All reviewed existing resources are present in encrypted OpenTofu state."), document


def publication_check(
    path: Path | None,
    release_trust_dir: Path | None,
    native_trust_dir: Path | None,
    git_sha: str,
) -> tuple[dict[str, str], dict[str, Any] | None]:
    if path is None:
        return check("signed-x86-release", "blocked", "Signed x86_64 Linux release build result is missing."), None
    if release_trust_dir is None or native_trust_dir is None:
        return check("signed-x86-release", "blocked", "Release and native public trust directories are required."), None
    if not path.is_file() or path.is_symlink():
        return check("signed-x86-release", "failed", "Linux release build result must be a regular non-symlink file."), None
    completed = subprocess.run(
        [
            sys.executable,
            str(PUBLICATION_VERIFIER),
            "--result", str(path.resolve()),
            "--git-sha", git_sha,
            "--target-triple", "x86_64-unknown-linux-gnu",
            "--release-trust-dir", str(release_trust_dir.resolve()),
            "--native-trust-dir", str(native_trust_dir.resolve()),
        ],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=900,
    )
    if completed.returncode:
        detail = (completed.stderr or completed.stdout or "publication verification failed").strip()
        return check("signed-x86-release", "failed", detail[-1000:]), None
    try:
        document = load_json(path, SCHEMAS["build_result"], "Linux release build result")
    except (OSError, ValueError, json.JSONDecodeError) as error:
        return check("signed-x86-release", "failed", str(error)), None
    return check("signed-x86-release", "passed", "Exact-SHA x86_64 release and native artifacts passed cryptographic cross-binding verification."), document


def recovery_check(path: Path | None, build_path: Path | None, build: dict[str, Any] | None, git_sha: str) -> dict[str, str]:
    if path is None:
        return check("recovery-target", "blocked", "First-release infrastructure recovery evidence is missing.")
    if build_path is None or build is None:
        return check("recovery-target", "blocked", "Recovery cannot be bound until the signed release publication passes.")
    try:
        recovery = load_json(path, SCHEMAS["recovery"], "first-release recovery evidence")
        manifest_path = build_path.resolve().parent / build["manifest"]["filename"]
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as error:
        return check("recovery-target", "failed", f"Recovery evidence is unreadable or invalid: {error}")
    database = manifest.get("database_compatibility") or {}
    if (
        recovery.get("git_sha") != git_sha
        or recovery.get("status") != "passed"
        or recovery.get("blockers")
        or recovery.get("public_traffic_enabled") is not False
        or manifest.get("rollback_target_release_id") is not None
        or database != {"minimum_migration": 8, "maximum_migration": 8, "rollback_safe_through": 8}
    ):
        return check("recovery-target", "failed", "First-release recovery or forward-only release identity binding failed.")
    return check("recovery-target", "passed", "First-release recovery is bound to a stopped private fallback, a full predeploy boot-volume backup, traffic-off state and forward-only database recovery.")

def environment_check() -> dict[str, str]:
    missing: list[str] = []
    if not os.environ.get("TF_ENCRYPTION"):
        missing.append("TF_ENCRYPTION")
    if "sslmode=verify-full" not in os.environ.get("PG_CONN_STR", ""):
        missing.append("PG_CONN_STR verify-full")
    if os.environ.get("PG_SCHEMA_NAME") != "opentofu_v1_live":
        missing.append("PG_SCHEMA_NAME")
    for name in ("PG_SKIP_SCHEMA_CREATION", "PG_SKIP_TABLE_CREATION", "PG_SKIP_INDEX_CREATION"):
        if os.environ.get(name) != "true":
            missing.append(name)
    if missing:
        return check("protected-environment", "blocked", "Protected backend environment is incomplete: " + ", ".join(missing))
    return check("protected-environment", "passed", "Encrypted state and verify-full PostgreSQL backend environment is present.")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--adoption-manifest", type=Path)
    parser.add_argument("--state-backend-evidence", type=Path)
    parser.add_argument("--adoption-result", type=Path)
    parser.add_argument("--var-file", type=Path)
    parser.add_argument("--linux-release-build-result", type=Path)
    parser.add_argument("--release-trust-dir", type=Path)
    parser.add_argument("--native-trust-dir", type=Path)
    parser.add_argument("--recovery-target", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--now", help="UTC RFC3339 override for deterministic tests")
    args = parser.parse_args()

    status = subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=all"], cwd=ROOT, text=True).strip()
    if status:
        raise SystemExit("clean exact-SHA worktree is required for pre-apply readiness")
    git_sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    now = datetime.fromisoformat(args.now.replace("Z", "+00:00")) if args.now else datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise SystemExit("--now must include a timezone")

    if args.output.is_symlink():
        raise SystemExit("--output must not be a symlink")
    output = args.output.resolve()
    try:
        output.relative_to(ROOT.resolve())
    except ValueError:
        pass
    else:
        raise SystemExit("--output must be outside the source repository")
    if output.exists() or output.is_symlink():
        raise SystemExit("--output must not already exist")

    adoption, adoption_document = adoption_check(args.adoption_manifest, git_sha)
    state_backend, _state_document = state_backend_check(args.state_backend_evidence, git_sha)
    tfvars, var_sha = tfvars_check(args.var_file, git_sha, now)
    adoption_sha = digest_if_regular(args.adoption_manifest)
    expected_adopted_addresses = None if adoption_document is None else {item["address"] for item in adoption_document["imports"]}
    state_adoption, _adoption_result_document = adoption_result_check(
        args.adoption_result, git_sha, adoption_sha, var_sha, expected_adopted_addresses
    )
    publication, build_document = publication_check(args.linux_release_build_result, args.release_trust_dir, args.native_trust_dir, git_sha)
    recovery = recovery_check(args.recovery_target, args.linux_release_build_result, build_document, git_sha)
    protected_environment = environment_check()

    by_name = {item["name"]: item for item in (adoption, state_backend, state_adoption, tfvars, publication, recovery, protected_environment)}
    checks = [by_name[name] for name in CHECK_ORDER]
    statuses = {item["status"] for item in checks}
    overall = "failed" if "failed" in statuses else "blocked" if "blocked" in statuses else "passed"
    blockers = [f"{item['name']}: {item['detail']}" for item in checks if item["status"] != "passed"]
    document = {
        "schema_version": "liqi.infrastructure.pre-apply-readiness/v1",
        "generated_at": now.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "git_sha": git_sha,
        "capacity_profile": "e5-temporary",
        "status": overall,
        "checks": checks,
        "inputs": {
            "adoption_manifest_sha256": adoption_sha,
            "state_backend_evidence_sha256": digest_if_regular(args.state_backend_evidence),
            "adoption_result_sha256": digest_if_regular(args.adoption_result),
            "var_file_sha256": var_sha,
            "linux_release_build_result_sha256": digest_if_regular(args.linux_release_build_result),
            "recovery_target_sha256": digest_if_regular(args.recovery_target),
        },
        "blockers": blockers,
        "state_mutation_performed": False,
        "oci_mutation_performed": False,
    }
    schema = json.loads(SCHEMAS["output"].read_text(encoding="utf-8"))
    errors = list(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(document))
    if errors:
        raise SystemExit(f"generated readiness result is invalid: {errors[0].message}")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.new.{os.getpid()}")
    temporary.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    os.chmod(temporary, 0o600)
    os.replace(temporary, output)
    print(json.dumps({"status": overall, "blockers": len(blockers), "sha256": sha256(output)}, sort_keys=True))
    return 0 if overall == "passed" else 2 if overall == "blocked" else 1


if __name__ == "__main__":
    raise SystemExit(main())
