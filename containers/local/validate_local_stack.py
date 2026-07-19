#!/usr/bin/env python3
"""Fail-closed source validation for the bounded local container topology."""
from __future__ import annotations

import json
import os
import re
import stat
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[2]
LOCAL = ROOT / "containers" / "local"
DIGEST_IMAGE = re.compile(r"^[a-z0-9./_-]+:[A-Za-z0-9._-]+@sha256:[0-9a-f]{64}$")


def load_yaml(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def validate() -> list[str]:
    failures: list[str] = []
    compose = load_yaml(LOCAL / "compose.yaml")
    compose_text = (LOCAL / "compose.yaml").read_text(encoding="utf-8")
    services = compose.get("services", {})
    expected = {"postgres", "db-init", "pod", "ingress", "pgbouncer", "runtime"}
    if set(services) != expected:
        failures.append(f"local compose service set differs: {sorted(services)}")

    for service_name in expected:
        service = services.get(service_name, {})
        if service.get("restart") != "no":
            failures.append(f"{service_name} must keep restart policy disabled")
        if not service.get("mem_limit") or not service.get("cpus") or not service.get("pids_limit"):
            failures.append(f"{service_name} is missing a memory, CPU, or PID limit")

    for service_name in ("postgres", "db-init"):
        image = services.get(service_name, {}).get("image", "")
        if not DIGEST_IMAGE.fullmatch(image):
            failures.append(f"{service_name} image is not version-and-digest pinned: {image}")
    if services.get("postgres", {}).get("ports"):
        failures.append("PostgreSQL must not publish a host port")
    if compose.get("networks", {}).get("backend", {}).get("internal") is not True:
        failures.append("backend network must remain internal")
    runtime_service = services.get("runtime", {})
    if runtime_service.get("network_mode") != "service:pod":
        failures.append("runtime must share the pod loopback namespace")
    if runtime_service.get("group_add"):
        failures.append("runtime must not receive supplemental host groups")
    if services.get("pgbouncer", {}).get("network_mode") != "service:pod":
        failures.append("pgBouncer must share the pod loopback namespace")
    if services.get("pod", {}).get("ports"):
        failures.append("internal pod namespace must not publish a host port")
    ingress = services.get("ingress", {})
    ingress_ports = ingress.get("ports", [])
    if len(ingress_ports) != 1 or not str(ingress_ports[0]).startswith("127.0.0.1:"):
        failures.append("ingress must publish exactly one host-loopback port")
    if set(ingress.get("networks") or []) != {"backend", "edge"}:
        failures.append("ingress must be the only dual-network proxy")
    if compose.get("networks", {}).get("edge", {}).get("internal") is True:
        failures.append("edge network must permit Docker host port publishing")
    ingress_command = ingress.get("command") or []
    if ingress_command != [
        "TCP-LISTEN:8080,reuseaddr,fork,bind=0.0.0.0",
        "TCP:pod:8080",
    ]:
        failures.append("ingress must proxy only to the internal pod gateway")

    for service_name in ("pod", "ingress", "pgbouncer", "runtime", "db-init"):
        service = services.get(service_name, {})
        if service.get("read_only") is not True:
            failures.append(f"{service_name} root filesystem must be read-only")
        if "ALL" not in (service.get("cap_drop") or []):
            failures.append(f"{service_name} must drop all Linux capabilities")
        if "no-new-privileges:true" not in (service.get("security_opt") or []):
            failures.append(f"{service_name} must enable no-new-privileges")

    runtime = json.loads((LOCAL / "config" / "runtime-v1.json").read_text(encoding="utf-8"))
    schema = json.loads((ROOT / "contracts" / "runtime" / "runtime-config-v1.schema.json").read_text(encoding="utf-8"))
    errors = list(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(runtime))
    failures.extend(f"runtime config schema: {error.message}" for error in errors)
    if runtime.get("environment") != "production":
        failures.append("local runtime config must exercise production validation")
    if runtime.get("native", {}).get("mode") != "required":
        failures.append("local runtime must require the real NIF")
    if runtime.get("oban", {}).get("enabled") is not True:
        failures.append("local runtime must exercise Oban")
    if not all(runtime.get("features", {}).get(name) is True for name in ("persistence", "realtimeDispatcher", "outboxWorker")):
        failures.append("local runtime must exercise all durable/realtime features")

    bundle = json.loads((LOCAL / "config" / "database-role-urls.local.json").read_text(encoding="utf-8"))
    if set(bundle) != {"command", "realtime", "worker"}:
        failures.append("local database role bundle has an invalid key set")
    for role, url in bundle.items():
        if not url.startswith("postgresql://liqi_") or "@127.0.0.1:6432/liqi" not in url:
            failures.append(f"local role URL is not loopback pgBouncer: {role}")

    pgbouncer = (LOCAL / "config" / "pgbouncer.ini").read_text(encoding="utf-8")
    for token in ("listen_addr = 127.0.0.1", "pool_mode = transaction", "auth_type = trust"):
        if token not in pgbouncer:
            failures.append(f"local pgBouncer invariant missing: {token}")

    up_script = (LOCAL / "bin" / "up.sh").read_text(encoding="utf-8")
    secret_materializer = (LOCAL / "bin" / "materialize-secrets.py").read_text(encoding="utf-8")
    runtime_dockerfile = (LOCAL / "Dockerfile.runtime").read_text(encoding="utf-8")
    for token in (
        "SECRET_MODE = 0o640",
        "RUNTIME_GID = 10001",
        "os.chown(path, -1, RUNTIME_GID)",
        "mode=0o700",
        '"gid"',
        '"mode"',
    ):
        if token not in secret_materializer:
            failures.append(f"local secret materializer invariant missing: {token}")
    if "USER 10001:10001" not in runtime_dockerfile:
        failures.append("local runtime user/group must remain 10001:10001")
    if "LIQI_LOCAL_SECRET_GID" in compose_text:
        failures.append("local Compose must not derive or add a host supplemental group")
    for service_name in ("pgbouncer", "runtime"):
        command = f"compose up --detach --no-deps {service_name}"
        if command not in up_script:
            failures.append(
                f"{service_name} startup must not rerun completed one-shot dependencies"
            )

    sidecar_dockerfile = (LOCAL / "Dockerfile.sidecars").read_text(encoding="utf-8")
    local_match = re.search(r"pgbouncer=(\d+\.\d+\.\d+)-r\d+", sidecar_dockerfile)
    production_text = (
        ROOT / "infrastructure" / "packages" / "oracle-linux-9-aarch64-v1.json"
    ).read_text(encoding="utf-8")
    production_match = re.search(r'"pgbouncer-(\d+\.\d+\.\d+)"', production_text)
    if local_match is None or production_match is None:
        failures.append("PgBouncer version pins are not parseable")
    else:
        local_version = tuple(int(part) for part in local_match.group(1).split("."))
        production_version = production_match.group(1)
        if local_match.group(1) != production_version:
            failures.append(
                f"local PgBouncer {local_match.group(1)} differs from production {production_version}"
            )
        if "transaction_timeout" in pgbouncer and local_version < (1, 25, 0):
            failures.append("transaction_timeout requires PgBouncer 1.25.0 or newer")

    runtime_dockerfile = (LOCAL / "Dockerfile.runtime").read_text(encoding="utf-8")
    config_copy = runtime_dockerfile.find("COPY beam/config beam/config")
    deps_get = runtime_dockerfile.find("mix deps.get --only prod --locked")
    if config_copy < 0 or deps_get < 0 or config_copy > deps_get:
        failures.append("runtime Docker dependency layer must copy beam/config before deps.get")

    for dockerfile in (LOCAL / "Dockerfile.runtime", LOCAL / "Dockerfile.sidecars"):
        text = dockerfile.read_text(encoding="utf-8")
        if ":latest" in text or "FROM latest" in text:
            failures.append(f"floating latest tag is forbidden: {dockerfile}")
        for line in text.splitlines():
            if line.startswith("ARG ") and "_IMAGE=" in line and "@sha256:" not in line:
                failures.append(f"base image argument lacks a digest: {line}")

    for script in (LOCAL / "bin").iterdir():
        if os.name == "posix" and script.suffix in {".sh", ".py"} and not script.stat().st_mode & stat.S_IXUSR:
            failures.append(f"local container script is not executable: {script.name}")
    return failures


def main() -> int:
    failures = validate()
    if failures:
        for failure in failures:
            print(failure)
        return 1
    print(json.dumps({"validation": "local-container-source-v1", "services": 6, "status": "passed"}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
