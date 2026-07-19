#!/usr/bin/env python3
"""Verify a native artifact manifest, checksums, provenance, SBOM, ELF ABI, and Sigstore bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
import subprocess
import sys
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = ROOT / "contracts" / "native" / "native-artifact-v1.schema.json"
TARGETS = {
    "aarch64-unknown-linux-gnu": {"architecture": "aarch64", "elf_machine": 183, "elf_name": "AArch64"},
    "x86_64-unknown-linux-gnu": {"architecture": "x86_64", "elf_machine": 62, "elf_name": "x86-64"},
}


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def resolve_child(base: Path, relative: str) -> Path:
    candidate = (base / relative).resolve()
    try:
        candidate.relative_to(base.resolve())
    except ValueError as error:
        raise ValueError(f"artifact path escapes manifest directory: {relative}") from error
    return candidate


def verify_elf(path: Path, target_triple: str) -> None:
    expected = TARGETS[target_triple]
    header = path.read_bytes()[:20]
    if len(header) < 20 or header[:4] != b"\x7fELF" or header[4] != 2 or header[5] != 1:
        raise ValueError("artifact is not little-endian ELF64")
    machine = struct.unpack("<H", header[18:20])[0]
    if machine != expected["elf_machine"]:
        raise ValueError(
            f"artifact ELF machine is not {expected['elf_name']} ({expected['elf_machine']}) "
            f"for {target_triple}"
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    manifest_path = args.manifest.resolve()
    manifest = load_json(manifest_path)
    schema = load_json(SCHEMA)
    errors = sorted(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(manifest),
        key=lambda error: list(error.absolute_path),
    )
    if errors:
        for error in errors:
            print(f"manifest schema error at {list(error.absolute_path)}: {error.message}", file=sys.stderr)
        return 65

    base = manifest_path.parent
    artifact = resolve_child(base, manifest["artifact_path"])
    sbom_path = resolve_child(base, manifest["sbom"]["path"])
    provenance_path = resolve_child(base, manifest["provenance"]["path"])
    bundle_path = resolve_child(base, manifest["signature"]["bundle_path"])
    for path in (artifact, sbom_path, provenance_path, bundle_path):
        if not path.is_file():
            raise FileNotFoundError(path)

    verify_elf(artifact, manifest["target_triple"])
    expected = {
        artifact: manifest["artifact_sha256"],
        sbom_path: manifest["sbom"]["sha256"],
        provenance_path: manifest["provenance"]["sha256"],
        bundle_path: manifest["signature"]["bundle_sha256"],
    }
    for path, expected_digest in expected.items():
        actual = digest(path)
        if actual != expected_digest:
            raise ValueError(f"checksum mismatch for {path.name}: expected {expected_digest}, got {actual}")

    sbom = load_json(sbom_path)
    file_checksums = {
        item.get("fileName"): next(
            (entry.get("checksumValue") for entry in item.get("checksums", []) if entry.get("algorithm") == "SHA256"),
            None,
        )
        for item in sbom.get("files", [])
    }
    if sbom.get("spdxVersion") != "SPDX-2.3" or file_checksums.get(artifact.name) != manifest["artifact_sha256"]:
        raise ValueError("SBOM does not bind the artifact SHA256")

    provenance = load_json(provenance_path)
    subjects = {item.get("name"): item.get("digest", {}).get("sha256") for item in provenance.get("subject", [])}
    dependencies = provenance.get("predicate", {}).get("buildDefinition", {}).get("resolvedDependencies", [])
    source_revisions = {item.get("digest", {}).get("gitCommit") for item in dependencies}
    if provenance.get("_type") != "https://in-toto.io/Statement/v1":
        raise ValueError("provenance statement type is invalid")
    if provenance.get("predicateType") != "https://slsa.dev/provenance/v1":
        raise ValueError("provenance predicate type is invalid")
    if subjects.get(artifact.name) != manifest["artifact_sha256"]:
        raise ValueError("provenance subject does not bind the artifact SHA256")
    if manifest["source_revision"] not in source_revisions:
        raise ValueError("provenance does not bind the source revision")
    provenance_target = provenance.get("predicate", {}).get("buildDefinition", {}).get("externalParameters", {}).get("target")
    if provenance_target != manifest["target_triple"]:
        raise ValueError("provenance target does not match the artifact manifest")

    subprocess.run(
        [
            "cosign",
            "verify-blob",
            str(artifact),
            "--bundle",
            str(bundle_path),
            "--certificate-identity",
            manifest["signature"]["identity"],
            "--certificate-oidc-issuer",
            manifest["signature"]["issuer"],
        ],
        check=True,
    )
    print(
        json.dumps(
            {
                "validation": "native-artifact-v1",
                "status": "passed",
                "release_id": manifest["release_id"],
                "source_revision": manifest["source_revision"],
                "artifact_sha256": manifest["artifact_sha256"],
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (FileNotFoundError, ValueError, subprocess.CalledProcessError) as error:
        print(f"native artifact verification failed: {error}", file=sys.stderr)
        raise SystemExit(1) from error
