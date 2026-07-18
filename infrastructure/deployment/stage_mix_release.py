#!/usr/bin/env python3
"""Verify and stage a self-contained signed Mix deployment package without activating it."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[2]
CONTRACT_ROOTS = {
    "deployment": (ROOT / "contracts/deployment", Path("/usr/local/share/liqi/contracts/deployment")),
    "runtime": (ROOT / "contracts/runtime", Path("/usr/local/share/liqi/contracts/runtime")),
    "database": (ROOT / "contracts/database", Path("/usr/local/lib/liqi-database/contracts/database")),
    "native": (ROOT / "contracts/native", Path("/usr/local/lib/liqi-native/contracts/native")),
}
IDENT = re.compile(r"^[a-z0-9][a-z0-9._-]{2,95}$")
CREDENTIAL = re.compile(r"^systemd-credential://([a-z][a-z0-9-]{1,63})$")
MAX_EXPANDED_BYTES = 2 * 1024 * 1024 * 1024
FORBIDDEN_CONTENT = (
    re.compile(rb"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(rb"postgres(?:ql)?://[^\s:/]+:[^\s@/]+@", re.I),
)


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def contract(name: str, namespace: str = "deployment") -> Path:
    for root in CONTRACT_ROOTS[namespace]:
        path = root / name
        if path.is_file():
            return path
    raise FileNotFoundError(f"{namespace}/{name}")


def validate(name: str, document: Any, label: str, namespace: str = "deployment") -> None:
    validator = Draft202012Validator(load(contract(name, namespace)), format_checker=FormatChecker())
    failures = sorted(validator.iter_errors(document), key=lambda item: list(item.absolute_path))
    if failures:
        failure = failures[0]
        location = ".".join(map(str, failure.absolute_path)) or "$"
        raise RuntimeError(f"{label} invalid at {location}: {failure.message}")


def safe_relative(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise RuntimeError(f"unsafe relative path: {value}")
    return path


def package_file(base: Path, filename: str, expected_sha: str) -> Path:
    relative = safe_relative(filename)
    path = base.joinpath(*relative.parts).resolve()
    try:
        path.relative_to(base.resolve())
    except ValueError as error:
        raise RuntimeError(f"package file escapes artifact directory: {filename}") from error
    if not path.is_file() or digest(path) != expected_sha:
        raise RuntimeError(f"package file identity mismatch: {filename}")
    return path


def trusted_key(directory: Path, key_id: str) -> Path:
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{2,63}", key_id):
        raise RuntimeError("invalid signing key ID")
    key = directory / f"{key_id}.pem"
    if not key.is_file():
        raise RuntimeError(f"trusted public key missing: {key}")
    return key


def verify_ed25519(payload: Path, signature: Path, key: Path) -> None:
    result = subprocess.run(
        ["openssl", "pkeyutl", "-verify", "-rawin", "-pubin", "-inkey", str(key), "-in", str(payload), "-sigfile", str(signature)],
        text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, timeout=30,
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or f"signature verification failed: {payload}")


def extract_release(archive: Path, destination: Path) -> None:
    total = 0
    with tarfile.open(archive, "r:gz") as source:
        members = source.getmembers()
        if not members:
            raise RuntimeError("release archive is empty")
        for member in members:
            relative = safe_relative(member.name)
            if member.issym() or member.islnk() or member.isdev() or member.isfifo():
                raise RuntimeError(f"unsupported archive member: {member.name}")
            if not (member.isfile() or member.isdir()):
                raise RuntimeError(f"archive member is not file/directory: {member.name}")
            if member.mode & (stat.S_ISUID | stat.S_ISGID | stat.S_ISVTX):
                raise RuntimeError(f"special permission bits forbidden: {member.name}")
            total += member.size
            if total > MAX_EXPANDED_BYTES:
                raise RuntimeError("release archive exceeds 2 GiB expanded limit")
            target = destination.joinpath(*relative.parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                os.chmod(target, 0o755)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            stream = source.extractfile(member)
            if stream is None:
                raise RuntimeError(f"cannot read archive member: {member.name}")
            with target.open("wb") as output:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    output.write(chunk)
            os.chmod(target, 0o755 if member.mode & 0o111 else 0o644)
            if target.stat().st_size <= 8 * 1024 * 1024:
                content = target.read_bytes()
                for pattern in FORBIDDEN_CONTENT:
                    if pattern.search(content):
                        raise RuntimeError(f"release contains forbidden secret material: {member.name}")


def verify_commands(root: Path, manifest: dict[str, Any]) -> None:
    for action, argv in manifest["runtime"]["commands"].items():
        relative = safe_relative(argv[0])
        path = root.joinpath(*relative.parts)
        if not path.is_file() or not os.access(path, os.X_OK):
            raise RuntimeError(f"release command unavailable for {action}: {argv[0]}")


def credential_names(runtime: dict[str, Any]) -> list[str]:
    refs = [runtime["http"]["secretRef"], runtime["database"]["secretRef"], runtime["shutdown"]["drainTokenRef"], runtime["security"]["probeTokenRef"]]
    names = []
    for ref in refs:
        match = CREDENTIAL.fullmatch(ref)
        if not match:
            raise RuntimeError(f"unsupported production credential reference: {ref}")
        names.append(match.group(1))
    if set(names) != {"phoenix-secret-key-base", "database-role-urls", "drain-token", "platform-probe-token"}:
        raise RuntimeError("runtime credential identities do not match the published provider contract")
    return names


def verify_runtime_result(result: dict[str, Any], provider: dict[str, Any], release_id: str, git_sha: str) -> None:
    if not (
        result["status"] == "passed"
        and not result["blockers"]
        and result["release_id"] == release_id
        and result["git_sha"] == git_sha
        and result["artifact_sha256"] == provider["artifact"]["sha256"]
    ):
        raise RuntimeError("runtime artifact result is not passed and bound to this exact release archive")


def database_version(contract_doc: dict[str, Any], readiness: dict[str, Any], runtime: dict[str, Any]) -> int:
    required = int(contract_doc["requiredMigration"])
    if contract_doc["status"] != "accepted" or contract_doc["compatibility"]["postgresqlMajor"] != 17:
        raise RuntimeError("database provider contract is not accepted for PostgreSQL 17")
    if runtime["database"]["requiredMigrationVersion"] != required or runtime["database"].get("credentialFormat") != "role-url-bundle-v1":
        raise RuntimeError("runtime database binding does not match provider contract")
    if not (
        readiness["status"] == "passed" and readiness["ready"] is True and readiness["writeReady"] is True
        and readiness["reason"] == "ready" and readiness["currentVersion"] == required
        and readiness["requiredVersion"] == required and readiness["obanMigrationVersion"] == 14
        and readiness["requiredObanMigrationVersion"] == 14 and readiness["inRecovery"] is False
    ):
        raise RuntimeError("database migration readiness is not exact and write-ready")
    return required


def native_verifier(explicit: Path | None) -> Path:
    candidates = [explicit, ROOT / "native/scripts/verify_artifact.py", Path("/usr/local/lib/liqi-native/native/scripts/verify_artifact.py")]
    for candidate in candidates:
        if candidate is not None and candidate.is_file():
            return candidate
    raise RuntimeError("Senior 3 native verifier is unavailable")


def verify_native(base: Path, entry: dict[str, Any], release_git_sha: str, staged: Path, final_release: Path, verifier: Path) -> dict[str, Any]:
    manifest_path = package_file(base, entry["manifest_filename"], entry["manifest_sha256"])
    manifest = load(manifest_path)
    validate("native-artifact-v1.schema.json", manifest, "native manifest", "native")
    if manifest["artifact"] != entry["artifact_id"] or manifest["source_revision"] != release_git_sha:
        raise RuntimeError("native artifact identity/source mismatch")
    result = subprocess.run([sys.executable, str(verifier), "--manifest", str(manifest_path)], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, timeout=300)
    if result.returncode:
        raise RuntimeError("Senior 3 native verification failed: " + (result.stderr or result.stdout or "unknown")[-4096:])
    source = manifest_path.parent.joinpath(*safe_relative(manifest["artifact_path"]).parts)
    if not source.is_file() or source.stat().st_size != manifest["artifact_size_bytes"] or digest(source) != manifest["artifact_sha256"]:
        raise RuntimeError("native shared object checksum/size mismatch")
    install = safe_relative(entry["install_relative_path"])
    target = staged.joinpath(*install.parts)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    os.chmod(target, 0o644)
    return {
        "artifact_id": manifest["artifact"],
        "manifest_sha256": entry["manifest_sha256"],
        "artifact_sha256": manifest["artifact_sha256"],
        "install_path": str(final_release.joinpath(*install.parts)),
        "provider_verification_status": "passed",
        "load_probe_status": "pending-activation",
    }


def immutable_tree(path: Path) -> None:
    import grp
    gid = grp.getgrnam("liqi").gr_gid
    for root, directories, files in os.walk(path):
        for name in directories:
            item = Path(root, name); os.chown(item, 0, gid); os.chmod(item, 0o550)
        for name in files:
            item = Path(root, name); executable = bool(item.stat().st_mode & 0o111); os.chown(item, 0, gid); os.chmod(item, 0o550 if executable else 0o440)
    os.chown(path, 0, gid); os.chmod(path, 0o550)


def atomic_write(path: Path, data: bytes, mode: int = 0o440) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.new.{os.getpid()}")
    temporary.write_bytes(data); os.chmod(temporary, mode); os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--deployment-wrapper", required=True, type=Path)
    parser.add_argument("--artifact-dir", required=True, type=Path)
    parser.add_argument("--deployment-trust-dir", required=True, type=Path)
    parser.add_argument("--provider-trust-dir", required=True, type=Path)
    parser.add_argument("--native-verifier", type=Path)
    parser.add_argument("--release-root", type=Path, default=Path("/opt/liqi/releases"))
    parser.add_argument("--runtime-config-root", type=Path, default=Path("/etc/liqi/runtime/releases"))
    parser.add_argument("--recovery-root", type=Path, default=Path("/var/lib/liqi/recovery"))
    parser.add_argument("--approval-reference")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    wrapper = load(args.deployment_wrapper)
    validate("mix-deployment-v1.schema.json", wrapper, "deployment wrapper")
    if args.execute and (os.name != "posix" or os.geteuid() != 0):
        raise SystemExit("live staging requires root on POSIX")
    if args.execute and (not args.approval_reference or len(args.approval_reference.strip()) < 3):
        raise SystemExit("live staging requires approval reference")

    wrapper_signature = args.artifact_dir / wrapper["signature"]["signature_filename"]
    verify_ed25519(args.deployment_wrapper, wrapper_signature, trusted_key(args.deployment_trust_dir, wrapper["signature"]["key_id"]))

    provider_path = package_file(args.artifact_dir, wrapper["provider_manifest"]["filename"], wrapper["provider_manifest"]["sha256"])
    provider = load(provider_path); validate("mix-release-v1.schema.json", provider, "Senior 1 Mix manifest")
    runtime_result_path = package_file(args.artifact_dir, wrapper["runtime_artifact_result"]["filename"], wrapper["runtime_artifact_result"]["sha256"])
    runtime_result = load(runtime_result_path); validate("runtime-artifact-result-v1.schema.json", runtime_result, "runtime artifact result", "runtime")
    runtime_path = package_file(args.artifact_dir, wrapper["runtime_config"]["filename"], wrapper["runtime_config"]["sha256"])
    runtime = load(runtime_path); validate("runtime-config-v1.schema.json", runtime, "runtime config", "runtime")
    database_path = package_file(args.artifact_dir, wrapper["database_contract"]["filename"], wrapper["database_contract"]["sha256"])
    database = load(database_path); validate("database-runtime-v1.schema.json", database, "database contract", "database")
    readiness_path = package_file(args.artifact_dir, wrapper["migration_readiness"]["filename"], wrapper["migration_readiness"]["sha256"])
    readiness = load(readiness_path); validate("migration-readiness-v1.schema.json", readiness, "migration readiness", "database")
    rollback_path = package_file(args.artifact_dir, wrapper["rollback_target"]["descriptor_filename"], wrapper["rollback_target"]["descriptor_sha256"])
    rollback = load(rollback_path); validate("release-target-v1.schema.json", rollback, "rollback descriptor")

    release_id = wrapper["release_id"]
    git_sha = wrapper["git_sha"]
    if not IDENT.fullmatch(release_id) or provider["release_id"] != release_id or provider["git_sha"] != git_sha:
        raise RuntimeError("deployment/provider release identity mismatch")
    verify_runtime_result(runtime_result, provider, release_id, git_sha)
    if runtime["releaseId"] != release_id or runtime["environment"] != "production":
        raise RuntimeError("runtime config release identity/environment mismatch")
    names = credential_names(runtime)
    required_migration = database_version(database, readiness, runtime)
    if rollback["release_id"] != wrapper["rollback_target"]["release_id"] or provider["rollback_target_release_id"] != rollback["release_id"]:
        raise RuntimeError("rollback target identity mismatch")
    rollback_db = rollback["database_compatibility"]
    if not rollback_db["minimum_migration"] <= required_migration <= rollback_db["rollback_safe_through"]:
        raise RuntimeError("rollback target is not proven compatible with the live migration")

    manifest_signature = args.artifact_dir / provider["manifest_signature"]["signature_filename"]
    verify_ed25519(provider_path, manifest_signature, trusted_key(args.provider_trust_dir, provider["manifest_signature"]["key_id"]))
    archive = args.artifact_dir / provider["artifact"]["filename"]
    archive_signature = args.artifact_dir / provider["artifact"]["signature"]["signature_filename"]
    if not archive.is_file() or archive.stat().st_size != provider["artifact"]["size_bytes"] or digest(archive) != provider["artifact"]["sha256"]:
        raise RuntimeError("Mix release archive checksum/size mismatch")
    if digest(archive_signature) != provider["artifact"]["signature"]["signature_sha256"]:
        raise RuntimeError("Mix release artifact signature checksum mismatch")
    verify_ed25519(archive, archive_signature, trusted_key(args.provider_trust_dir, provider["artifact"]["signature"]["key_id"]))

    release_path = args.release_root / release_id
    if release_path != Path(provider["installation"]["release_directory"]):
        raise RuntimeError("release installation path mismatch")
    if release_path.exists():
        raise RuntimeError(f"release path already exists: {release_path}")
    runtime_install = args.runtime_config_root / f"{release_id}.json"

    with tempfile.TemporaryDirectory(prefix=f"liqi-stage-{release_id}-") as directory:
        staged = Path(directory) / release_id; staged.mkdir()
        extract_release(archive, staged); verify_commands(staged, provider)
        verifier = native_verifier(args.native_verifier) if wrapper["native_artifacts"] else None
        native_results = [verify_native(args.artifact_dir, entry, git_sha, staged, release_path, verifier) for entry in wrapper["native_artifacts"]]
        shutil.copyfile(provider_path, staged / "deployment-manifest.json")
        shutil.copyfile(args.deployment_wrapper, staged / "deployment-wrapper.json")
        os.chmod(staged / "deployment-manifest.json", 0o644); os.chmod(staged / "deployment-wrapper.json", 0o644)

        retained_root = args.recovery_root / "release-inputs"
        retained_provider = retained_root / f"{release_id}.mix-release-v1.json"
        retained_wrapper = retained_root / f"{release_id}.mix-deployment-v1.json"
        retained_runtime = retained_root / f"{release_id}.runtime-config-v1.json"
        retained_database = retained_root / f"{release_id}.database-runtime-v1.json"
        retained_readiness = retained_root / f"{release_id}.migration-readiness-v1.json"
        descriptor_path = args.recovery_root / "release-targets" / f"{release_id}.json"
        descriptor = {
            "schema_version": "liqi.deployment.release-target/v1",
            "release_id": release_id,
            "runtime_generation": "beam-v1",
            "git_sha": git_sha,
            "release_path": str(release_path),
            "source_manifest": {"schema_version": provider["schema_version"], "sha256": digest(provider_path), "retained_path": str(retained_provider)},
            "services": [{"unit": "liqi-beam.service", "start_order": 1, "stop_timeout_seconds": provider["runtime"]["drain_timeout_seconds"] + 30}],
            "drain": {"argv": ["/usr/local/libexec/liqi-release-command", "drain"], "timeout_seconds": provider["runtime"]["drain_timeout_seconds"]},
            "health": {"argv": ["/usr/local/libexec/liqi-release-command", "health"], "timeout_seconds": provider["runtime"]["health_timeout_seconds"]},
            "database_compatibility": {"minimum_migration": required_migration, "maximum_migration": required_migration, "rollback_safe_through": required_migration, "database_rollback_allowed": False},
            "database_compatibility_evidence": {"schema_version": readiness["schemaVersion"], "sha256": digest(readiness_path), "retained_path": str(retained_readiness)},
            "rollback_target_release_id": rollback["release_id"],
            "runtime_config_path": str(runtime_install),
            "credential_directory": "/run/liqi/secrets/beam",
            "required_credentials": names,
            "configuration_paths": [str(runtime_install), "/run/liqi/secrets/beam"],
            "created_at": wrapper["created_at"],
        }
        validate("release-target-v1.schema.json", descriptor, "release descriptor")
        descriptor_bytes = (json.dumps(descriptor, indent=2, sort_keys=True) + "\n").encode()

        status = "planned"
        if args.execute:
            args.release_root.mkdir(parents=True, exist_ok=True); args.runtime_config_root.mkdir(parents=True, exist_ok=True)
            retained_root.mkdir(parents=True, exist_ok=True); descriptor_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = args.release_root / f".{release_id}.staging.{os.getpid()}"
            if temporary.exists(): shutil.rmtree(temporary)
            shutil.copytree(staged, temporary, copy_function=shutil.copy2); immutable_tree(temporary); os.replace(temporary, release_path)
            atomic_write(runtime_install, runtime_path.read_bytes())
            for source, target in ((provider_path, retained_provider), (args.deployment_wrapper, retained_wrapper), (runtime_path, retained_runtime), (database_path, retained_database), (readiness_path, retained_readiness)):
                atomic_write(target, source.read_bytes())
            atomic_write(descriptor_path, descriptor_bytes)
            status = "staged"

    evidence = {
        "schema_version": "liqi.deployment.installed-release/v1",
        "release_id": release_id, "git_sha": git_sha, "release_path": str(release_path),
        "runtime_config_path": str(runtime_install), "runtime_config_sha256": digest(runtime_path),
        "manifest_sha256": digest(provider_path), "artifact_sha256": digest(archive),
        "native_artifacts": native_results, "descriptor_sha256": hashlib.sha256(descriptor_bytes).hexdigest(),
        "status": status, "approval_reference": args.approval_reference if args.execute else None,
        "installed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"), "mutation_performed": args.execute,
    }
    validate("installed-release-v1.schema.json", evidence, "installation evidence")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(evidence, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
