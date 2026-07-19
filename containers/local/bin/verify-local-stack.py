#!/usr/bin/env python3
"""Verify the local container topology and emit redacted exact-SHA evidence."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def request_json(url: str, *, method: str = "GET", body: dict[str, Any] | None = None, headers: dict[str, str] | None = None) -> tuple[int, dict[str, Any]]:
    payload = None if body is None else json.dumps(body, separators=(",", ":")).encode()
    request_headers = {"accept": "application/json", **(headers or {})}
    if payload is not None:
        request_headers["content-type"] = "application/json"
    request = urllib.request.Request(url, data=payload, headers=request_headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=8) as response:
            return response.status, json.loads(response.read().decode())
    except urllib.error.HTTPError as error:
        data = json.loads(error.read().decode())
        return error.code, data


def compose(compose_file: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["docker", "compose", "--file", str(compose_file), *arguments],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=60,
    )
    return completed.stdout.strip()


def check_http(base_url: str, token: str, source_revision: str) -> dict[str, Any]:
    live_status, live = request_json(base_url + "/health/live")
    ready_status, ready = request_json(base_url + "/health/ready")
    metadata_status, metadata = request_json(base_url + "/platform/v1/metadata")
    if live_status != 200 or live.get("status") != "live":
        raise RuntimeError("live health endpoint failed")
    if ready_status != 200 or ready.get("status") != "ready":
        raise RuntimeError(f"ready health endpoint failed: {ready}")
    checks = {item.get("name"): item.get("status") for item in ready.get("checks", [])}
    if checks.get("database") != "up" or checks.get("native") != "up":
        raise RuntimeError(f"readiness dependencies are not up: {checks}")
    if metadata_status != 200 or metadata.get("sourceRevision") != source_revision:
        raise RuntimeError("runtime metadata is not bound to the exact source revision")
    if metadata.get("beam", {}).get("elixir") != "1.20.2" or metadata.get("beam", {}).get("otp") != "28":
        raise RuntimeError("runtime BEAM versions differ from the pinned toolchain")

    unauthorized_status, _ = request_json(
        base_url + "/platform/v1/probes/native",
        method="POST",
        body={"expectedFirst": 1, "expectedLast": 5, "observedSequences": [1, 3, 5]},
    )
    if unauthorized_status != 401:
        raise RuntimeError("operator probe endpoint did not fail closed without a token")

    native_status, native = request_json(
        base_url + "/platform/v1/probes/native",
        method="POST",
        headers={"x-liqi-probe-token": token},
        body={"expectedFirst": 1, "expectedLast": 5, "observedSequences": [1, 3, 5]},
    )
    configured = native.get("configured", {})
    readiness = native.get("readiness", {})
    if native_status != 200 or native.get("parity") is not True:
        raise RuntimeError(f"native parity probe failed: {native}")
    if configured.get("implementation") != "native" or configured.get("fallback") is not False:
        raise RuntimeError(f"runtime did not execute the real NIF: {configured}")
    if readiness.get("required") is not True or readiness.get("nativeAvailable") is not True:
        raise RuntimeError(f"native-required readiness failed: {readiness}")

    return {"live": live, "ready": ready, "metadata": metadata, "native": native}


def check_durable_probe(base_url: str, token: str) -> dict[str, Any]:
    probe_id = str(uuid.uuid4())
    idempotency_key = "local-container-" + uuid.uuid4().hex
    headers = {"x-liqi-probe-token": token, "idempotency-key": idempotency_key}
    status, accepted = request_json(
        base_url + "/platform/v1/probes",
        method="POST",
        headers=headers,
        body={"clientProbeId": probe_id},
    )
    if status != 202 or accepted.get("probeId") != probe_id or not accepted.get("eventId"):
        raise RuntimeError(f"durable probe was not accepted: {accepted}")

    duplicate_status, duplicate = request_json(
        base_url + "/platform/v1/probes",
        method="POST",
        headers=headers,
        body={"clientProbeId": probe_id},
    )
    if duplicate_status != 202 or duplicate.get("eventId") != accepted.get("eventId"):
        raise RuntimeError("durable probe idempotency did not return the original event")

    event_id = accepted["eventId"]
    query = urllib.parse.urlencode({"eventId": event_id})
    observation: dict[str, Any] = {}
    for _attempt in range(60):
        observed_status, observation = request_json(
            f"{base_url}/platform/v1/probes/{probe_id}?{query}",
            headers={"x-liqi-probe-token": token},
        )
        if observed_status == 200 and observation.get("terminal") is True:
            break
        time.sleep(0.25)
    if observation.get("probeStatus") != "completed" or observation.get("outboxState") != "succeeded":
        raise RuntimeError(f"durable probe did not complete: {observation}")
    if observation.get("effectApplied") is not True or observation.get("terminal") is not True:
        raise RuntimeError(f"durable probe postconditions failed: {observation}")
    return {"accepted": accepted, "duplicate": duplicate, "observation": observation}


def docker_inspect(identifier: str) -> dict[str, Any]:
    output = subprocess.check_output(["docker", "inspect", identifier], text=True, timeout=30)
    return json.loads(output)[0]


def check_control_plane(compose_file: Path) -> dict[str, Any]:
    migration = compose(
        compose_file,
        "exec", "-T", "postgres", "psql", "--no-psqlrc", "--tuples-only", "--no-align",
        "--username=postgres", "--dbname=liqi", "--command=SELECT max(version) FROM platform.schema_migrations",
    )
    if migration != "8":
        raise RuntimeError(f"unexpected database migration version: {migration}")

    role_count = compose(
        compose_file,
        "exec", "-T", "postgres", "psql", "--no-psqlrc", "--tuples-only", "--no-align",
        "--username=postgres", "--dbname=postgres",
        "--command=SELECT count(*) FROM pg_roles WHERE rolname LIKE 'liqi_%'",
    )
    if role_count != "8":
        raise RuntimeError(f"unexpected LIQI role count: {role_count}")

    pool_config = compose(
        compose_file,
        "exec", "-T", "pgbouncer", "psql", "--no-psqlrc", "--tuples-only", "--no-align",
        "--field-separator=|", "--host=127.0.0.1", "--port=6432", "--username=liqi_monitor",
        "--dbname=pgbouncer", "--command=SHOW CONFIG",
    )
    pool_mode = next((line.split("|", 2)[1] for line in pool_config.splitlines() if line.startswith("pool_mode|")), None)
    if pool_mode != "transaction":
        raise RuntimeError(f"pgBouncer pool mode is not transaction: {pool_mode}")

    runtime_id = compose(compose_file, "ps", "--quiet", "runtime")
    pod_id = compose(compose_file, "ps", "--quiet", "pod")
    ingress_id = compose(compose_file, "ps", "--quiet", "ingress")
    postgres_id = compose(compose_file, "ps", "--quiet", "postgres")
    if not all((runtime_id, pod_id, ingress_id, postgres_id)):
        raise RuntimeError("expected local containers are not running")

    runtime = docker_inspect(runtime_id)
    host = runtime["HostConfig"]
    if host.get("ReadonlyRootfs") is not True:
        raise RuntimeError("runtime root filesystem is not read-only")
    if "ALL" not in (host.get("CapDrop") or []):
        raise RuntimeError("runtime Linux capabilities were not dropped")
    if host.get("RestartPolicy", {}).get("Name") != "no":
        raise RuntimeError("runtime restart policy must remain disabled")
    if not 0 < int(host.get("Memory") or 0) <= 768 * 1024 * 1024:
        raise RuntimeError("runtime memory limit is missing or exceeds 768 MiB")
    if not 0 < int(host.get("PidsLimit") or 0) <= 128:
        raise RuntimeError("runtime PID limit is missing or exceeds 128")

    pod = docker_inspect(pod_id)
    if pod.get("HostConfig", {}).get("PortBindings"):
        raise RuntimeError("internal pod namespace must not publish a host port")

    ingress = docker_inspect(ingress_id)
    ingress_host = ingress.get("HostConfig", {})
    bindings = ingress_host.get("PortBindings", {}).get("8080/tcp") or []
    if len(bindings) != 1 or bindings[0].get("HostIp") != "127.0.0.1":
        raise RuntimeError(f"ingress is not bound only to host loopback: {bindings}")
    if ingress_host.get("ReadonlyRootfs") is not True:
        raise RuntimeError("ingress root filesystem is not read-only")
    if "ALL" not in (ingress_host.get("CapDrop") or []):
        raise RuntimeError("ingress Linux capabilities were not dropped")
    if ingress_host.get("RestartPolicy", {}).get("Name") != "no":
        raise RuntimeError("ingress restart policy must remain disabled")
    if not 0 < int(ingress_host.get("Memory") or 0) <= 32 * 1024 * 1024:
        raise RuntimeError("ingress memory limit is missing or exceeds 32 MiB")
    if not 0 < int(ingress_host.get("PidsLimit") or 0) <= 32:
        raise RuntimeError("ingress PID limit is missing or exceeds 32")
    ingress_networks = set(ingress.get("NetworkSettings", {}).get("Networks", {}))
    if len(ingress_networks) != 2 or not any(name.endswith("_backend") for name in ingress_networks) or not any(name.endswith("_edge") for name in ingress_networks):
        raise RuntimeError(f"ingress network attachments differ: {sorted(ingress_networks)}")

    postgres = docker_inspect(postgres_id)
    if postgres.get("HostConfig", {}).get("PortBindings"):
        raise RuntimeError("PostgreSQL must not publish a host port")

    image = docker_inspect(runtime["Image"])
    labels = image.get("Config", {}).get("Labels") or {}
    return {
        "migration_version": int(migration),
        "liqi_role_count": int(role_count),
        "pgbouncer_pool_mode": pool_mode,
        "runtime_image_id": runtime["Image"],
        "runtime_image_revision": labels.get("org.opencontainers.image.revision"),
        "runtime_memory_bytes": host["Memory"],
        "runtime_pids_limit": host["PidsLimit"],
        "ingress_host_ip": bindings[0]["HostIp"],
        "ingress_memory_bytes": ingress_host["Memory"],
        "ingress_pids_limit": ingress_host["PidsLimit"],
        "postgres_host_ports": 0,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--compose-file", required=True, type=Path)
    parser.add_argument("--state-dir", required=True, type=Path)
    parser.add_argument("--source-revision", required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:4100")
    parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if len(args.source_revision) != 40 or any(character not in "0123456789abcdef" for character in args.source_revision):
        raise SystemExit("--source-revision must be an exact lowercase Git SHA")
    token_path = args.state_dir.resolve() / "secrets" / "probe_token"
    if not token_path.is_file() or token_path.is_symlink():
        raise SystemExit("local probe token file is missing")
    token = token_path.read_text(encoding="ascii").strip()
    if len(token) != 64:
        raise SystemExit("local probe token has an invalid shape")

    started_at = utc_now()
    http = check_http(args.base_url.rstrip("/"), token, args.source_revision)
    durable = check_durable_probe(args.base_url.rstrip("/"), token)
    control = check_control_plane(args.compose_file.resolve())
    if control.get("runtime_image_revision") != args.source_revision:
        raise RuntimeError("runtime image label is not bound to the exact source revision")

    document = {
        "schema_version": "liqi.local-container-result/v1",
        "source_revision": args.source_revision,
        "started_at": started_at,
        "completed_at": utc_now(),
        "status": "passed",
        "checks": {
            "live": http["live"].get("status"),
            "ready": http["ready"].get("status"),
            "native_implementation": http["native"].get("configured", {}).get("implementation"),
            "native_parity": http["native"].get("parity"),
            "durable_probe_terminal": durable["observation"].get("terminal"),
            "durable_probe_effect_applied": durable["observation"].get("effectApplied"),
            "durable_probe_outbox_state": durable["observation"].get("outboxState"),
            "migration_version": control["migration_version"],
            "pgbouncer_pool_mode": control["pgbouncer_pool_mode"],
            "liqi_role_count": control["liqi_role_count"],
            "runtime_read_only": True,
            "runtime_capabilities_dropped": True,
            "ingress_host_ip": control["ingress_host_ip"],
            "ingress_read_only": True,
            "ingress_capabilities_dropped": True,
            "postgres_host_ports": control["postgres_host_ports"],
        },
        "runtime": {
            "image_id": control["runtime_image_id"],
            "memory_bytes": control["runtime_memory_bytes"],
            "pids_limit": control["runtime_pids_limit"],
            "metadata": http["metadata"],
        },
        "durable_probe": {
            "probe_id": durable["accepted"].get("probeId"),
            "event_id": durable["accepted"].get("eventId"),
            "idempotent_event_id": durable["duplicate"].get("eventId"),
            "terminal": durable["observation"].get("terminal"),
        },
    }
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.new.{os.getpid()}")
    temporary.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    os.chmod(temporary, 0o600)
    os.replace(temporary, output)
    print(json.dumps({"status": "passed", "output": str(output), "sha256": hashlib.sha256(output.read_bytes()).hexdigest()}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
