#!/usr/bin/env python3
"""Validate Senior 3 native contracts and fail-closed source invariants."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
CONTRACT_DIR = ROOT / "contracts" / "native"
EXAMPLE_DIR = CONTRACT_DIR / "examples"

EXPECTED = {
    "max_input_bytes": 16_384,
    "max_observed_sequences": 2_048,
    "max_window_span": 65_536,
    "max_output_ranges": 2_049,
    "max_native_output_bytes": 32_784,
    "max_concurrency": 2,
    "rust_toolchain": "1.97.1",
    "rustler_version": "0.38.0",
    "nif_abi": "2.15",
}


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def fail_location(error: Exception) -> str:
    path = ".".join(str(part) for part in getattr(error, "absolute_path", ()))
    return path or "$"


def require_text(path: Path, patterns: dict[str, str]) -> list[str]:
    if not path.is_file():
        return [f"missing required source file: {path.relative_to(ROOT)}"]
    content = path.read_text(encoding="utf-8")
    failures = []
    for label, pattern in patterns.items():
        if re.search(pattern, content, flags=re.MULTILINE) is None:
            failures.append(f"{path.relative_to(ROOT)} missing invariant {label}")
    return failures


def reject_text(path: Path, patterns: dict[str, str]) -> list[str]:
    if not path.is_file():
        return [f"missing required source file: {path.relative_to(ROOT)}"]
    content = path.read_text(encoding="utf-8")
    failures = []
    for label, pattern in patterns.items():
        if re.search(pattern, content, flags=re.MULTILINE):
            failures.append(f"{path.relative_to(ROOT)} violates invariant {label}")
    return failures


def validate_semantics() -> list[str]:
    failures: list[str] = []
    capabilities = load_json(CONTRACT_DIR / "native-capabilities-v1.json")
    kernel = load_json(CONTRACT_DIR / "compact-sequence-diff-v1.json")

    if capabilities.get("status") != "source-ready":
        failures.append("native capabilities must remain source-ready until a signed ARM64 artifact is bound")
    if capabilities.get("source_revision") is not None:
        failures.append("uncommitted source capability source_revision must be null")
    binding = capabilities.get("artifact_binding", {})
    if binding.get("status") != "pending":
        failures.append("source-ready capabilities cannot claim an artifact binding")

    declared = capabilities.get("capabilities", [{}])[0]
    bounds = kernel.get("bounds", {})
    scheduler = kernel.get("scheduler", {})
    latency = kernel.get("latency", {})
    fallback = kernel.get("fallback", {})
    security = kernel.get("security_review", {})

    checks = {
        "max_input_bytes": bounds.get("max_input_bytes"),
        "max_observed_sequences": bounds.get("max_observed_sequences"),
        "max_window_span": bounds.get("max_window_span"),
        "max_output_ranges": bounds.get("max_output_ranges"),
        "max_native_output_bytes": bounds.get("max_native_output_bytes"),
        "max_concurrency": scheduler.get("max_concurrency"),
        "rust_toolchain": capabilities.get("rust_toolchain"),
        "rustler_version": capabilities.get("rustler_version"),
        "nif_abi": capabilities.get("nif_abi"),
    }
    for name, expected in EXPECTED.items():
        if checks.get(name) != expected:
            failures.append(f"contract invariant {name} must equal {expected!r}, got {checks.get(name)!r}")

    if declared.get("max_input_bytes") != bounds.get("max_input_bytes"):
        failures.append("capability and kernel max_input_bytes declarations differ")
    if declared.get("max_output_ranges") != bounds.get("max_output_ranges"):
        failures.append("capability and kernel max_output_ranges declarations differ")
    if declared.get("max_concurrency") != scheduler.get("max_concurrency"):
        failures.append("capability and kernel max_concurrency declarations differ")
    if scheduler.get("class") != "regular" or scheduler.get("blocking_io") is not False:
        failures.append("compact sequence diff must remain a non-blocking regular NIF")
    if scheduler.get("queue_capacity") != 0:
        failures.append("regular NIF provider must not introduce an internal queue")
    if latency.get("enforcement") != "blocked-pending-a1":
        failures.append("latency enforcement cannot be promoted before direct OCI A1 evidence")
    if fallback.get("feature_default") != "disabled" or fallback.get("always_deployable") is not True:
        failures.append("optional native capability must ship disabled with an always-deployable fallback")
    if any(value != "forbidden" for key, value in security.items() if key != "sensitive_payload"):
        failures.append("security review must forbid unsafe code, I/O, threads, and payload logging")

    failures.extend(
        require_text(
            ROOT / "Cargo.toml",
            {
                "pinned Rustler 0.38.0": r'rustler\s*=\s*\{[^\n]*version\s*=\s*"=0\.38\.0"',
                "NIF ABI 2.15 feature": r'nif_version_2_15',
                "NIF unwind profile": r'\[profile\.nif-release\][\s\S]*?panic\s*=\s*"unwind"',
                "V0 release abort profile retained": r'\[profile\.release\][\s\S]*?panic\s*=\s*"abort"',
            },
        )
    )
    failures.extend(
        require_text(
            ROOT / "native" / "sequence-diff-core" / "src" / "lib.rs",
            {
                "unsafe forbidden": r'#!\[forbid\(unsafe_code\)\]',
                "input cap": r'MAX_OBSERVED_SEQUENCES:\s*usize\s*=\s*2_048',
                "window cap": r'MAX_WINDOW_SPAN:\s*u64\s*=\s*65_536',
                "bounded capacity": r'Vec::with_capacity\(observed_count\.saturating_add\(1\)\)',
            },
        )
    )
    nif_source = ROOT / "native" / "sequence-diff-nif" / "src" / "lib.rs"
    failures.extend(
        require_text(
            nif_source,
            {
                "regular scheduler annotation": r'schedule\s*=\s*"Normal"',
                "panic guard": r'catch_unwind',
                "stable panic code": r'NATIVE_PANIC',
                "capability negotiation function": r'fn kernel_info_v1\(',
                "Elixir module binding": r'Elixir\.Liqi\.Native\.SequenceDiff\.Nif',
            },
        )
    )
    failures.extend(
        reject_text(
            nif_source,
            {
                "filesystem I/O": r'\bstd::fs\b',
                "network I/O": r'\bstd::net\b',
                "unmanaged thread": r'\bstd::thread\b',
                "database dependency": r'\b(sqlx|postgres|tokio_postgres)\b',
                "unsafe block": r'\bunsafe\s*\{',
            },
        )
    )
    failures.extend(
        require_text(
            ROOT / "native" / "elixir" / "lib" / "liqi" / "native" / "sequence_diff.ex",
            {
                "reference-only mode": r':reference',
                "optional fallback": r':native_preferred',
                "required fail-closed mode": r':native_required',
                "version negotiation": r'NATIVE_VERSION_MISMATCH',
                "execution metadata": r'fallback_reason',
            },
        )
    )
    failures.extend(
        require_text(
            ROOT / "native" / "elixir" / "lib" / "liqi" / "native" / "sequence_diff" / "nif.ex",
            {
                "precompiled artifact loading": r'skip_compilation\?:\s*true',
                "release artifact path": r'priv/native/libliqi_sequence_diff_nif',
            },
        )
    )
    failures.extend(
        require_text(
            ROOT / "native" / "elixir" / "mix.exs",
            {
                "provider OTP application": r'app:\s*:liqi_native',
                "Rustler Mix dependency": r'\{:rustler,\s*"~> 0\.38\.0",\s*runtime:\s*false\}',
            },
        )
    )
    failures.extend(
        require_text(
            ROOT / "native" / "scripts" / "prepare_deployment_manifest.py",
            {
                "single artifact identity": r'artifact SHA256 differs from the provider manifest',
                "deployment Ed25519 verification": r'openssl.*pkeyutl|pkeyutl',
                "distribution-free load probe": r'"bin/liqi_platform", "eval"',
                "release install path": r'lib/liqi_native-1\.0\.0/priv/native/libliqi_sequence_diff_nif\.so',
            },
        )
    )
    failures.extend(
        reject_text(
            ROOT / "native" / "scripts" / "prepare_deployment_manifest.py",
            {"distribution RPC": r'"rpc"'},
        )
    )
    failures.extend(
        require_text(
            ROOT / "native" / "scripts" / "build-linux-artifact.sh",
            {
                "reviewed ARM64 target": r'aarch64-unknown-linux-gnu.*ELF_MACHINE=183',
                "reviewed x86 target": r'x86_64-unknown-linux-gnu.*ELF_MACHINE=62',
                "exact clean source": r'git status --porcelain --untracked-files=all',
                "bounded build jobs": r'BUILD_JOBS < 1 \|\| BUILD_JOBS > 2',
            },
        )
    )
    failures.extend(
        require_text(
            ROOT / "native" / "fuzz" / "run-fuzz.sh",
            {
                "bounded exact nightly selection": r'LIQI_FUZZ_TOOLCHAIN.*nightly-YYYY-MM-DD',
                "selected cargo fuzz toolchain": r'cargo \+"\$FUZZ_TOOLCHAIN" fuzz run',
                "ignored bounded corpus": r'CORPUS_DIR="\$ROOT_DIR/\.artifacts/native-fuzz/corpus/sequence_diff_parity"',
            },
        )
    )
    for required_path in (
        ROOT / "native" / "scripts" / "build-linux-artifact.sh",
        ROOT / "native" / "scripts" / "build-arm64-artifact.sh",
        ROOT / "native" / "scripts" / "build-x86_64-artifact.sh",
        ROOT / "native" / "tests" / "test_artifact_architecture.py",
        ROOT / "native" / "fuzz" / "fuzz_targets" / "sequence_diff_parity.rs",
        ROOT / "native" / "sequence-diff-core" / "tests" / "differential.rs",
        ROOT / "native" / "elixir" / "test" / "run-reference-tests.exs",
        ROOT / "native" / "elixir" / "lib" / "liqi" / "native" / "benchmark" / "sequence_diff.ex",
        ROOT / "native" / "bench" / "run-a1-nif-benchmark.exs",
        ROOT / "native" / "bench" / "validate_benchmark.py",
        ROOT / "native" / "scripts" / "run-v1-safety-gates.sh",
        ROOT / "native" / "scripts" / "run_v1_safety_gates.py",
        ROOT / "native" / "scripts" / "prepare_deployment_manifest.py",
        ROOT / "native" / "scripts" / "verify_deployment_manifest.py",
        ROOT / "native" / "tests" / "test_deployment_manifest.py",
        ROOT / "native" / "tests" / "test_safety_gate.py",
        ROOT / "contracts" / "native" / "native-safety-result-v1.schema.json",
    ):
        if not required_path.is_file():
            failures.append(f"missing safety evidence provider: {required_path.relative_to(ROOT)}")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    failures: list[str] = []
    contract_command = [sys.executable, str(CONTRACT_DIR / "validate_contracts.py")]
    if args.quiet:
        contract_command.append("--quiet")
    contract_result = subprocess.run(contract_command, check=False)
    if contract_result.returncode != 0:
        failures.append("native contract validation failed")
    failures.extend(validate_semantics())

    if failures:
        for failure in failures:
            print(f"FAIL native-source: {failure}", file=sys.stderr)
        return 1
    if not args.quiet:
        print(
            json.dumps(
                {
                    "validation": "native-source-v1",
                    "status": "passed",
                    "schemas": 7,
                    "live_documents": 2,
                    "artifact_state": "pending",
                    "live_a1_state": "pending",
                },
                separators=(",", ":"),
                sort_keys=True,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
