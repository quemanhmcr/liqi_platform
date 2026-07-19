#!/usr/bin/env python3
"""Verify one portable, atomic Linux release build-result publication."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[2]
BUILD_RESULT_SCHEMA = ROOT / "contracts/runtime/linux-release-build-result-v1.schema.json"
MIX_SCHEMA = ROOT / "contracts/deployment/mix-release-v1.schema.json"
RUNTIME_RESULT_SCHEMA = ROOT / "contracts/runtime/runtime-artifact-result-v1.schema.json"
NATIVE_SCHEMA = ROOT / "contracts/native/native-artifact-v1.schema.json"
NATIVE_DEPLOYMENT_SCHEMA = ROOT / "contracts/deployment/native-artifact-v1.schema.json"
NATIVE_HANDOFF_VERIFIER = ROOT / "native/scripts/verify_deployment_manifest.py"
SHA = re.compile(r"^[0-9a-f]{40}$")
KEY_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{2,63}$")
TARGETS = ("aarch64-unknown-linux-gnu", "x86_64-unknown-linux-gnu")


def load(path: Path) -> dict[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError(f"JSON root must be an object: {path.name}")
    return document


def validate(schema_path: Path, document: dict[str, Any], label: str) -> None:
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    errors = sorted(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(document),
        key=lambda item: list(item.absolute_path),
    )
    if errors:
        error = errors[0]
        location = ".".join(map(str, error.absolute_path)) or "$"
        raise ValueError(f"{label} invalid at {location}: {error.message}")


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def run(argv: list[str], timeout: int = 600) -> str:
    completed = subprocess.run(
        argv,
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=timeout,
    )
    if completed.returncode:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise ValueError(detail[-4096:] or f"command failed: {argv[0]}")
    return completed.stdout.strip()


def trust_root(path: Path, label: str) -> Path:
    if not path.is_dir() or path.is_symlink():
        raise FileNotFoundError(f"{label} trust directory is missing or is a symlink")
    return path.resolve()


def public_key_fingerprint(key: Path) -> str:
    completed = subprocess.run(
        ["openssl", "pkey", "-pubin", "-in", str(key), "-outform", "DER"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=60,
    )
    if completed.returncode:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        raise ValueError(detail or "cannot canonicalize public key")
    return hashlib.sha256(completed.stdout).hexdigest()


def require_distinct_public_keys(keys: list[tuple[str, Path]]) -> None:
    fingerprints: dict[str, str] = {}
    for label, key in keys:
        fingerprint = public_key_fingerprint(key)
        if fingerprint in fingerprints:
            raise ValueError(f"public key material is reused by {fingerprints[fingerprint]} and {label}")
        fingerprints[fingerprint] = label


def trusted_key(trust_dir: Path, key_id: str, label: str) -> Path:
    if not KEY_ID.fullmatch(key_id):
        raise ValueError(f"{label} key ID is invalid")
    root = trust_root(trust_dir, label)
    candidate = root / f"{key_id}.pem"
    if candidate.is_symlink():
        raise FileNotFoundError(f"{label} public key is missing or is a symlink")
    key = candidate.resolve()
    try:
        key.relative_to(root)
    except ValueError as error:
        raise ValueError(f"{label} key escapes trust directory") from error
    if not key.is_file() or key.is_symlink():
        raise FileNotFoundError(f"{label} public key is missing or is a symlink")
    return key


def verify_ed25519(payload: Path, signature: Path, key: Path) -> None:
    run([
        "openssl", "pkeyutl", "-verify", "-rawin", "-pubin",
        "-inkey", str(key), "-in", str(payload), "-sigfile", str(signature),
    ], timeout=60)


def evidence_file(base: Path, record: dict[str, Any], label: str) -> Path:
    filename = str(record.get("filename", ""))
    relative = PurePosixPath(filename)
    if relative.is_absolute() or len(relative.parts) != 1 or ".." in relative.parts:
        raise ValueError(f"{label} filename is not portable: {filename}")
    path = (base / filename).resolve()
    try:
        path.relative_to(base.resolve())
    except ValueError as error:
        raise ValueError(f"{label} escapes publication directory") from error
    if not path.is_file() or path.is_symlink():
        raise FileNotFoundError(f"{label} is missing, non-regular, or a symlink: {filename}")
    actual = digest(path)
    if actual != record.get("sha256"):
        raise ValueError(f"{label} SHA256 mismatch")
    return path


def verify(result_path: Path, expected_sha: str, expected_target: str, release_trust_dir: Path, native_trust_dir: Path) -> dict[str, Any]:
    result_path = result_path.resolve()
    if not result_path.is_file() or result_path.is_symlink():
        raise FileNotFoundError("build result must be a regular non-symlink file")
    result = load(result_path)
    validate(BUILD_RESULT_SCHEMA, result, "Linux release build result")
    if result["git_sha"] != expected_sha or result["target_triple"] != expected_target:
        raise ValueError("build result Git SHA or target mismatch")
    if result["status"] != "passed" or result["build_on_host"] is not False:
        raise ValueError("build result is not a passed off-host build")

    base = result_path.parent
    manifest_path = evidence_file(base, result["manifest"], "Mix manifest")
    artifact_path = evidence_file(base, result["artifact"], "Mix archive")
    artifact_signature = evidence_file(base, result["artifact_signature"], "Mix archive signature")
    manifest_signature = evidence_file(base, result["manifest_signature"], "Mix manifest signature")
    runtime_result_path = evidence_file(base, result["runtime_artifact_result"], "runtime artifact result")
    native_provider_path = evidence_file(base, result["native_provider_manifest"], "native provider manifest")
    native_deployment_path = evidence_file(base, result["native_deployment_manifest"], "native deployment manifest")

    if artifact_path.stat().st_size != result["artifact"]["size_bytes"]:
        raise ValueError("Mix archive size mismatch")

    manifest = load(manifest_path)
    validate(MIX_SCHEMA, manifest, "Mix release manifest")
    if (
        manifest["release_id"] != result["release_id"]
        or manifest["git_sha"] != expected_sha
        or manifest["target_triple"] != expected_target
    ):
        raise ValueError("Mix manifest identity mismatch")
    artifact_contract = manifest["artifact"]
    if (
        artifact_contract["filename"] != artifact_path.name
        or artifact_contract["sha256"] != result["artifact"]["sha256"]
        or artifact_contract["size_bytes"] != result["artifact"]["size_bytes"]
        or artifact_contract["signature"]["signature_filename"] != artifact_signature.name
        or artifact_contract["signature"]["signature_sha256"] != result["artifact_signature"]["sha256"]
        or manifest["manifest_signature"]["signature_filename"] != manifest_signature.name
    ):
        raise ValueError("Mix manifest artifact/signature binding mismatch")

    artifact_key_id = artifact_contract["signature"]["key_id"]
    manifest_key_id = manifest["manifest_signature"]["key_id"]
    if artifact_key_id == manifest_key_id:
        raise ValueError("release artifact and manifest key IDs must be distinct")
    artifact_key = trusted_key(release_trust_dir, artifact_key_id, "release artifact")
    manifest_key = trusted_key(release_trust_dir, manifest_key_id, "release manifest")
    verify_ed25519(artifact_path, artifact_signature, artifact_key)
    verify_ed25519(manifest_path, manifest_signature, manifest_key)

    runtime_result = load(runtime_result_path)
    validate(RUNTIME_RESULT_SCHEMA, runtime_result, "runtime artifact result")
    if (
        runtime_result["status"] != "passed"
        or runtime_result["blockers"]
        or runtime_result["git_sha"] != expected_sha
        or runtime_result["release_id"] != result["release_id"]
        or runtime_result["artifact_sha256"] != result["artifact"]["sha256"]
    ):
        raise ValueError("runtime artifact result identity mismatch")

    native_provider = load(native_provider_path)
    native_deployment = load(native_deployment_path)
    validate(NATIVE_SCHEMA, native_provider, "native provider manifest")
    validate(NATIVE_DEPLOYMENT_SCHEMA, native_deployment, "native deployment manifest")
    if (
        native_provider["source_revision"] != expected_sha
        or native_deployment["git_sha"] != expected_sha
        or native_provider["target_triple"] != expected_target
        or native_deployment["target_triple"] != expected_target
        or native_provider["artifact_sha256"] != native_deployment["artifact"]["sha256"]
    ):
        raise ValueError("native provider/deployment identity mismatch")
    references = {item["manifest_filename"]: item for item in manifest["native_artifacts"]}
    reference = references.get(native_deployment_path.name)
    if not reference or reference["manifest_sha256"] != result["native_deployment_manifest"]["sha256"] or reference["required"] is not True:
        raise ValueError("Mix manifest does not require the exact native deployment handoff")

    native_key = trusted_key(
        native_trust_dir,
        native_deployment["artifact"]["signature"]["key_id"],
        "native deployment",
    )
    require_distinct_public_keys([
        ("release artifact", artifact_key),
        ("release manifest", manifest_key),
        ("native deployment", native_key),
    ])
    run([
        sys.executable,
        str(NATIVE_HANDOFF_VERIFIER),
        "--native-manifest", str(native_provider_path),
        "--deployment-manifest", str(native_deployment_path),
        "--trust-dir", str(trust_root(native_trust_dir, "native deployment")),
    ], timeout=600)

    return {
        "validation": "liqi.runtime.linux-release-build-result/v1",
        "status": "passed",
        "release_id": result["release_id"],
        "git_sha": expected_sha,
        "target_triple": expected_target,
        "build_result_sha256": digest(result_path),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--result", required=True, type=Path)
    parser.add_argument("--git-sha", required=True)
    parser.add_argument("--target-triple", required=True, choices=TARGETS)
    parser.add_argument("--release-trust-dir", required=True, type=Path)
    parser.add_argument("--native-trust-dir", required=True, type=Path)
    args = parser.parse_args()
    if not SHA.fullmatch(args.git_sha):
        parser.error("--git-sha must be an exact lowercase 40-character Git SHA")
    try:
        output = verify(args.result, args.git_sha, args.target_triple, args.release_trust_dir, args.native_trust_dir)
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as error:
        print(f"Linux release build result verification failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps(output, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
