#!/usr/bin/env python3
"""Generate a deterministic release-manifest-v0 from source and artifact inputs."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = ROOT / "contracts" / "operations" / "release-manifest-v0.schema.json"


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def git_value(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def resolve_path(value: str) -> Path:
    path = (ROOT / value).resolve()
    try:
        path.relative_to(ROOT.resolve())
    except ValueError as exc:
        raise ValueError(f"input path escapes repository root: {value}") from exc
    if not path.is_file():
        raise FileNotFoundError(path)
    return path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--git-sha")
    parser.add_argument("--source-date-epoch", type=int)
    args = parser.parse_args()

    source = load_json(args.input)
    git_sha = args.git_sha or git_value("rev-parse", "HEAD")
    if len(git_sha) != 40:
        raise ValueError("git SHA must be the full 40-character commit ID")
    source_epoch = args.source_date_epoch
    if source_epoch is None:
        source_epoch = int(git_value("show", "-s", "--format=%ct", git_sha))
    source_timestamp = datetime.fromtimestamp(source_epoch, tz=timezone.utc).isoformat().replace("+00:00", "Z")

    artifacts = []
    for artifact in sorted(source["artifacts"], key=lambda item: item["name"]):
        path = resolve_path(artifact["path"])
        artifacts.append({
            "name": artifact["name"],
            "media_type": artifact["media_type"],
            "sha256": digest(path),
            "size_bytes": path.stat().st_size,
            "sbom_ref": artifact["sbom_ref"],
            "provenance_ref": artifact["provenance_ref"]
        })

    plan_path = resolve_path(source["infrastructure"]["plan_path"])
    config_path = resolve_path(source["configuration"]["path"])
    sbom_path = resolve_path(source["supply_chain"]["sbom"]["path"])
    provenance_path = resolve_path(source["supply_chain"]["provenance"]["path"])
    release_id = f"{source['release_id_prefix']}-{git_sha[:12]}"

    manifest = {
        "schema_version": "release-manifest-v0",
        "release_id": release_id,
        "git_sha": git_sha,
        "source_timestamp": source_timestamp,
        "source_date_epoch": source_epoch,
        "rust_toolchain": source["rust_toolchain"],
        "artifacts": artifacts,
        "contract_versions": source["contract_versions"],
        "database_migration": source["database_migration"],
        "infrastructure": {
            "required_output_version": source["infrastructure"]["required_output_version"],
            "cost_classification": source["infrastructure"]["cost_classification"],
            "plan_digest": digest(plan_path)
        },
        "configuration": {
            "schema_version": source["configuration"]["schema_version"],
            "digest": digest(config_path),
            "contains_secrets": False
        },
        "rollback": source["rollback"],
        "supply_chain": {
            "sbom": {"uri": source["supply_chain"]["sbom"]["uri"], "sha256": digest(sbom_path)},
            "provenance": {"uri": source["supply_chain"]["provenance"]["uri"], "sha256": digest(provenance_path)},
            "attestation_policy": source["supply_chain"]["attestation_policy"]
        },
        "deployment": {
            "environment": source["environment"],
            **source["deployment"]
        }
    }

    schema = load_json(SCHEMA)
    errors = sorted(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(manifest),
        key=lambda item: list(item.absolute_path)
    )
    if errors:
        rendered = "\n".join(f"{'.'.join(map(str, error.absolute_path))}: {error.message}" for error in errors)
        raise ValueError(f"generated release manifest is invalid:\n{rendered}")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    print(f"generated {args.output} release_id={release_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
