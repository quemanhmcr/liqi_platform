#!/usr/bin/env python3
"""Validate, install and describe a signed ARM64 Mix release without activating it."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[2]
CONTRACT_ROOTS = (ROOT / "contracts/deployment", Path("/usr/local/share/liqi/contracts/deployment"))
IDENT = re.compile(r"^[a-z0-9][a-z0-9._-]{2,95}$")
MAX_EXPANDED_BYTES = 2 * 1024 * 1024 * 1024
FORBIDDEN_CONTENT = (
    re.compile(rb"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(rb"postgres(?:ql)?://[^\s:/]+:[^\s@/]+@", re.I),
)


def contract(name: str) -> Path:
    for root in CONTRACT_ROOTS:
        candidate = root / name
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(name)


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def schema_errors(schema_name: str, document: Any) -> list[str]:
    schema = load(contract(schema_name))
    return [
        f"{'.'.join(map(str, item.absolute_path)) or '$'}: {item.message}"
        for item in sorted(
            Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(document),
            key=lambda value: list(value.absolute_path),
        )
    ]


def verify_ed25519(payload: Path, signature: Path, key: Path) -> None:
    result = subprocess.run(
        ["openssl", "pkeyutl", "-verify", "-rawin", "-pubin", "-inkey", str(key), "-in", str(payload), "-sigfile", str(signature)],
        text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, timeout=30,
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or f"signature verification failed: {payload}")


def trusted_key(trust_dir: Path, key_id: str) -> Path:
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{2,63}", key_id):
        raise RuntimeError("invalid signing key ID")
    key = trust_dir / f"{key_id}.pem"
    if not key.is_file():
        raise RuntimeError(f"trusted key is missing: {key}")
    return key


def safe_relative(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise RuntimeError(f"unsafe relative path: {value}")
    return path


def extract_release(archive: Path, destination: Path) -> None:
    total = 0
    with tarfile.open(archive, "r:gz") as source:
        members = source.getmembers()
        if not members:
            raise RuntimeError("release archive is empty")
        for member in members:
            path = safe_relative(member.name)
            if member.issym() or member.islnk() or member.isdev() or member.isfifo():
                raise RuntimeError(f"release archive contains unsupported member: {member.name}")
            if not (member.isfile() or member.isdir()):
                raise RuntimeError(f"release archive member is not file/directory: {member.name}")
            if member.mode & (stat.S_ISUID | stat.S_ISGID | stat.S_ISVTX):
                raise RuntimeError(f"release archive contains special permission bits: {member.name}")
            total += member.size
            if total > MAX_EXPANDED_BYTES:
                raise RuntimeError("release archive exceeds 2 GiB expanded limit")
            target = destination.joinpath(*path.parts)
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
            mode = 0o755 if member.mode & 0o111 else 0o644
            os.chmod(target, mode)
            if target.stat().st_size <= 8 * 1024 * 1024:
                content = target.read_bytes()
                for pattern in FORBIDDEN_CONTENT:
                    if pattern.search(content):
                        raise RuntimeError(f"release contains forbidden secret material: {member.name}")


def verify_release_commands(root: Path, manifest: dict[str, Any]) -> None:
    for name, argv in manifest["runtime"]["commands"].items():
        executable = safe_relative(argv[0])
        path = root.joinpath(*executable.parts)
        if not path.is_file() or not os.access(path, os.X_OK):
            raise RuntimeError(f"release {name} executable is missing or not executable: {argv[0]}")


def verify_native(
    staged_root: Path,
    final_release_path: Path,
    artifact_dir: Path,
    trust_dir: Path,
    reference: dict[str, Any],
    execute: bool,
) -> dict[str, Any]:
    manifest_path = artifact_dir / reference["manifest_filename"]
    if not manifest_path.is_file() or sha256(manifest_path) != reference["manifest_sha256"]:
        raise RuntimeError(f"native manifest identity mismatch: {reference['artifact_id']}")
    manifest = load(manifest_path)
    failures = schema_errors("native-artifact-v1.schema.json", manifest)
    if failures:
        raise RuntimeError("invalid native manifest: " + "; ".join(failures))
    if manifest["artifact_id"] != reference["artifact_id"] or manifest["target_triple"] != "aarch64-unknown-linux-gnu":
        raise RuntimeError("native artifact identity/target mismatch")
    artifact = artifact_dir / manifest["artifact"]["filename"]
    signature = artifact_dir / manifest["artifact"]["signature"]["signature_filename"]
    if not artifact.is_file() or artifact.stat().st_size != manifest["artifact"]["size_bytes"] or sha256(artifact) != manifest["artifact"]["sha256"]:
        raise RuntimeError("native artifact checksum/size mismatch")
    verify_ed25519(artifact, signature, trusted_key(trust_dir, manifest["artifact"]["signature"]["key_id"]))
    install_relative = safe_relative(manifest["artifact"]["install_relative_path"])
    target = staged_root.joinpath(*install_relative.parts)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(artifact, target)
    os.chmod(target, 0o644)
    probe_status = "planned"
    if execute:
        argv = manifest["load"]["probe_command"]
        executable = safe_relative(argv[0])
        command = [str(staged_root.joinpath(*executable.parts)), *argv[1:]]
        result = subprocess.run(command, cwd=staged_root, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, timeout=60)
        if result.returncode:
            raise RuntimeError(f"native load probe failed: {(result.stderr or result.stdout)[-2048:]}")
        probe_status = "passed"
    return {
        "artifact_id": manifest["artifact_id"],
        "manifest_sha256": sha256(manifest_path),
        "artifact_sha256": sha256(artifact),
        "install_path": str(final_release_path.joinpath(*install_relative.parts)),
        "load_probe_status": probe_status,
    }


def immutable_tree(path: Path) -> None:
    import grp

    liqi_gid = grp.getgrnam("liqi").gr_gid
    for root, directories, files in os.walk(path):
        for name in directories:
            directory = Path(root, name)
            os.chown(directory, 0, liqi_gid)
            os.chmod(directory, 0o550)
        for name in files:
            file = Path(root, name)
            executable = bool(file.stat().st_mode & 0o111)
            os.chown(file, 0, liqi_gid)
            os.chmod(file, 0o550 if executable else 0o440)
    os.chown(path, 0, liqi_gid)
    os.chmod(path, 0o550)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--artifact-dir", required=True, type=Path)
    parser.add_argument("--trust-dir", required=True, type=Path)
    parser.add_argument("--release-root", type=Path, default=Path("/opt/liqi/releases"))
    parser.add_argument("--recovery-root", type=Path, default=Path("/var/lib/liqi/recovery"))
    parser.add_argument("--approval-reference")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    manifest = load(args.manifest)
    failures = schema_errors("mix-release-v1.schema.json", manifest)
    if failures:
        raise SystemExit("invalid Mix release manifest: " + "; ".join(failures))
    release_id = manifest["release_id"]
    if not IDENT.fullmatch(release_id):
        raise SystemExit("invalid release ID")
    if args.execute and (os.name != "posix" or os.geteuid() != 0):
        raise SystemExit("live staging requires root on POSIX")
    if args.execute and (not args.approval_reference or len(args.approval_reference.strip()) < 3):
        raise SystemExit("live staging requires approval reference")

    manifest_signature = args.artifact_dir / manifest["manifest_signature"]["signature_filename"]
    verify_ed25519(args.manifest, manifest_signature, trusted_key(args.trust_dir, manifest["manifest_signature"]["key_id"]))
    artifact = args.artifact_dir / manifest["artifact"]["filename"]
    artifact_signature = args.artifact_dir / manifest["artifact"]["signature"]["signature_filename"]
    if not artifact.is_file() or artifact.stat().st_size != manifest["artifact"]["size_bytes"] or sha256(artifact) != manifest["artifact"]["sha256"]:
        raise SystemExit("Mix release archive checksum/size mismatch")
    verify_ed25519(artifact, artifact_signature, trusted_key(args.trust_dir, manifest["artifact"]["signature"]["key_id"]))

    release_path = args.release_root / release_id
    expected = Path(manifest["installation"]["release_directory"])
    if release_path != expected:
        raise SystemExit(f"release path mismatch: provider={release_path} manifest={expected}")
    if release_path.exists():
        raise SystemExit(f"release path already exists: {release_path}")

    with tempfile.TemporaryDirectory(prefix=f"liqi-stage-{release_id}-") as directory:
        staged = Path(directory) / release_id
        staged.mkdir()
        extract_release(artifact, staged)
        verify_release_commands(staged, manifest)
        native_results = []
        for reference in manifest["native_artifacts"]:
            result = verify_native(
                staged,
                release_path,
                args.artifact_dir,
                args.trust_dir,
                reference,
                args.execute,
            )
            native_results.append(result)
        deployment_manifest = staged / "deployment-manifest.json"
        shutil.copyfile(args.manifest, deployment_manifest)
        os.chmod(deployment_manifest, 0o644)

        retained_manifest = args.recovery_root / "release-inputs" / f"{release_id}.mix-release-v1.json"
        descriptor_path = args.recovery_root / "release-targets" / f"{release_id}.json"
        descriptor = {
            "schema_version": "liqi.deployment.release-target/v1",
            "release_id": release_id,
            "runtime_generation": "beam-v1",
            "git_sha": manifest["git_sha"],
            "release_path": str(release_path),
            "source_manifest": {
                "schema_version": manifest["schema_version"],
                "sha256": sha256(args.manifest),
                "retained_path": str(retained_manifest),
            },
            "services": [{"unit": "liqi-beam.service", "start_order": 1, "stop_timeout_seconds": manifest["runtime"]["drain_timeout_seconds"] + 30}],
            "drain": {"argv": ["/usr/local/libexec/liqi-release-command", "drain"], "timeout_seconds": manifest["runtime"]["drain_timeout_seconds"]},
            "health": {"argv": ["/usr/local/libexec/liqi-release-command", "health"], "timeout_seconds": manifest["runtime"]["health_timeout_seconds"]},
            "database_compatibility": {**manifest["database_compatibility"], "database_rollback_allowed": False},
            "rollback_target_release_id": manifest["rollback_target_release_id"],
            "configuration_paths": ["/etc/liqi/runtime", "/run/liqi/secrets/beam"],
            "created_at": manifest["created_at"],
        }
        descriptor_failures = schema_errors("release-target-v1.schema.json", descriptor)
        if descriptor_failures:
            raise RuntimeError("invalid release descriptor: " + "; ".join(descriptor_failures))
        descriptor_bytes = (json.dumps(descriptor, indent=2, sort_keys=True) + "\n").encode()

        status = "planned"
        if args.execute:
            args.release_root.mkdir(parents=True, exist_ok=True)
            args.recovery_root.joinpath("release-inputs").mkdir(parents=True, exist_ok=True)
            args.recovery_root.joinpath("release-targets").mkdir(parents=True, exist_ok=True)
            temporary = args.release_root / f".{release_id}.staging.{os.getpid()}"
            if temporary.exists():
                shutil.rmtree(temporary)
            shutil.copytree(staged, temporary, copy_function=shutil.copy2)
            immutable_tree(temporary)
            os.replace(temporary, release_path)
            retained_manifest.write_bytes(args.manifest.read_bytes())
            os.chmod(retained_manifest, 0o440)
            descriptor_path.write_bytes(descriptor_bytes)
            os.chmod(descriptor_path, 0o440)
            status = "staged"

    evidence = {
        "schema_version": "liqi.deployment.installed-release/v1",
        "release_id": release_id,
        "git_sha": manifest["git_sha"],
        "release_path": str(release_path),
        "manifest_sha256": sha256(args.manifest),
        "artifact_sha256": sha256(artifact),
        "native_artifacts": native_results,
        "descriptor_sha256": hashlib.sha256(descriptor_bytes).hexdigest(),
        "status": status,
        "approval_reference": args.approval_reference if args.execute else None,
        "installed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "mutation_performed": args.execute,
    }
    output_failures = schema_errors("installed-release-v1.schema.json", evidence)
    if output_failures:
        raise RuntimeError("invalid installation evidence: " + "; ".join(output_failures))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(evidence, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
