#!/usr/bin/env python3
"""Validate the OCI host example against the provider-owned JSON Schema."""

from __future__ import annotations

import json
import sys
from pathlib import Path

try:
    from jsonschema import Draft202012Validator, FormatChecker
except ImportError as exc:  # pragma: no cover - explicit operator guidance
    raise SystemExit(
        "jsonschema is required; install with: "
        "python -m pip install -r infrastructure/validation/requirements.txt"
    ) from exc

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = ROOT / "contracts/platform/oci-host-v0.schema.json"
EXAMPLE_PATH = ROOT / "contracts/platform/oci-host-v0.example.json"


def load_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"failed to load {path.relative_to(ROOT)}: {exc}") from exc


def main() -> int:
    schema = load_json(SCHEMA_PATH)
    example = load_json(EXAMPLE_PATH)

    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    errors = sorted(validator.iter_errors(example), key=lambda error: list(error.path))
    if errors:
        for error in errors:
            location = ".".join(str(part) for part in error.absolute_path) or "<root>"
            print(f"ERROR {location}: {error.message}", file=sys.stderr)
        return 1

    assert isinstance(example, dict)
    semantic_errors: list[str] = []

    capacity = example["capacity_profile"]
    expected_capacity = {
        "name": "free-tier-a1-4x24",
        "shape": "VM.Standard.A1.Flex",
        "architecture": "aarch64",
        "ocpus": 4,
        "memory_gb": 24,
    }
    for key, expected in expected_capacity.items():
        if capacity.get(key) != expected:
            semantic_errors.append(
                f"capacity_profile.{key} must be {expected!r}, got {capacity.get(key)!r}"
            )

    if example.get("infrastructure_output_version") != "0.3.0":
        semantic_errors.append("infrastructure_output_version must be 0.3.0")
    if example.get("bootstrap_version") != "0.3.0":
        semantic_errors.append("bootstrap_version must be 0.3.0")

    expected_runtime_files = {
        "liqi-api": "/etc/liqi/api.json",
        "liqi-realtime": "/etc/liqi/realtime.json",
        "liqi-worker": "/etc/liqi/worker.json",
    }
    runtime_configuration = example.get("runtime_configuration", {})
    if runtime_configuration.get("environment_variable") != "LIQI_CONFIG_PATH":
        semantic_errors.append("runtime configuration must publish LIQI_CONFIG_PATH")
    if runtime_configuration.get("cli_argument") != "--config":
        semantic_errors.append("runtime configuration must publish --config")
    if runtime_configuration.get("maximum_file_bytes") != 1048576:
        semantic_errors.append("runtime configuration must preserve the 1 MiB bound")
    actual_runtime_files = {
        item.get("service"): item.get("path")
        for item in runtime_configuration.get("files", [])
    }
    if actual_runtime_files != expected_runtime_files:
        semantic_errors.append(
            f"runtime configuration files must be {expected_runtime_files!r}, got {actual_runtime_files!r}"
        )

    execution = example.get("execution_control", {})
    expected_slices = {
        "parent": ("liqi-platform.slice", None, 300, 20480),
        "runtime": ("liqi-platform-runtime.slice", "liqi-platform.slice", 145, 7168),
        "database": ("liqi-platform-database.slice", "liqi-platform.slice", 120, 7936),
        "operations": ("liqi-platform-operations.slice", "liqi-platform.slice", 25, 1024),
        "edge": ("liqi-platform-edge.slice", "liqi-platform.slice", 10, 256),
    }
    for key, expected in expected_slices.items():
        record = execution.get(key, {})
        actual = (
            record.get("slice"), record.get("parent"), record.get("cpu_quota_percent"),
            record.get("memory_max_mib")
        )
        if actual != expected or record.get("memory_swap_max_mib") != 0:
            semantic_errors.append(f"execution_control.{key} mismatch: {actual!r}")

    expected_service_controls = {
        "liqi-api": ("liqi-api.service", 45, 2048, "/etc/liqi/api.json"),
        "liqi-realtime": ("liqi-realtime.service", 65, 3072, "/etc/liqi/realtime.json"),
        "liqi-worker": ("liqi-worker.service", 35, 2048, "/etc/liqi/worker.json"),
    }
    service_controls = {item.get("service"): item for item in execution.get("services", [])}
    if set(service_controls) != set(expected_service_controls):
        semantic_errors.append("execution_control.services must contain exactly the three Rust services")
    else:
        for service, expected in expected_service_controls.items():
            record = service_controls[service]
            actual = (
                record.get("unit"), record.get("cpu_quota_percent"),
                record.get("memory_max_mib"), record.get("config_path")
            )
            if actual != expected or record.get("memory_swap_max_mib") != 0:
                semantic_errors.append(f"execution control mismatch for {service}: {actual!r}")

    cpu_policy = execution.get("cpu_aggregation", {})
    if cpu_policy != {
        "hard_ceiling_semantics": "additive-admission-with-parent-enforcement",
        "hard_ceiling_limit_ocpu": 3,
        "steady_state_limit_ocpu": 3,
        "host_scheduling_reserve_ocpu": 1,
        "parent_enforcement": "liqi-platform.slice",
    }:
        semantic_errors.append("CPU aggregation policy does not preserve the 1 OCPU reserve")
    memory_policy = execution.get("memory_aggregation", {})
    if memory_policy != {
        "hard_limits_additive": True,
        "hard_limit_mib": 20480,
        "host_reserve_mib": 4096,
        "swap_is_capacity": False,
    }:
        semantic_errors.append("memory aggregation policy does not preserve the 4 GiB reserve")

    child_slices = [execution.get(name, {}) for name in ("runtime", "database", "operations", "edge")]
    if sum(int(item.get("cpu_quota_percent", 0)) for item in child_slices) != 300:
        semantic_errors.append("enabled provider slice hard CPU ceilings must total 300%")
    child_memory = sum(int(item.get("memory_max_mib", 0)) for item in child_slices)
    if child_memory != 16384 or child_memory > 20480:
        semantic_errors.append(
            f"enabled provider slice hard memory must total 16384 MiB within 20480 MiB, got {child_memory}"
        )
    readiness_checks = set(example.get("readiness", {}).get("required_checks", []))
    expected_readiness_checks = {
        "runtime-identities", "runtime-directories", "data-volume-mounted", "swap-disabled",
        "selinux-enforcing", "firewall-policy", "ssh-root-disabled", "ssh-password-auth-disabled",
        "legacy-imds-disabled", "capacity-controls", "runtime-service-units", "edge-fail-closed",
    }
    if readiness_checks != expected_readiness_checks:
        semantic_errors.append(
            f"host readiness checks mismatch: {sorted(readiness_checks)}"
        )

    services = {item["service"]: item for item in example["identities"]["services"]}
    expected_services = {
        "liqi-api": ("liqi-api", 2210),
        "liqi-realtime": ("liqi-realtime", 2211),
        "liqi-worker": ("liqi-worker", 2212),
    }
    if set(services) != set(expected_services):
        semantic_errors.append(
            f"service identities must be exactly {sorted(expected_services)}, got {sorted(services)}"
        )
    else:
        for service, (user, uid) in expected_services.items():
            if services[service]["user"] != user or services[service]["uid"] != uid:
                semantic_errors.append(
                    f"{service} identity must be user={user!r}, uid={uid}"
                )

    required_directories = {
        "/opt/liqi/releases",
        "/opt/liqi",
        "/etc/liqi",
        "/run/liqi/secrets",
        "/run/liqi/secrets/liqi-api",
        "/run/liqi/secrets/liqi-realtime",
        "/run/liqi/secrets/liqi-worker",
        "/var/tmp/liqi/releases",
        "/var/lib/liqi/postgresql/data",
        "/var/lib/liqi/postgresql/backup-staging",
        "/var/log/liqi",
        "/var/tmp/liqi",
    }
    directories = {entry["path"]: entry for entry in example["directories"]}
    missing_directories = required_directories - set(directories)
    if missing_directories:
        semantic_errors.append(f"missing required directories: {sorted(missing_directories)}")
    for path_value, directory in directories.items():
        if int(directory["mode"], 8) & 0o002:
            semantic_errors.append(f"directory {path_value} is world-writable")

    if directories.get("/run/liqi/secrets", {}).get("mode") != "0710":
        semantic_errors.append("/run/liqi/secrets must use mode 0710")

    expected_ports = {
        "edge-http": (80, "public-redirect-only", True),
        "edge-https": (443, "public-edge", True),
        "administration-ssh": (22, "admin-allowlist-only", False),
        "postgresql": (5432, "host-internal-only", False),
        "pgbouncer": (6432, "host-internal-only", False),
        "liqi-api": (8080, "host-internal-only", False),
        "liqi-realtime": (8081, "host-internal-only", False),
        "liqi-worker-admin": (8082, "host-internal-only", False),
        "otel-otlp-grpc": (4317, "host-internal-only", False),
        "otel-otlp-http": (4318, "host-internal-only", False),
    }
    ports = {entry["name"]: entry for entry in example["ports"]}
    if set(ports) != set(expected_ports):
        semantic_errors.append(
            f"port names must be exactly {sorted(expected_ports)}, got {sorted(ports)}"
        )
    else:
        for name, (port, exposure, enabled) in expected_ports.items():
            actual = ports[name]
            if (actual["port"], actual["exposure"], actual["default_enabled"]) != (
                port,
                exposure,
                enabled,
            ):
                semantic_errors.append(
                    f"port contract mismatch for {name}: expected "
                    f"{(port, exposure, enabled)!r}"
                )

    forbidden_key_fragments = (
        "password",
        "private_key",
        "private-key",
        "pem",
        "secret_value",
        "secret-value",
        "access_token",
        "refresh_token",
    )

    def inspect_keys(value: object, path_parts: tuple[str, ...] = ()) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                lowered = key.lower()
                if any(fragment in lowered for fragment in forbidden_key_fragments):
                    semantic_errors.append(
                        "secret-bearing field is forbidden in host output: "
                        + ".".join((*path_parts, key))
                    )
                inspect_keys(child, (*path_parts, key))
        elif isinstance(value, list):
            for index, child in enumerate(value):
                inspect_keys(child, (*path_parts, str(index)))

    inspect_keys(example)

    if semantic_errors:
        for error in semantic_errors:
            print(f"ERROR semantic: {error}", file=sys.stderr)
        return 1

    print(
        "validated contracts/platform/oci-host-v0.example.json "
        "against schema and provider semantics"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
