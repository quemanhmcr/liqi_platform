#!/usr/bin/env python3
"""Authorize exact Senior 3 native bytes for offline host installation."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[2]
KEY_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{2,63}$")


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def validate(schema: Path, document: Any, label: str) -> None:
    validator = Draft202012Validator(load(schema), format_checker=FormatChecker())
    failures = sorted(validator.iter_errors(document), key=lambda item: list(item.absolute_path))
    if failures:
        failure = failures[0]
        location = ".".join(map(str, failure.absolute_path)) or "$"
        raise RuntimeError(f"{label} invalid at {location}: {failure.message}")


def resolve_child(base: Path, relative: str) -> Path:
    path = PurePosixPath(relative)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise RuntimeError(f"unsafe native path: {relative}")
    candidate = base.joinpath(*path.parts).resolve()
    try:
        candidate.relative_to(base.resolve())
    except ValueError as error:
        raise RuntimeError(f"native path escapes manifest directory: {relative}") from error
    return candidate


def run(argv: list[str], timeout: int = 300) -> str:
    result = subprocess.run(argv, cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, timeout=timeout)
    if result.returncode:
        raise RuntimeError((result.stderr or result.stdout or f"command failed: {argv}")[-4096:])
    return result.stdout.strip()


def sign_artifact(artifact: Path, private_key: Path, signature: Path) -> None:
    result = subprocess.run(
        ["openssl", "pkeyutl", "-sign", "-rawin", "-inkey", str(private_key), "-in", str(artifact), "-out", str(signature)],
        text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, timeout=60,
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "native Ed25519 authorization failed")
    os.chmod(signature, 0o640)


def derive_public(private_key: Path, key_id: str, trust_dir: Path) -> None:
    public = trust_dir / f"{key_id}.pem"
    result = subprocess.run(
        ["openssl", "pkey", "-in", str(private_key), "-pubout", "-out", str(public)],
        text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, timeout=30,
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "cannot derive native deployment public key")
    os.chmod(public, 0o600)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--native-manifest", required=True, type=Path)
    parser.add_argument("--deployment-key-id", required=True)
    parser.add_argument("--deployment-signing-key", required=True, type=Path)
    parser.add_argument("--authorization-reference", required=True)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--output-result", required=True, type=Path)
    args = parser.parse_args()

    if not KEY_ID.fullmatch(args.deployment_key_id):
        raise SystemExit("deployment key ID is invalid")
    if not args.deployment_signing_key.is_file():
        raise SystemExit("deployment signing key is missing")
    if not 3 <= len(args.authorization_reference.strip()) <= 256:
        raise SystemExit("authorization reference is invalid")

    manifest_path = args.native_manifest.resolve()
    provider = load(manifest_path)
    validate(ROOT / "contracts/native/native-artifact-v1.schema.json", provider, "native provider manifest")
    run([sys.executable, str(ROOT / "native/scripts/verify_artifact.py"), "--manifest", str(manifest_path)], 600)
    artifact = resolve_child(manifest_path.parent, provider["artifact_path"])
    if not artifact.is_file() or artifact.stat().st_size != provider["artifact_size_bytes"] or digest(artifact) != provider["artifact_sha256"]:
        raise RuntimeError("native artifact bytes do not match provider identity")

    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    signature = output_dir / f"{artifact.name}.sig"
    deployment_manifest = output_dir / f"native-deployment-{provider['release_id']}.json"
    sign_artifact(artifact, args.deployment_signing_key.resolve(), signature)

    with tempfile.TemporaryDirectory(prefix="liqi-native-authorize-trust-") as directory:
        trust_dir = Path(directory)
        derive_public(args.deployment_signing_key.resolve(), args.deployment_key_id, trust_dir)
        run([
            sys.executable,
            str(ROOT / "native/scripts/prepare_deployment_manifest.py"),
            "--native-manifest", str(manifest_path),
            "--ed25519-signature", str(signature),
            "--key-id", args.deployment_key_id,
            "--trust-dir", str(trust_dir),
            "--output", str(deployment_manifest),
        ], 600)
        run([
            sys.executable,
            str(ROOT / "native/scripts/verify_deployment_manifest.py"),
            "--native-manifest", str(manifest_path),
            "--deployment-manifest", str(deployment_manifest),
            "--trust-dir", str(trust_dir),
        ], 600)

    deployment = load(deployment_manifest)
    validate(ROOT / "contracts/deployment/native-artifact-v1.schema.json", deployment, "native deployment manifest")
    if deployment["artifact"]["sha256"] != provider["artifact_sha256"] or deployment["git_sha"] != provider["source_revision"]:
        raise RuntimeError("native deployment authorization does not bind the provider artifact")

    result = {
        "schema_version": "liqi.deployment.native-authorization-result/v1",
        "status": "authorized",
        "git_sha": provider["source_revision"],
        "release_id": provider["release_id"],
        "artifact_sha256": provider["artifact_sha256"],
        "provider_manifest": {"filename": manifest_path.name, "sha256": digest(manifest_path)},
        "deployment_manifest": {"filename": deployment_manifest.name, "sha256": digest(deployment_manifest)},
        "signature": {"filename": signature.name, "sha256": digest(signature)},
        "key_id": args.deployment_key_id,
        "authorization_reference": args.authorization_reference.strip(),
        "observed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "artifact_bytes_changed": False,
        "oci_mutation_performed": False,
    }
    validate(ROOT / "contracts/deployment/native-authorization-result-v1.schema.json", result, "native authorization result")
    args.output_result.parent.mkdir(parents=True, exist_ok=True)
    args.output_result.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
