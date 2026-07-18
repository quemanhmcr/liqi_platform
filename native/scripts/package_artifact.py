#!/usr/bin/env python3
"""Create and verify the provenance-bound ARM64 NIF artifact manifest."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import struct
import subprocess
import sys
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = ROOT / "contracts" / "native" / "native-artifact-v1.schema.json"
ARTIFACT_NAME = "libliqi_sequence_diff_nif.so"
RELEASE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, document: Any) -> None:
    path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def require_arm64_elf(path: Path) -> None:
    header = path.read_bytes()[:20]
    if len(header) < 20 or header[:4] != b"\x7fELF" or header[4] != 2 or header[5] != 1:
        raise ValueError("artifact must be a little-endian ELF64 binary")
    machine = struct.unpack("<H", header[18:20])[0]
    if machine != 183:
        raise ValueError(f"artifact ELF machine must be AArch64 (183), got {machine}")


def cargo_packages() -> list[dict[str, Any]]:
    completed = subprocess.run(
        ["cargo", "+1.97.1", "metadata", "--locked", "--format-version", "1"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    metadata = json.loads(completed.stdout)
    packages = []
    for package in sorted(metadata["packages"], key=lambda item: (item["name"], item["version"])):
        packages.append(
            {
                "SPDXID": f"SPDXRef-Package-{len(packages) + 1}",
                "name": package["name"],
                "versionInfo": package["version"],
                "downloadLocation": "NOASSERTION",
                "filesAnalyzed": False,
                "licenseConcluded": "NOASSERTION",
                "licenseDeclared": package.get("license") or "NOASSERTION",
                "externalRefs": [
                    {
                        "referenceCategory": "PACKAGE-MANAGER",
                        "referenceType": "purl",
                        "referenceLocator": f"pkg:cargo/{package['name']}@{package['version']}",
                    }
                ],
            }
        )
    return packages


def timestamp() -> str:
    epoch = os.environ.get("SOURCE_DATE_EPOCH")
    when = dt.datetime.fromtimestamp(int(epoch), tz=dt.timezone.utc) if epoch else dt.datetime.now(dt.timezone.utc)
    return when.isoformat(timespec="seconds").replace("+00:00", "Z")


def verify_bundle(artifact: Path, bundle: Path, identity: str, issuer: str) -> None:
    subprocess.run(
        [
            "cosign",
            "verify-blob",
            str(artifact),
            "--bundle",
            str(bundle),
            "--certificate-identity",
            identity,
            "--certificate-oidc-issuer",
            issuer,
        ],
        check=True,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--release-id", required=True)
    parser.add_argument("--source-revision", required=True)
    parser.add_argument("--builder-id", required=True)
    parser.add_argument("--signature-identity", required=True)
    parser.add_argument("--signature-issuer", required=True)
    args = parser.parse_args()

    if not RELEASE_PATTERN.fullmatch(args.release_id):
        parser.error("--release-id has an invalid format")
    if not SHA_PATTERN.fullmatch(args.source_revision):
        parser.error("--source-revision must be an exact lowercase 40-character Git SHA")
    if not args.builder_id.startswith("https://"):
        parser.error("--builder-id must be an HTTPS URI")
    if not args.signature_issuer.startswith("https://"):
        parser.error("--signature-issuer must be an HTTPS URI")

    artifact_dir = args.artifact_dir.resolve()
    artifact = artifact_dir / ARTIFACT_NAME
    bundle = artifact_dir / f"{ARTIFACT_NAME}.sigstore.json"
    if not artifact.is_file() or not bundle.is_file():
        parser.error(f"{ARTIFACT_NAME} and its .sigstore.json bundle must exist in --artifact-dir")
    require_arm64_elf(artifact)
    verify_bundle(artifact, bundle, args.signature_identity, args.signature_issuer)

    artifact_sha = digest(artifact)
    created = timestamp()
    sbom_path = artifact_dir / f"{ARTIFACT_NAME}.spdx.json"
    provenance_path = artifact_dir / f"{ARTIFACT_NAME}.intoto.json"
    manifest_path = artifact_dir / f"native-artifact-{args.release_id}.json"

    packages = cargo_packages()
    artifact_spdx_id = "SPDXRef-File-liqi-sequence-diff-nif"
    sbom = {
        "spdxVersion": "SPDX-2.3",
        "dataLicense": "CC0-1.0",
        "SPDXID": "SPDXRef-DOCUMENT",
        "name": f"liqi-native-{args.release_id}",
        "documentNamespace": f"https://artifacts.liqi.internal/native/{args.release_id}/{artifact_sha}",
        "creationInfo": {"created": created, "creators": [f"Tool: {args.builder_id}"]},
        "files": [
            {
                "fileName": ARTIFACT_NAME,
                "SPDXID": artifact_spdx_id,
                "checksums": [{"algorithm": "SHA256", "checksumValue": artifact_sha}],
                "licenseConcluded": "NOASSERTION",
                "copyrightText": "NOASSERTION",
            }
        ],
        "packages": packages,
        "relationships": [
            {"spdxElementId": "SPDXRef-DOCUMENT", "relationshipType": "DESCRIBES", "relatedSpdxElement": artifact_spdx_id}
        ],
    }
    write_json(sbom_path, sbom)

    provenance = {
        "_type": "https://in-toto.io/Statement/v1",
        "subject": [{"name": ARTIFACT_NAME, "digest": {"sha256": artifact_sha}}],
        "predicateType": "https://slsa.dev/provenance/v1",
        "predicate": {
            "buildDefinition": {
                "buildType": "https://contracts.liqi.internal/native/build-types/rustler-nif-v1",
                "externalParameters": {
                    "target": "aarch64-unknown-linux-gnu",
                    "profile": "nif-release",
                    "release_id": args.release_id,
                },
                "internalParameters": {"build_jobs_max": 2, "panic_strategy": "unwind"},
                "resolvedDependencies": [
                    {
                        "uri": "git+https://github.com/liqi-platform/liqi_platform",
                        "digest": {"gitCommit": args.source_revision},
                    }
                ],
            },
            "runDetails": {
                "builder": {"id": args.builder_id},
                "metadata": {"invocationId": args.release_id, "startedOn": created, "finishedOn": created},
            },
        },
    }
    write_json(provenance_path, provenance)

    manifest = {
        "schema_version": "native-artifact-v1",
        "artifact": "liqi_sequence_diff_nif",
        "artifact_version": "1.0.0",
        "release_id": args.release_id,
        "source_revision": args.source_revision,
        "crate": "liqi-sequence-diff-nif",
        "target_triple": "aarch64-unknown-linux-gnu",
        "architecture": "aarch64",
        "libc": "gnu",
        "rust_toolchain": "1.97.1",
        "cargo_version": "1.97.1",
        "rustler_version": "0.38.0",
        "nif_abi": "2.15",
        "build_profile": "nif-release",
        "panic_strategy": "unwind",
        "artifact_path": artifact.name,
        "artifact_sha256": artifact_sha,
        "artifact_size_bytes": artifact.stat().st_size,
        "kernels": [{"name": "compact_sequence_diff", "version": "1", "scheduler_class": "regular"}],
        "sbom": {"path": sbom_path.name, "sha256": digest(sbom_path), "media_type": "application/spdx+json"},
        "provenance": {
            "path": provenance_path.name,
            "sha256": digest(provenance_path),
            "media_type": "application/vnd.in-toto+json",
        },
        "signature": {
            "algorithm": "cosign-keyless",
            "bundle_path": bundle.name,
            "bundle_sha256": digest(bundle),
            "identity": args.signature_identity,
            "issuer": args.signature_issuer,
        },
        "built_at": created,
    }
    schema = load_json(SCHEMA)
    errors = sorted(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(manifest),
        key=lambda error: list(error.absolute_path),
    )
    if errors:
        for error in errors:
            print(f"artifact manifest validation failed at {list(error.absolute_path)}: {error.message}", file=sys.stderr)
        return 65
    write_json(manifest_path, manifest)
    print(json.dumps({"artifact_manifest": str(manifest_path), "status": "passed", "sha256": artifact_sha}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
