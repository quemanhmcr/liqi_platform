#!/usr/bin/env python3
"""Validate Senior 4 V1 infrastructure/deployment contracts and semantic invariants."""
from __future__ import annotations
import json
import sys
from pathlib import Path
from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[2]
PAIRS = [
    ("contracts/infrastructure/state-backend-evidence-v1.schema.json", "contracts/infrastructure/state-backend-evidence-v1.example.json"),
    ("contracts/infrastructure/oci-live-v1.schema.json", "contracts/infrastructure/oci-live-v1.example.json"),
    ("contracts/infrastructure/oci-live-v1.schema.json", "contracts/infrastructure/oci-live-v1.e5-temporary.example.json"),
    ("contracts/infrastructure/adoption-manifest-v1.schema.json", "contracts/infrastructure/adoption-manifest-v1.example.json"),
    ("contracts/infrastructure/adoption-result-v1.schema.json", "contracts/infrastructure/adoption-result-v1.example.json"),
    ("contracts/infrastructure/pre-apply-readiness-v1.schema.json", "contracts/infrastructure/pre-apply-readiness-v1.example.json"),
    ("contracts/infrastructure/plan-result-v1.schema.json", "contracts/infrastructure/plan-result-v1.example.json"),
    ("contracts/infrastructure/apply-result-v1.schema.json", "contracts/infrastructure/apply-result-v1.example.json"),
    ("contracts/infrastructure/host-runtime-v1.schema.json", "contracts/infrastructure/host-runtime-v1.example.json"),
    ("contracts/infrastructure/secret-mapping-v1.schema.json", "contracts/infrastructure/secret-mapping-v1.example.json"),
    ("contracts/infrastructure/host-bundle-v1.schema.json", "contracts/infrastructure/host-bundle-v1.example.json"),
    ("contracts/infrastructure/host-bundle-v1.schema.json", "contracts/infrastructure/host-bundle-v1.x86_64.example.json"),
    ("contracts/infrastructure/database-credentials-v1.schema.json", "contracts/infrastructure/database-credentials-v1.example.json"),
    ("contracts/runtime/linux-release-build-result-v1.schema.json", "contracts/runtime/linux-release-build-result-v1.example.json"),
    ("contracts/deployment/mix-release-v1.schema.json", "contracts/deployment/mix-release-v1.example.json"),
    ("contracts/deployment/mix-release-v1.schema.json", "contracts/deployment/mix-release-v1.e5-temporary.example.json"),
    ("contracts/deployment/mix-deployment-v1.schema.json", "contracts/deployment/mix-deployment-v1.example.json"),
    ("contracts/deployment/v0-rollback-compatibility-v1.schema.json", "contracts/deployment/v0-rollback-compatibility-v1.example.json"),
    ("contracts/deployment/release-target-v1.schema.json", "contracts/deployment/release-target-v1.example.json"),
    ("contracts/deployment/installed-release-v1.schema.json", "contracts/deployment/installed-release-v1.example.json"),
    ("contracts/deployment/native-artifact-v1.schema.json", "contracts/deployment/native-artifact-v1.example.json"),
    ("contracts/deployment/native-artifact-v1.schema.json", "contracts/deployment/native-artifact-v1.x86_64.example.json"),
    ("contracts/deployment/native-authorization-result-v1.schema.json", "contracts/deployment/native-authorization-result-v1.example.json"),
    ("contracts/deployment/activation-v1.schema.json", "contracts/deployment/activation-v1.example.json"),
    ("contracts/deployment/rollback-v1.schema.json", "contracts/deployment/rollback-v1.example.json"),
    ("contracts/deployment/live-endpoint-v1.schema.json", "contracts/deployment/live-endpoint-v1.example.json"),
]


