#!/usr/bin/env python3
"""Validate GitHub workflow syntax and V0 safety invariants."""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any, Iterable

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_DIR = ROOT / ".github" / "workflows"
SHA_REF = re.compile(r"^[^\s@]+@[0-9a-f]{40}$")
FORBIDDEN_MUTATIONS = (
    re.compile(r"\b(?:tofu|terraform)\s+apply\b", re.IGNORECASE),
    re.compile(r"\boci\s+[^\n]*(?:create|update|delete|terminate)\b", re.IGNORECASE),
    re.compile(r"\bkubectl\s+(?:apply|delete)\b", re.IGNORECASE),
)
FORBIDDEN_LONG_LIVED_SECRETS = (
    "OCI_PRIVATE_KEY",
    "OCI_API_KEY",
    "OCI_CLI_KEY_FILE",
    "OCI_USER_OCID",
)


def iter_steps(node: Any) -> Iterable[dict[str, Any]]:
    if not isinstance(node, dict):
        return
    jobs = node.get("jobs", {})
    if not isinstance(jobs, dict):
        return
    for job in jobs.values():
        if isinstance(job, dict):
            for step in job.get("steps", []) or []:
                if isinstance(step, dict):
                    yield step


def main() -> int:
    failures: list[str] = []
    workflow_paths = sorted(WORKFLOW_DIR.glob("*.y*ml"))
    if not workflow_paths:
        failures.append("no workflows found")
    for path in workflow_paths:
        relative = path.relative_to(ROOT).as_posix()
        text = path.read_text(encoding="utf-8")
        try:
            workflow = yaml.load(text, Loader=yaml.BaseLoader)
        except yaml.YAMLError as exc:
            failures.append(f"{relative}: invalid YAML: {exc}")
            continue
        if not isinstance(workflow, dict):
            failures.append(f"{relative}: workflow root must be an object")
            continue
        permissions = workflow.get("permissions")
        if not isinstance(permissions, dict) or permissions.get("contents") != "read":
            failures.append(f"{relative}: top-level permissions.contents must be read")
        for pattern in FORBIDDEN_MUTATIONS:
            if pattern.search(text):
                failures.append(f"{relative}: forbidden automatic mutation command matches {pattern.pattern}")
        for secret_name in FORBIDDEN_LONG_LIVED_SECRETS:
            if secret_name in text:
                failures.append(f"{relative}: long-lived OCI credential reference is forbidden: {secret_name}")
        checkout_count = 0
        checkout_hardened = 0
        for step in iter_steps(workflow):
            uses = step.get("uses")
            if uses:
                if not SHA_REF.match(uses):
                    failures.append(f"{relative}: action must be pinned to a 40-char commit SHA: {uses}")
                if uses.startswith("actions/checkout@"):
                    checkout_count += 1
                    with_block = step.get("with", {})
                    if isinstance(with_block, dict) and with_block.get("persist-credentials") == "false":
                        checkout_hardened += 1
                if uses.startswith("opentofu/setup-opentofu@"):
                    with_block = step.get("with", {})
                    version = with_block.get("tofu_version") if isinstance(with_block, dict) else None
                    wrapper = with_block.get("tofu_wrapper") if isinstance(with_block, dict) else None
                    if not isinstance(version, str) or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
                        failures.append(f"{relative}: OpenTofu must be pinned to an exact semantic version")
                    if wrapper != "false":
                        failures.append(f"{relative}: OpenTofu wrapper must be disabled for unambiguous provider exit codes")
        if checkout_count != checkout_hardened:
            failures.append(f"{relative}: every checkout step must set persist-credentials: false")
        if path.name == "ci.yml":
            required_source_seams = (
                "assemble_source_readiness.py",
                "source-integration-readiness-v0.json",
                "provider-source-result.json",
                "provider-compatibility-result.json",
                "provider-capacity-result.json",
            )
            for required in required_source_seams:
                if required not in text:
                    failures.append(f"{relative}: source CI is missing integration readiness seam {required}")
            step_by_name = {step.get("name"): step for step in iter_steps(workflow) if step.get("name")}
            python_step = step_by_name.get("Configure Python")
            python_with = python_step.get("with", {}) if isinstance(python_step, dict) else {}
            cache_paths = python_with.get("cache-dependency-path", "") if isinstance(python_with, dict) else ""
            if "database/requirements-validation.txt" not in cache_paths:
                failures.append(f"{relative}: source CI cache must include pinned database validation requirements")
            install_step = step_by_name.get("Install pinned control-plane dependencies")
            install_command = install_step.get("run", "") if isinstance(install_step, dict) else ""
            if "-r database/requirements-validation.txt" not in install_command:
                failures.append(f"{relative}: source CI must install pinned database validation requirements")
            for evidence_step in (
                "Validate cross-provider compatibility",
                "Collect provider capacity budgets",
                "Invoke published provider source gates",
            ):
                step = step_by_name.get(evidence_step)
                if not isinstance(step, dict) or step.get("continue-on-error") != "true":
                    failures.append(f"{relative}: {evidence_step} must preserve evidence with continue-on-error: true")
            readiness_step = step_by_name.get("Assemble source integration readiness")
            if not isinstance(readiness_step, dict) or readiness_step.get("if") != "always()":
                failures.append(f"{relative}: source readiness composition must run with if: always()")

        if path.name == "v1-source-readiness.yml":
            for required in (
                "operations/bin/validate_readiness_v1.py",
                "--stage source",
                "--stage integration",
                "--stage artifact",
                "--allow-disposable-database",
                "tests/load/v1-floor.js",
                "tests/load/reconnect-storm-v1.js",
            ):
                if required not in text:
                    failures.append(f"{relative}: V1 source workflow is missing stage seam {required}")
            if "--allow-blocked" not in text:
                failures.append(f"{relative}: source/integration/artifact CI must retain owner-attributed blocked evidence")
        if path.name == "v1-e5-artifact-release.yml":
            triggers = workflow.get("on")
            if (
                not isinstance(triggers, dict)
                or set(triggers) != {"workflow_dispatch"}
            ):
                failures.append(f"{relative}: E5 artifact publication must be manual-only")
            permissions = workflow.get("permissions")
            if not isinstance(permissions, dict) or permissions.get("id-token") != "write":
                failures.append(f"{relative}: E5 artifact publication requires OIDC id-token: write")
            for required in (
                "environment: v1-e5-artifact-production",
                "runs-on: ubuntu-24.04",
                "nightly-2026-07-01",
                'CARGO_FUZZ_VERSION: "0.13.2"',
                'COSIGN_VERSION: "3.1.2"',
                "native/scripts/run_v1_safety_gates.py",
                "native/scripts/build-x86_64-artifact.sh",
                "--target-triple x86_64-unknown-linux-gnu",
                "cosign sign-blob --yes",
                "infrastructure/deployment/authorize_native_artifact.py",
                "beam/scripts/build_linux_release.py",
                "LIQI_NATIVE_DEPLOYMENT_ED25519_PRIVATE_KEY",
                "LIQI_RELEASE_ARTIFACT_ED25519_PRIVATE_KEY",
                "LIQI_RELEASE_MANIFEST_ED25519_PRIVATE_KEY",
                "if: always()",
                'rm -rf "$RUNNER_TEMP/liqi-signing-private"',
                "actions/upload-artifact@",
            ):
                if required not in text:
                    failures.append(f"{relative}: E5 artifact workflow is missing protected seam {required}")
            if "--allow-blocked" in text or "--allow-not-ready" in text or "mock:" in text:
                failures.append(f"{relative}: E5 artifact publication cannot tolerate blocked, not-ready, or mock evidence")
            if "$GITHUB_WORKSPACE/liqi-signing-private" in text or ".artifacts/liqi-signing-private" in text:
                failures.append(f"{relative}: private signing keys must remain under RUNNER_TEMP")

        if path.name == "v1-live-readiness.yml":
            triggers = workflow.get("on")
            if not isinstance(triggers, dict) or "workflow_dispatch" not in triggers or "push" in triggers or "pull_request" in triggers:
                failures.append(f"{relative}: V1 live readiness must be manual-only")
            for required in (
                "environment: v1-${{ inputs.environment }}",
                "--allow-live-read-only",
                "restore_approval_ref",
                "actions/download-artifact@",
                "operations/bin/compose_readiness_v1.py",
                "v1-readiness-result.json",
            ):
                if required not in text:
                    failures.append(f"{relative}: V1 live workflow is missing protected seam {required}")
            if "--allow-blocked" in text or "--allow-not-ready" in text:
                failures.append(f"{relative}: protected live readiness cannot tolerate blocked or not-ready results")
        if "provider-integration" in path.name:
            triggers = workflow.get("on")
            if not isinstance(triggers, dict) or "workflow_dispatch" not in triggers or "push" in triggers:
                failures.append(f"{relative}: provider integration must be manual-only")
            if "--allow-blocked" in text or "mock:" in text:
                failures.append(f"{relative}: strict provider integration cannot allow blocked or mock gates")
            for required in (
                "oci_plan_run_id", "database_recovery_run_id", "actions/download-artifact@",
                "LIQI_OCI_PLAN_JSON", "LIQI_BACKUP_METADATA_FILE", "collect_provider_capacity.py",
                "extract_provider_output.py", "check_recovery_freshness.py", "assemble_integration_result.py",
            ):
                if required not in text:
                    failures.append(f"{relative}: promotion workflow is missing protected OCI plan evidence seam {required}")
    if failures:
        for failure in failures:
            print(f"ERROR ci-workflow: {failure}", file=sys.stderr)
        return 1
    print(f"validated {len(workflow_paths)} GitHub workflows with pinned actions and no automatic mutations")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
