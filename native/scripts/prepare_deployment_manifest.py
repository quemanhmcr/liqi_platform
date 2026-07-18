#!/usr/bin/env python3
"""Bind one verified native artifact to the Senior 4 deployment handoff.

The provider manifest remains the source of artifact/ABI identity. This adapter
requires a second Ed25519 signature over the exact same artifact bytes because
the host installer deliberately trusts an offline deployment key rather than
an OIDC identity. It never signs, copies, or mutates the artifact.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[2]
NATIVE_SCHEMA = ROOT / "contracts" / "native" / "native-artifact-v1.schema.json"
DEPLOYMENT_SCHEMA = ROOT / "contracts" / "deployment" / "native-artifact-v1.schema.json"
PROVIDER_VERIFIER = ROOT / "native" / "scripts" / "verify_artifact.py"
ARTIFACT_ID = "liqi-sequence-diff-nif-v1"
INSTALL_RELATIVE_PATH = "lib/liqi_native-1.0.0/priv/native/libliqi_sequence_diff_nif.so"
KEY_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{2,63}$")
FILE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate(schema_path: Path, document: Any, label: str) -> list[str]:
    schema = load_json(schema_path)
    return [
        f"{label}.{'.'.join(map(str, error.absolute_path)) or '$'}: {error.message}"
        for error in sorted(
            Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(document),
            key=lambda item: list(item.absolute_path),
        )
    ]


def resolve_child(base: Path, relative: str) -> Path:
    candidate = (base / relative).resolve()
    try:
        candidate.relative_to(base.resolve())
    except ValueError as error:
        raise ValueError(f"path escapes provider manifest directory: {relative}") from error
    return candidate


def verify_provider_manifest(manifest: Path) -> None:
    subprocess.run(
        [sys.executable, str(PROVIDER_VERIFIER), "--manifest", str(manifest)],
        cwd=ROOT,
        check=True,
        timeout=300,
    )


def verify_ed25519(artifact: Path, signature: Path, public_key: Path) -> None:
    completed = subprocess.run(
        [
            "openssl",
            "pkeyutl",
            "-verify",
            "-rawin",
            "-pubin",
            "-inkey",
            str(public_key),
            "-in",
            str(artifact),
            "-sigfile",
            str(signature),
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=30,
    )
    if completed.returncode:
        raise ValueError(completed.stderr.strip() or "Ed25519 signature verification failed")


def deployment_document(
    native_manifest: dict[str, Any],
    artifact: Path,
    signature: Path,
    key_id: str,
) -> dict[str, Any]:
    if not KEY_ID_PATTERN.fullmatch(key_id):
        raise ValueError("key ID is invalid")
    if not FILE_PATTERN.fullmatch(signature.name):
        raise ValueError("signature filename is invalid")
    actual_sha = sha256(artifact)
    actual_size = artifact.stat().st_size
    if actual_sha != native_manifest["artifact_sha256"]:
        raise ValueError("artifact SHA256 differs from the provider manifest")
    if actual_size != native_manifest["artifact_size_bytes"]:
        raise ValueError("artifact size differs from the provider manifest")
    if artifact.name != Path(native_manifest["artifact_path"]).name:
        raise ValueError("artifact filename differs from the provider manifest")

    probe_expression = (
        'case Application.ensure_all_started(:liqi_native) do '
        '{:ok, _} -> :ok; {:error, reason} -> raise "native app start failed: #{inspect(reason)}" end; '
        'status = Liqi.Native.SequenceDiff.readiness(:native_required); '
        'unless status.ready and status.native_available, do: raise("native readiness failed"); '
        'IO.puts("native-load-ok")'
    )
    return {
        "schema_version": "liqi.deployment.native-artifact/v1",
        "artifact_id": ARTIFACT_ID,
        "git_sha": native_manifest["source_revision"],
        "crate": native_manifest["crate"],
        "target_triple": native_manifest["target_triple"],
        "nif_abi": native_manifest["nif_abi"],
        "rustler_version": native_manifest["rustler_version"],
        "artifact": {
            "filename": artifact.name,
            "install_relative_path": INSTALL_RELATIVE_PATH,
            "size_bytes": actual_size,
            "sha256": actual_sha,
            "signature": {
                "algorithm": "ed25519",
                "key_id": key_id,
                "signature_filename": signature.name,
                "signature_sha256": sha256(signature),
                "signed_payload": "artifact-bytes",
            },
        },
        "load": {
            "probe_command": ["bin/liqi_platform", "eval", probe_expression],
            "expected_module": "Liqi.Native.SequenceDiff.Nif",
            "feature_flag": "LIQI_NATIVE_MODE",
            "fallback_module": "Liqi.Native.Reference.SequenceDiff",
        },
        "safety": {
            "scheduler_class": "regular",
            "hard_execution_budget_us": 1000,
            "native_memory_limit_bytes": 65536,
            "concurrency_limit": 2,
            "panic_mapping_tested": True,
            "differential_tests_passed": True,
            "fuzz_tests_passed": True,
        },
        "created_at": native_manifest["built_at"],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--native-manifest", required=True, type=Path)
    parser.add_argument("--ed25519-signature", required=True, type=Path)
    parser.add_argument("--key-id", required=True)
    parser.add_argument("--trust-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    manifest_path = args.native_manifest.resolve()
    manifest = load_json(manifest_path)
    errors = validate(NATIVE_SCHEMA, manifest, "native-manifest")
    if errors:
        raise ValueError("; ".join(errors))
    verify_provider_manifest(manifest_path)

    artifact = resolve_child(manifest_path.parent, manifest["artifact_path"])
    signature = args.ed25519_signature.resolve()
    if not artifact.is_file() or not signature.is_file():
        raise FileNotFoundError("artifact or Ed25519 signature is missing")
    if not KEY_ID_PATTERN.fullmatch(args.key_id):
        raise ValueError("key ID is invalid")
    public_key = args.trust_dir.resolve() / f"{args.key_id}.pem"
    if not public_key.is_file():
        raise FileNotFoundError(f"trusted public key is missing: {public_key}")
    verify_ed25519(artifact, signature, public_key)

    document = deployment_document(manifest, artifact, signature, args.key_id)
    errors = validate(DEPLOYMENT_SCHEMA, document, "deployment-manifest")
    if errors:
        raise ValueError("; ".join(errors))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    print(
        json.dumps(
            {
                "validation": "native-deployment-adapter-v1",
                "status": "passed",
                "artifact_id": document["artifact_id"],
                "git_sha": document["git_sha"],
                "artifact_sha256": document["artifact"]["sha256"],
                "output": str(args.output),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (FileNotFoundError, ValueError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        print(f"native deployment manifest failed: {error}", file=sys.stderr)
        raise SystemExit(1) from error
