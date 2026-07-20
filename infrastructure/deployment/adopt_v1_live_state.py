#!/usr/bin/env python3
"""Validate or explicitly import existing OCI identities into encrypted V1 OpenTofu state."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[2]
ENV_DIR = ROOT / "infrastructure/opentofu/environments/v1-live"
MANIFEST_SCHEMA = ROOT / "contracts/infrastructure/adoption-manifest-v1.schema.json"
RESULT_SCHEMA = ROOT / "contracts/infrastructure/adoption-result-v1.schema.json"
OCID = re.compile(r"ocid1\.[A-Za-z0-9._-]+")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate(schema_path: Path, document: dict[str, Any], label: str) -> None:
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    errors = list(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(document))
    if errors:
        raise RuntimeError(f"invalid {label}: {errors[0].message}")


def redact(value: str) -> str:
    return OCID.sub("<oci-id-redacted>", value)[:1000]


def run(*argv: str, env: dict[str, str], timeout: int = 180) -> str:
    completed = subprocess.run(argv, cwd=ENV_DIR, env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, timeout=timeout)
    if completed.returncode != 0:
        raise RuntimeError(redact(completed.stderr.strip() or completed.stdout.strip() or f"command failed: {argv[0]}"))
    return completed.stdout


def state_ids(document: dict[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for resource in document.get("resources", []):
        if resource.get("mode", "managed") != "managed":
            continue
        module = resource.get("module")
        prefix = f"{module}." if module else ""
        base = f"{prefix}{resource.get('type')}.{resource.get('name')}"
        for index, instance in enumerate(resource.get("instances") or []):
            attributes = instance.get("attributes") or {}
            identifier = attributes.get("id")
            if not isinstance(identifier, str):
                continue
            key = instance.get("index_key")
            address = base if key is None else f"{base}[{json.dumps(key)}]"
            if address in result:
                raise RuntimeError(f"duplicate state address: {address}")
            result[address] = identifier
    return result


def identifiers_match(address: str, expected: str, actual: str) -> bool:
    if actual == expected:
        return True
    if address.startswith("module.v1_live.oci_core_network_security_group_security_rule."):
        return expected.endswith(f"/securityRules/{actual}")
    if address == "module.v1_live.oci_kms_key.main":
        return expected.endswith(f"/keys/{actual}")
    return False


def write_result(path: Path, document: dict[str, Any]) -> None:
    validate(RESULT_SCHEMA, document, "adoption result")
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.new.{os.getpid()}")
    temporary.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--var-file", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--approval-reference", default="")
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()

    for path, label in ((args.manifest, "manifest"), (args.var_file, "var file")):
        if not path.is_file() or path.is_symlink():
            raise SystemExit(f"{label} must be a regular non-symlink file")
    status = subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=all"], cwd=ROOT, text=True).strip()
    if status:
        raise SystemExit("clean worktree is required for exact-SHA adoption")
    git_sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    validate(MANIFEST_SCHEMA, manifest, "adoption manifest")
    if manifest["git_sha"] != git_sha or manifest["capacity_profile"] != "e5-temporary":
        raise SystemExit("adoption manifest is not bound to this exact E5 source revision")
    if manifest["status"] != "passed" or manifest["blockers"]:
        raise SystemExit("blocked adoption manifest cannot be consumed")

    result = {
        "schema_version": "liqi.infrastructure.adoption-result/v1",
        "git_sha": git_sha,
        "capacity_profile": "e5-temporary",
        "operation": "execute" if args.execute else "validate",
        "approval_reference": args.approval_reference or None,
        "manifest_sha256": sha(args.manifest),
        "var_file_sha256": sha(args.var_file),
        "status": "validated" if not args.execute else "failed",
        "imported_addresses": [],
        "already_present_addresses": [],
        "blockers": [],
        "state_mutation_performed": False,
        "oci_mutation_performed": False,
    }
    if not args.execute:
        if args.approval_reference:
            raise SystemExit("approval reference is accepted only with --execute")
        write_result(args.output, result)
        print(json.dumps({"status": "validated", "imports": len(manifest["imports"]), "oci_mutation_performed": False}, sort_keys=True))
        return 0
    if len(args.approval_reference.strip()) < 3:
        raise SystemExit("--execute requires a non-empty approval reference")

    env = os.environ.copy()
    if not env.get("TF_ENCRYPTION"):
        raise SystemExit("TF_ENCRYPTION is required")
    if "sslmode=verify-full" not in env.get("PG_CONN_STR", ""):
        raise SystemExit("PG_CONN_STR with sslmode=verify-full is required")
    if env.get("PG_SCHEMA_NAME") != "opentofu_v1_live":
        raise SystemExit("PG_SCHEMA_NAME must be opentofu_v1_live")
    for name in ("PG_SKIP_SCHEMA_CREATION", "PG_SKIP_TABLE_CREATION", "PG_SKIP_INDEX_CREATION"):
        if env.get(name) != "true":
            raise SystemExit(f"{name} must be true")
    env.pop("PGSERVICEFILE", None)
    env.pop("PGPASSFILE", None)
    env["TF_DATA_DIR"] = str(args.output.resolve().parent / "tfdata-adoption")

    try:
        run("tofu", "init", "-input=false", "-reconfigure", env=env, timeout=300)
        pulled = subprocess.run(
            ["tofu", "state", "pull"], cwd=ENV_DIR, env=env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, timeout=180,
        )
        if pulled.returncode == 0:
            current = state_ids(json.loads(pulled.stdout)) if pulled.stdout.strip() else {}
        elif "no state" in (pulled.stderr + pulled.stdout).lower():
            current = {}
        else:
            raise RuntimeError(redact(pulled.stderr.strip() or pulled.stdout.strip() or "unable to read OpenTofu state"))
        expected = {item["address"]: item["id"] for item in manifest["imports"]}
        unexpected = sorted(set(current) - set(expected))
        if unexpected:
            raise RuntimeError(f"state contains unexpected managed addresses: {unexpected}")
        for address, identifier in sorted(expected.items()):
            if address in current:
                if not identifiers_match(address, identifier, current[address]):
                    raise RuntimeError(f"state ID mismatch for {address}")
                result["already_present_addresses"].append(address)
                continue
            run(
                "tofu", "import", "-input=false", f"-var-file={args.var_file.resolve()}",
                f"-var=source_git_sha={git_sha}", "-var=capacity_profile=e5-temporary",
                address, identifier, env=env, timeout=300,
            )
            result["imported_addresses"].append(address)
            result["state_mutation_performed"] = True
        result["status"] = "passed"
    except Exception as exc:
        result["blockers"] = [redact(str(exc))]
        write_result(args.output, result)
        print(json.dumps({"status": "failed", "state_mutation_performed": result["state_mutation_performed"], "oci_mutation_performed": False}, sort_keys=True))
        return 4

    write_result(args.output, result)
    print(json.dumps({"status": "passed", "imported": len(result["imported_addresses"]), "already_present": len(result["already_present_addresses"]), "oci_mutation_performed": False}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