def load(path: str) -> object:
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def main() -> int:
    failures: list[str] = []
    examples: dict[str, dict[str, object]] = {}
    for schema_path, example_path in PAIRS:
        schema = load(schema_path)
        example = load(example_path)
        Draft202012Validator.check_schema(schema)
        validator = Draft202012Validator(schema, format_checker=FormatChecker())
        for error in sorted(validator.iter_errors(example), key=lambda item: list(item.absolute_path)):
            location = ".".join(map(str, error.absolute_path)) or "<root>"
            failures.append(f"{example_path}:{location}: {error.message}")
        assert isinstance(example, dict)
        examples[example_path] = example

    oci = examples["contracts/infrastructure/oci-live-v1.example.json"]
    capacity = oci["capacity"]
    if capacity["combined_storage_gib"] != capacity["boot_volume_gib"] + capacity["data_volume_gib"]:
        failures.append("OCI capacity combined storage is inconsistent")
    if capacity["combined_storage_gib"] > capacity["provider_disk_ceiling_gib"]:
        failures.append("OCI capacity exceeds provider disk ceiling")
    ingress = {(item["protocol"], item["port"]) for item in oci["network"]["public_ingress"]}
    if ingress != {("tcp", 80), ("tcp", 443)}:
        failures.append(f"public ingress must be exactly TCP 80/443, got {sorted(ingress)}")
    if oci["mutation"]["applied"] or oci["mutation"]["approval_reference"] is not None:
        failures.append("source fixture must not claim approved OCI mutation")


    adoption = examples["contracts/infrastructure/adoption-manifest-v1.example.json"]
    if (adoption["status"] == "passed") != (len(adoption["blockers"]) == 0):
        failures.append("adoption manifest status must match blocker presence")
    addresses = [item["address"] for item in adoption["imports"]]
    if len(addresses) != len(set(addresses)):
        failures.append("adoption manifest import addresses must be unique")


    pre_apply = examples["contracts/infrastructure/pre-apply-readiness-v1.example.json"]
    expected_pre_apply_checks = [
        "oci-adoption-handoff", "state-backend", "state-adoption",
        "protected-tfvars", "signed-x86-release", "rollback-target",
        "protected-environment",
    ]
    if [item["name"] for item in pre_apply["checks"]] != expected_pre_apply_checks:
        failures.append("pre-apply readiness checks must remain exact and ordered")
    if pre_apply["status"] == "passed" and (pre_apply["blockers"] or any(value is None for value in pre_apply["inputs"].values())):
        failures.append("passed pre-apply readiness must bind every input and have zero blockers")
    if pre_apply["state_mutation_performed"] or pre_apply["oci_mutation_performed"]:
        failures.append("pre-apply readiness fixture must never claim state or OCI mutation")

    plan_result = examples["contracts/infrastructure/plan-result-v1.example.json"]
    apply_result = examples["contracts/infrastructure/apply-result-v1.example.json"]
    if plan_result["oci_mutation_performed"] or plan_result["mode"] != "approved-apply" or plan_result["plan_mode"] != "adopt-existing":
        failures.append("plan result fixture must be a non-mutating approved E5 adoption plan")
    for name, value in plan_result["inputs"].items():
        if value is None:
            failures.append(f"approved adoption plan fixture is missing input binding {name}")
    if not apply_result["oci_mutation_performed"] or apply_result["status"] != "applied":
        failures.append("apply result fixture must record an approved OCI mutation")
    for name in ("pre_apply_readiness_sha256", "linux_release_build_result_sha256", "rollback_target_sha256"):
        if apply_result[name] != plan_result["inputs"][name]:
            failures.append(f"apply result fixture differs from plan binding {name}")
    if apply_result["saved_plan_sha256"] != plan_result["saved_plan"]["sha256"]:
        failures.append("apply result fixture differs from saved plan digest")
    apply_oci = apply_result["oci_output"]
    if (
        apply_oci["git_sha"] != apply_result["git_sha"]
        or apply_oci["capacity"]["profile"] != "e5-temporary"
        or apply_oci["mutation"]["applied"] is not True
        or apply_oci["mutation"]["approval_reference"] != apply_result["approval_reference"]
        or apply_oci["mutation"]["plan_sha256"] != apply_result["saved_plan_sha256"]
    ):
        failures.append("apply result fixture does not cross-bind exact OCI output identity")

    target_pairs = {
        "aarch64-unknown-linux-gnu": "aarch64",
        "x86_64-unknown-linux-gnu": "x86_64",
    }
    e5_release = examples["contracts/deployment/mix-release-v1.e5-temporary.example.json"]
    if e5_release["target_triple"] != "x86_64-unknown-linux-gnu":
        failures.append("temporary E5 release fixture must target x86_64 GNU/Linux")
    for path in (
        "contracts/infrastructure/host-bundle-v1.example.json",
        "contracts/infrastructure/host-bundle-v1.x86_64.example.json",
        "contracts/deployment/native-artifact-v1.example.json",
        "contracts/deployment/native-artifact-v1.x86_64.example.json",
    ):
        if examples[path]["target_triple"] not in target_pairs:
            failures.append(f"unsupported target triple in {path}")

    release = examples["contracts/deployment/mix-release-v1.example.json"]
    db = release["database_compatibility"]
    if not (db["rollback_safe_through"] <= db["minimum_migration"] <= db["maximum_migration"]):
        failures.append("release database compatibility range is invalid")
    if release["installation"]["release_directory"] != f"/opt/liqi/releases/{release['release_id']}":
        failures.append("release directory must be derived exactly from release_id")

    activation = examples["contracts/deployment/activation-v1.example.json"]
    rollback = examples["contracts/deployment/rollback-v1.example.json"]
    endpoint = examples["contracts/deployment/live-endpoint-v1.example.json"]
    for name, document in (("activation", activation), ("rollback", rollback), ("endpoint", endpoint)):
        if document["status"] != "engineering-complete-evidence-pending":
            failures.append(f"{name} source fixture must report evidence pending")
    if activation["traffic_enabled"]:
        failures.append("source fixture must not claim traffic enabled")

    if failures:
        for failure in failures:
            print(f"ERROR {failure}", file=sys.stderr)
        return 1
    print(f"validated {len(PAIRS)} V1 Senior 4 contract/example pairs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
