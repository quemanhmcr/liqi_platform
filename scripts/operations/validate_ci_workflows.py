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
        if "provider-integration" in path.name:
            triggers = workflow.get("on")
            if not isinstance(triggers, dict) or "workflow_dispatch" not in triggers or "push" in triggers:
                failures.append(f"{relative}: provider integration must be manual-only")
            if "--allow-blocked" in text or "mock:" in text:
                failures.append(f"{relative}: strict provider integration cannot allow blocked or mock gates")
            for required in ("oci_plan_run_id", "actions/download-artifact@", "LIQI_OCI_PLAN_JSON", "collect_provider_capacity.py"):
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
