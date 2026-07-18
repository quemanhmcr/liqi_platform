#!/usr/bin/env python3
"""Verify the complete native provider-to-deployment artifact handoff."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[2]
NATIVE_SCHEMA = ROOT / "contracts" / "native" / "native-artifact-v1.schema.json"
DEPLOYMENT_SCHEMA = ROOT / "contracts" / "deployment" / "native-artifact-v1.schema.json"
PROVIDER_VERIFIER = ROOT / "native" / "scripts" / "verify_artifact.py"
EXPECTED_INSTALL_PATH = "lib/liqi_native-1.0.0/priv/native/libliqi_sequence_diff_nif.so"


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def validate(schema_path: Path, document: Any, label: str) -> list[str]:
    schema = load(schema_path)
    return [
        f"{label}.{'.'.join(map(str, error.absolute_path)) or '$'}: {error.message}"
        for error in sorted(
            Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(document),
            key=lambda item: list(item.absolute_path),
        )
    ]


def binding_failures(native: dict[str, Any], deployment: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    comparisons = (
        ("source revision", native.get("source_revision"), deployment.get("git_sha")),
        ("crate", native.get("crate"), deployment.get("crate")),
        ("target triple", native.get("target_triple"), deployment.get("target_triple")),
        ("NIF ABI", native.get("nif_abi"), deployment.get("nif_abi")),
        ("Rustler version", native.get("rustler_version"), deployment.get("rustler_version")),
        ("artifact filename", Path(str(native.get("artifact_path", ""))).name, deployment.get("artifact", {}).get("filename")),
        ("artifact SHA256", native.get("artifact_sha256"), deployment.get("artifact", {}).get("sha256")),
        ("artifact size", native.get("artifact_size_bytes"), deployment.get("artifact", {}).get("size_bytes")),
    )
    for label, provider_value, deployment_value in comparisons:
        if provider_value != deployment_value:
            failures.append(f"{label} differs between provider and deployment manifests")

    artifact = deployment.get("artifact", {})
    load_contract = deployment.get("load", {})
    safety = deployment.get("safety", {})
    if artifact.get("install_relative_path") != EXPECTED_INSTALL_PATH:
        failures.append("deployment install path does not match the Rustler OTP priv path")
    probe = load_contract.get("probe_command", [])
    if probe[:2] != ["bin/liqi_platform", "eval"] or "rpc" in probe:
        failures.append("native load probe must use distribution-free release eval")
    if load_contract.get("expected_module") != "Liqi.Native.SequenceDiff.Nif":
        failures.append("deployment expected module is not the provider NIF module")
    if load_contract.get("feature_flag") != "LIQI_NATIVE_MODE":
        failures.append("deployment feature flag differs from runtime configuration")
    if load_contract.get("fallback_module") != "Liqi.Native.Reference.SequenceDiff":
        failures.append("deployment fallback module differs from the provider reference")

    expected_safety = {
        "scheduler_class": "regular",
        "hard_execution_budget_us": 1000,
        "native_memory_limit_bytes": 65536,
        "concurrency_limit": 2,
        "panic_mapping_tested": True,
        "differential_tests_passed": True,
        "fuzz_tests_passed": True,
    }
    for name, expected in expected_safety.items():
        if safety.get(name) != expected:
            failures.append(f"deployment safety field {name} must equal {expected!r}")
    return failures


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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--native-manifest", required=True, type=Path)
    parser.add_argument("--deployment-manifest", required=True, type=Path)
    parser.add_argument("--trust-dir", required=True, type=Path)
    args = parser.parse_args()

    native_path = args.native_manifest.resolve()
    deployment_path = args.deployment_manifest.resolve()
    native = load(native_path)
    deployment = load(deployment_path)
    failures = validate(NATIVE_SCHEMA, native, "native-manifest")
    failures.extend(validate(DEPLOYMENT_SCHEMA, deployment, "deployment-manifest"))
    failures.extend(binding_failures(native, deployment))
    if failures:
        raise ValueError("; ".join(failures))

    subprocess.run(
        [sys.executable, str(PROVIDER_VERIFIER), "--manifest", str(native_path)],
        cwd=ROOT,
        check=True,
        timeout=300,
    )

    artifact = native_path.parent / native["artifact_path"]
    signature = deployment_path.parent / deployment["artifact"]["signature"]["signature_filename"]
    key_id = deployment["artifact"]["signature"]["key_id"]
    public_key = args.trust_dir.resolve() / f"{key_id}.pem"
    for path in (artifact, signature, public_key):
        if not path.is_file():
            raise FileNotFoundError(path)
    verify_ed25519(artifact, signature, public_key)

    print(
        json.dumps(
            {
                "validation": "native-deployment-handoff-v1",
                "status": "passed",
                "artifact_id": deployment["artifact_id"],
                "git_sha": native["source_revision"],
                "artifact_sha256": native["artifact_sha256"],
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
        print(f"native deployment handoff verification failed: {error}", file=sys.stderr)
        raise SystemExit(1) from error
