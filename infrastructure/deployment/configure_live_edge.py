#!/usr/bin/env python3
"""Render, validate and optionally enable Caddy after health-gated activation."""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import secrets
import shutil
import socket
import ssl
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[2]
HOST = re.compile(r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
CREDENTIAL = re.compile(r"^systemd-credential://([a-z][a-z0-9-]{1,63})$")
HEADER_LIMIT = 32768


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def validate(schema: Path, document: Any, label: str) -> None:
    validator = Draft202012Validator(load(schema), format_checker=FormatChecker())
    failures = sorted(validator.iter_errors(document), key=lambda item: list(item.absolute_path))
    if failures:
        failure = failures[0]
        raise RuntimeError(f"{label} invalid at {list(failure.absolute_path)}: {failure.message}")


def run(argv: list[str], timeout: int = 30) -> str:
    result = subprocess.run(argv, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, timeout=timeout)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or f"command failed: {argv}")
    return result.stdout.strip()


def probe_token(runtime: dict[str, Any], directory: Path) -> str:
    match = CREDENTIAL.fullmatch(runtime["security"]["probeTokenRef"])
    if not match:
        raise RuntimeError("platform probe token must be a systemd credential")
    path = directory / match.group(1)
    if not path.is_file() or path.stat().st_mode & 0o077:
        raise RuntimeError("platform probe token is unavailable or permissions are too broad")
    value = path.read_text(encoding="utf-8").strip()
    if not 16 <= len(value) <= 4096:
        raise RuntimeError("platform probe token length is invalid")
    return value


