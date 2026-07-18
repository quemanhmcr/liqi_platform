#!/usr/bin/env python3
"""Build a signed, self-contained Senior 4 deployment wrapper from provider-owned outputs."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[2]
IDENT = re.compile(r"^[a-z0-9][a-z0-9._-]{2,95}$")
CREDENTIAL = re.compile(r"^systemd-credential://([a-z][a-z0-9-]{1,63})$")


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def validate(schema_path: Path, document: Any, label: str) -> None:
    validator = Draft202012Validator(load(schema_path), format_checker=FormatChecker())
    failures = sorted(validator.iter_errors(document), key=lambda item: list(item.absolute_path))
    if failures:
        failure = failures[0]
        location = ".".join(map(str, failure.absolute_path)) or "$"
        raise RuntimeError(f"{label} invalid at {location}: {failure.message}")


def safe_child(base: Path, name: str) -> Path:
    relative = PurePosixPath(name)
    if relative.is_absolute() or ".." in relative.parts or len(relative.parts) != 1:
        raise RuntimeError(f"unsafe artifact filename: {name}")
    return base / name


def copy_exact(source: Path, output_dir: Path, seen: dict[str, str], relative_name: str | None = None) -> Path:
    if not source.is_file():
        raise FileNotFoundError(source)
    sha = digest(source)
    name = relative_name or source.name
    relative = PurePosixPath(name)
    if relative.is_absolute() or ".." in relative.parts:
        raise RuntimeError(f"unsafe deployment package path: {name}")
    key = relative.as_posix()
    previous = seen.get(key)
    if previous is not None and previous != sha:
        raise RuntimeError(f"deployment package filename collision: {key}")
    target = output_dir.joinpath(*relative.parts)
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        shutil.copyfile(source, target)
        os.chmod(target, 0o640)
    seen[key] = sha
    return target


def run_runtime_verifier(manifest: Path, trust_dir: Path, output: Path) -> dict[str, Any]:
    command = [
        sys.executable,
        str(ROOT / "beam/scripts/validate_release_manifest.py"),
        "--manifest", str(manifest),
        "--trust-dir", str(trust_dir),
        "--output", str(output),
    ]
    result = subprocess.run(command, cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, timeout=900)
    if result.returncode:
        raise RuntimeError("Senior 1 release verification failed: " + (result.stderr or result.stdout or "unknown")[-4096:])
    document = load(output)
    validate(ROOT / "contracts/runtime/runtime-artifact-result-v1.schema.json", document, "runtime artifact result")
    if document["status"] != "passed" or document["blockers"]:
        raise RuntimeError(f"runtime artifact result is not passed: {document['blockers']}")
    return document


def runtime_credentials(runtime: dict[str, Any]) -> list[str]:
    references = [
        runtime["http"]["secretRef"],
        runtime["database"]["secretRef"],
        runtime["shutdown"]["drainTokenRef"],
        runtime["security"]["probeTokenRef"],
    ]
    names: list[str] = []
    for reference in references:
        match = CREDENTIAL.fullmatch(reference)
        if not match:
            raise RuntimeError(f"production runtime secret must be a systemd credential: {reference}")
        names.append(match.group(1))
    if len(names) != len(set(names)):
        raise RuntimeError("runtime secret identities must be unique")
    expected = {"phoenix-secret-key-base", "database-role-urls", "drain-token", "platform-probe-token"}
    if set(names) != expected:
        raise RuntimeError(f"runtime credential contract mismatch: expected={sorted(expected)} actual={sorted(names)}")
    return names


def database_ready(contract: dict[str, Any], readiness: dict[str, Any], runtime: dict[str, Any]) -> int:
    if contract["status"] != "accepted" or contract["authority"] != "postgresql-only-durable-authority":
        raise RuntimeError("database provider contract is not accepted")
    required = int(contract["requiredMigration"])
    if runtime["database"]["requiredMigrationVersion"] != required:
        raise RuntimeError("runtime/database required migration mismatch")
    if runtime["database"].get("credentialFormat") != "role-url-bundle-v1":
        raise RuntimeError("runtime database credential format is unsupported")
    if not (
        readiness["status"] == "passed"
        and readiness["ready"] is True
        and readiness["writeReady"] is True
        and readiness["reason"] == "ready"
        and readiness["currentVersion"] == required
        and readiness["requiredVersion"] == required
        and readiness["obanMigrationVersion"] == readiness["requiredObanMigrationVersion"] == 14
        and readiness["inRecovery"] is False
    ):
        raise RuntimeError("database migration readiness is not exact and write-ready")
    return required


def verify_native(manifest: Path, provider_git_sha: str) -> dict[str, Any]:
    document = load(manifest)
    validate(ROOT / "contracts/native/native-artifact-v1.schema.json", document, f"native manifest {manifest}")
    if document["source_revision"] != provider_git_sha or document["target_triple"] != "aarch64-unknown-linux-gnu":
        raise RuntimeError("native source revision or target does not match release")
    result = subprocess.run(
        [sys.executable, str(ROOT / "native/scripts/verify_artifact.py"), "--manifest", str(manifest)],
        cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, timeout=300,
    )
    if result.returncode:
        raise RuntimeError("Senior 3 native verification failed: " + (result.stderr or result.stdout or "unknown")[-4096:])
    return document


def verify_rollback(descriptor_path: Path, release_id: str, required_migration: int) -> dict[str, Any]:
    descriptor = load(descriptor_path)
    validate(ROOT / "contracts/deployment/release-target-v1.schema.json", descriptor, "rollback descriptor")
    if descriptor["release_id"] != release_id:
        raise RuntimeError("rollback descriptor release identity mismatch")
    compatibility = descriptor["database_compatibility"]
    if not compatibility["minimum_migration"] <= required_migration <= compatibility["rollback_safe_through"]:
        raise RuntimeError("rollback target has no evidence for the required migration")
    if compatibility["database_rollback_allowed"] is not False:
        raise RuntimeError("database down migration is forbidden")
    return descriptor


def sign(payload: Path, key: Path, signature: Path) -> None:
    result = subprocess.run(
        ["openssl", "pkeyutl", "-sign", "-rawin", "-inkey", str(key), "-in", str(payload), "-out", str(signature)],
        text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, timeout=30,
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "deployment wrapper signing failed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider-manifest", required=True, type=Path)
    parser.add_argument("--provider-trust-dir", required=True, type=Path)
    parser.add_argument("--runtime-config", required=True, type=Path)
    parser.add_argument("--database-contract", required=True, type=Path)
    parser.add_argument("--migration-readiness", required=True, type=Path)
    parser.add_argument("--native-manifest", action="append", default=[], type=Path)
    parser.add_argument("--rollback-target-descriptor", required=True, type=Path)
    parser.add_argument("--deployment-key-id", required=True)
    parser.add_argument("--deployment-signing-key", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()

    if not IDENT.fullmatch(args.deployment_key_id) or not args.deployment_signing_key.is_file():
        raise SystemExit("valid deployment key ID and private key are required")

    provider = load(args.provider_manifest)
    runtime = load(args.runtime_config)
    database = load(args.database_contract)
    readiness = load(args.migration_readiness)
    validate(ROOT / "contracts/deployment/mix-release-v1.schema.json", provider, "Senior 1 Mix manifest")
    validate(ROOT / "contracts/runtime/runtime-config-v1.schema.json", runtime, "runtime config")
    validate(ROOT / "contracts/database/database-runtime-v1.schema.json", database, "database contract")
    validate(ROOT / "contracts/database/migration-readiness-v1.schema.json", readiness, "migration readiness")

    release_id = provider["release_id"]
    git_sha = provider["git_sha"]
    if runtime["releaseId"] != release_id or runtime["environment"] != "production":
        raise RuntimeError("runtime config release/environment mismatch")
    runtime_credentials(runtime)
    required_migration = database_ready(database, readiness, runtime)
    if provider["database_compatibility"] != {"minimum_migration": 8, "maximum_migration": 8, "rollback_safe_through": 4}:
        raise RuntimeError("Senior 1 database compatibility contract changed")
    rollback = verify_rollback(args.rollback_target_descriptor, provider["rollback_target_release_id"], required_migration)

    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    seen: dict[str, str] = {}
    provider_copy = copy_exact(args.provider_manifest.resolve(), output_dir, seen)
    provider_base = args.provider_manifest.resolve().parent
    for name in (
        provider["artifact"]["filename"],
        provider["artifact"]["signature"]["signature_filename"],
        provider["manifest_signature"]["signature_filename"],
    ):
        copy_exact(safe_child(provider_base, name), output_dir, seen)

    with tempfile.TemporaryDirectory(prefix="liqi-runtime-verify-") as temporary:
        result_path = Path(temporary) / f"{release_id}.runtime-artifact-result-v1.json"
        runtime_result = run_runtime_verifier(args.provider_manifest.resolve(), args.provider_trust_dir.resolve(), result_path)
        if runtime_result["git_sha"] != git_sha or runtime_result["release_id"] != release_id or runtime_result["artifact_sha256"] != provider["artifact"]["sha256"]:
            raise RuntimeError("runtime artifact evidence identity mismatch")
        runtime_result_copy = copy_exact(result_path, output_dir, seen)

    runtime_copy = copy_exact(args.runtime_config.resolve(), output_dir, seen)
    database_copy = copy_exact(args.database_contract.resolve(), output_dir, seen)
    readiness_copy = copy_exact(args.migration_readiness.resolve(), output_dir, seen)
    rollback_copy = copy_exact(args.rollback_target_descriptor.resolve(), output_dir, seen)

    supplied_native: dict[str, tuple[Path, dict[str, Any]]] = {}
    for manifest_path in args.native_manifest:
        native = verify_native(manifest_path.resolve(), git_sha)
        artifact_id = native["artifact"]
        if artifact_id in supplied_native:
            raise RuntimeError(f"duplicate native manifest for {artifact_id}")
        supplied_native[artifact_id] = (manifest_path.resolve(), native)

    native_entries = []
    provider_native_ids = set()
    for reference in provider["native_artifacts"]:
        artifact_id = reference["artifact_id"]
        provider_native_ids.add(artifact_id)
        if artifact_id not in supplied_native:
            raise RuntimeError(f"Mix manifest references missing native artifact {artifact_id}")
        manifest_path, native = supplied_native[artifact_id]
        if manifest_path.name != reference["manifest_filename"] or digest(manifest_path) != reference["manifest_sha256"]:
            raise RuntimeError(f"Mix/native manifest identity mismatch for {artifact_id}")
        native_base = manifest_path.parent
        manifest_copy = copy_exact(manifest_path, output_dir, seen)
        for relative in (native["artifact_path"], native["sbom"]["path"], native["provenance"]["path"], native["signature"]["bundle_path"]):
            source = (native_base / relative).resolve()
            try:
                source.relative_to(native_base)
            except ValueError as error:
                raise RuntimeError(f"native file escapes manifest directory: {relative}") from error
            copy_exact(source, output_dir, seen, relative)
        native_entries.append({
            "artifact_id": artifact_id,
            "manifest_filename": manifest_copy.name,
            "manifest_sha256": digest(manifest_copy),
            "install_relative_path": reference["install_relative_path"],
            "required": reference["required"],
        })
    if set(supplied_native) != provider_native_ids:
        raise RuntimeError("supplied native manifests do not exactly match the signed Mix manifest")
    if runtime["native"]["mode"] == "required" and not native_entries:
        raise RuntimeError("runtime requires native artifacts but the Mix manifest declares none")

    wrapper = {
        "schema_version": "liqi.deployment.mix-deployment/v1",
        "release_id": release_id,
        "git_sha": git_sha,
        "provider_manifest": {"schema_version": provider["schema_version"], "filename": provider_copy.name, "sha256": digest(provider_copy)},
        "runtime_artifact_result": {"schema_version": runtime_result["schema_version"], "filename": runtime_result_copy.name, "sha256": digest(runtime_result_copy)},
        "runtime_config": {"schema_version": runtime["schemaVersion"], "filename": runtime_copy.name, "sha256": digest(runtime_copy)},
        "database_contract": {"schema_version": database["contractVersion"], "filename": database_copy.name, "sha256": digest(database_copy)},
        "migration_readiness": {"schema_version": readiness["schemaVersion"], "filename": readiness_copy.name, "sha256": digest(readiness_copy)},
        "native_artifacts": native_entries,
        "rollback_target": {"release_id": rollback["release_id"], "descriptor_filename": rollback_copy.name, "descriptor_sha256": digest(rollback_copy)},
        "signature": {"algorithm": "ed25519", "key_id": args.deployment_key_id, "signature_filename": f"{release_id}.mix-deployment-v1.json.sig", "signed_payload": "exact-wrapper-bytes"},
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    validate(ROOT / "contracts/deployment/mix-deployment-v1.schema.json", wrapper, "deployment wrapper")
    wrapper_path = output_dir / f"{release_id}.mix-deployment-v1.json"
    wrapper_path.write_text(json.dumps(wrapper, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    signature_path = output_dir / wrapper["signature"]["signature_filename"]
    sign(wrapper_path, args.deployment_signing_key.resolve(), signature_path)
    os.chmod(wrapper_path, 0o640)
    os.chmod(signature_path, 0o640)
    print(json.dumps({
        "schema_version": "liqi.deployment.mix-deployment-preparation-result/v1",
        "status": "prepared",
        "release_id": release_id,
        "git_sha": git_sha,
        "wrapper_path": str(wrapper_path),
        "wrapper_sha256": digest(wrapper_path),
        "signature_path": str(signature_path),
        "signature_sha256": digest(signature_path),
        "required_migration": required_migration,
        "rollback_target_release_id": rollback["release_id"],
        "oci_mutation_performed": False,
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