def websocket_probe(hostname: str, websocket_path: str, token: str) -> None:
    query = urlencode({
        "vsn": "2.0.0",
        "protocolVersion": "1",
        "sessionId": str(uuid.uuid4()),
        "deviceId": str(uuid.uuid4()),
    })
    path = f"{websocket_path}/websocket?{query}"
    key = base64.b64encode(secrets.token_bytes(16)).decode()
    context = ssl.create_default_context()
    with socket.create_connection((hostname, 443), timeout=10) as raw:
        with context.wrap_socket(raw, server_hostname=hostname) as stream:
            request = (
                f"GET {path} HTTP/1.1\r\n"
                f"Host: {hostname}\r\n"
                "Upgrade: websocket\r\n"
                "Connection: Upgrade\r\n"
                "Sec-WebSocket-Version: 13\r\n"
                f"Sec-WebSocket-Key: {key}\r\n"
                f"x-liqi-probe-token: {token}\r\n\r\n"
            ).encode()
            stream.sendall(request)
            response = stream.recv(4096).decode("latin1", errors="replace")
    first = response.splitlines()[0] if response else "empty response"
    if not first.startswith(("HTTP/1.1 101", "HTTP/1.0 101")):
        raise RuntimeError(f"authenticated WebSocket upgrade did not return 101: {first}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hostname", required=True)
    parser.add_argument("--acme-email", required=True)
    parser.add_argument("--activation-evidence", required=True, type=Path)
    parser.add_argument("--runtime-config", required=True, type=Path)
    parser.add_argument("--credential-directory", type=Path, default=Path("/run/liqi/secrets/beam"))
    parser.add_argument("--template", type=Path, default=Path("/usr/local/share/liqi/Caddyfile.v1-live.tftpl"))
    parser.add_argument("--caddyfile", type=Path, default=Path("/etc/caddy/Caddyfile"))
    parser.add_argument("--approval-reference")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--rendered-output", required=True, type=Path)
    parser.add_argument("--evidence-output", required=True, type=Path)
    args = parser.parse_args()

    if not HOST.fullmatch(args.hostname) or not EMAIL.fullmatch(args.acme_email):
        raise SystemExit("valid public hostname and ACME email are required")
    activation = load(args.activation_evidence)
    runtime = load(args.runtime_config)
    validate(ROOT / "contracts/runtime/runtime-config-v1.schema.json", runtime, "runtime config")
    if not (
        activation.get("schema_version") == "liqi.deployment.activation/v1"
        and activation.get("status") == "passed"
        and activation.get("state") == "health-gated"
        and activation.get("traffic_enabled") is False
        and activation.get("release_id") == runtime["releaseId"]
    ):
        raise SystemExit("traffic enablement requires matching passed health-gated activation evidence")
    if args.execute and (os.name != "posix" or os.geteuid() != 0):
        raise SystemExit("traffic mutation requires root on POSIX")
    if args.execute and (not args.approval_reference or len(args.approval_reference.strip()) < 3):
        raise SystemExit("traffic mutation requires cutover approval reference")

    backend = f"127.0.0.1:{runtime['http']['port']}"
    body_limit = int(runtime["requests"]["bodyBytes"])
    websocket_path = runtime["http"]["websocketPath"]
    replacements = {
        "${hostname}": args.hostname,
        "${acme_email}": args.acme_email,
        "${backend_address}": backend,
        "${request_body_limit_bytes}": str(body_limit),
        "${header_limit_bytes}": str(HEADER_LIMIT),
    }
    rendered = args.template.read_text(encoding="utf-8")
    for token, value in replacements.items():
        rendered = rendered.replace(token, value)
    if "${" in rendered:
        raise SystemExit("unresolved Caddy template variable")
    args.rendered_output.parent.mkdir(parents=True, exist_ok=True)
    args.rendered_output.write_text(rendered, encoding="utf-8", newline="\n")
    caddy = shutil.which("caddy")
    if caddy:
        run([caddy, "validate", "--config", str(args.rendered_output)])

    status = "engineering-complete-evidence-pending"
    certificate = "pending"
    websocket = False
    mutation = False
    if args.execute:
        if not caddy:
            raise SystemExit("caddy binary is required")
        token = probe_token(runtime, args.credential_directory)
        backup = args.caddyfile.with_suffix(".pre-cutover")
        shutil.copyfile(args.caddyfile, backup)
        try:
            temporary = args.caddyfile.with_name(f".{args.caddyfile.name}.new.{os.getpid()}")
            shutil.copyfile(args.rendered_output, temporary)
            os.chmod(temporary, 0o640)
            os.replace(temporary, args.caddyfile)
            run([caddy, "reload", "--config", str(args.caddyfile), "--force"])
            https_code = run(["curl", "--fail", "--silent", "--show-error", "--output", "/dev/null", "--write-out", "%{http_code}", f"https://{args.hostname}/health/live"], 60)
            if https_code != "200":
                raise RuntimeError(f"HTTPS liveness returned {https_code}")
            redirect = run(["curl", "--silent", "--show-error", "--output", "/dev/null", "--write-out", "%{http_code}", f"http://{args.hostname}/health/live"], 30)
            if redirect not in {"301", "302", "307", "308"}:
                raise RuntimeError(f"HTTP redirect returned {redirect}")
            websocket_probe(args.hostname, websocket_path, token)
            status = "verified"
            certificate = "valid"
            websocket = True
            mutation = True
        except Exception:
            shutil.copyfile(backup, args.caddyfile)
            run([caddy, "reload", "--config", str(args.caddyfile), "--force"])
            raise

    evidence = {
        "schema_version": "liqi.deployment.live-endpoint/v1",
        "environment": "v1-live",
        "classification": "production-shaped-development",
        "git_sha": activation["git_sha"],
        "release_id": activation["release_id"],
        "hostname": args.hostname,
        "public_ports": [80, 443],
        "tls": {"mode": "public-acme", "issuer": "Caddy ACME issuer", "certificate_status": certificate, "renewal_command": ["caddy", "reload", "--config", "/etc/caddy/Caddyfile"]},
        "backend": {"address": backend, "publicly_routable": False},
        "websocket": {"path": f"{websocket_path}/websocket", "supported": websocket, "resume_evidence": "pending"},
        "health": {"public_path": "/health/live", "public_detail_level": "binary-no-dependency-detail", "internal_path": "/health/ready"},
        "security": {"admin_api_public": False, "http_redirect": True, "request_body_limit_bytes": body_limit, "header_limit_bytes": HEADER_LIMIT, "security_headers": ["Strict-Transport-Security", "X-Content-Type-Options", "Referrer-Policy", "Content-Security-Policy"]},
        "status": status,
        "observed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "evidence_class": "live-approved" if args.execute else "source-only",
    }
    schema_path = ROOT / "contracts/deployment/live-endpoint-v1.schema.json"
    validate(schema_path, evidence, "live endpoint evidence")
    args.evidence_output.parent.mkdir(parents=True, exist_ok=True)
    args.evidence_output.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({"status": status, "hostname": args.hostname, "traffic_mutation_performed": mutation}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
