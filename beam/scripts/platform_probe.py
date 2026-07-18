#!/usr/bin/env python3
"""Bounded live HTTPS/Phoenix WebSocket probe for the LIQI V1 runtime."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import secrets
import shutil
import socket
import ssl
import struct
import subprocess
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[2]
GIT_EXECUTABLE = shutil.which("git")
CHECK_ORDER = [
    "https-health",
    "runtime-readiness",
    "websocket-connect",
    "websocket-auth",
    "durable-command-commit",
    "outbox-handoff",
    "realtime-delivery",
    "resume-gap-repair",
    "worker-job",
    "native-kernel",
    "native-fallback",
]
CHECK_OWNERS = {
    "https-health": "Senior 4",
    "runtime-readiness": "Senior 1",
    "websocket-connect": "Senior 1",
    "websocket-auth": "Senior 1",
    "durable-command-commit": "Senior 2",
    "outbox-handoff": "Senior 2",
    "realtime-delivery": "Senior 1",
    "resume-gap-repair": "Senior 1",
    "worker-job": "Senior 2",
    "native-kernel": "Senior 3",
    "native-fallback": "Senior 3",
}
RELEASE_ID = re.compile(r"^liqi-v1-[a-z0-9][a-z0-9._-]{2,95}$")
MAX_HTTP_BODY = 1_048_576
MAX_WS_FRAME = 1_048_576


class ProbeFailure(RuntimeError):
    def __init__(self, failure_class: str, message: str, *, blocked: bool = False):
        super().__init__(message)
        self.failure_class = failure_class
        self.blocked = blocked


class WebSocketHandshakeError(ProbeFailure):
    def __init__(self, status: int | None, message: str):
        super().__init__("websocket_handshake_rejected", message)
        self.status = status



class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        raise HTTPError(req.full_url, code, "redirects are forbidden", headers, fp)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def monotonic_ms() -> int:
    return round(time.monotonic() * 1000)


def git_sha() -> str:
    if not GIT_EXECUTABLE:
        return "0" * 40
    try:
        value = subprocess.check_output(
            [GIT_EXECUTABLE, "rev-parse", "HEAD"],
            cwd=ROOT,
            text=True,
            timeout=10,
        ).strip()
    except (OSError, subprocess.SubprocessError):
        return "0" * 40
    if len(value) == 40 and all(character in "0123456789abcdef" for character in value):
        return value
    return "0" * 40


def sanitize_error(error: BaseException, secrets_to_redact: tuple[str, ...] = ()) -> str:
    value = f"{type(error).__name__}: {error}"
    for secret in secrets_to_redact:
        if secret:
            value = value.replace(secret, "<redacted>")
    return value.replace("\r", " ").replace("\n", " ")[:1000]


def _read_token_file(path: Path) -> str:
    if not path.is_absolute() or not path.is_file():
        raise ProbeFailure(
            "probe_auth_token_missing", "probe token materialized file is unavailable", blocked=True
        )
    try:
        payload = path.read_bytes()
    except OSError as error:
        raise ProbeFailure(
            "probe_auth_token_missing", "probe token materialized file is unavailable", blocked=True
        ) from error
    if not 1 <= len(payload) <= 8193 or b"\x00" in payload:
        raise ProbeFailure(
            "probe_auth_token_invalid", "probe token materialized file is invalid", blocked=True
        )
    try:
        token = payload.decode("utf-8").rstrip("\r\n")
    except UnicodeDecodeError as error:
        raise ProbeFailure(
            "probe_auth_token_invalid", "probe token materialized file is not UTF-8", blocked=True
        ) from error
    return _validate_probe_token(token)


def _validate_probe_token(token: str) -> str:
    if not 1 <= len(token.encode("utf-8")) <= 8192 or "\r" in token or "\n" in token:
        raise ProbeFailure(
            "probe_auth_token_invalid", "probe token must be one bounded UTF-8 line", blocked=True
        )
    return token


def load_probe_token() -> str:
    reference = os.environ.get("LIQI_PROBE_AUTH_TOKEN_REF")
    if reference:
        if reference.startswith("file://"):
            return _read_token_file(Path(reference.removeprefix("file://")))
        if reference.startswith("systemd-credential://"):
            name = reference.removeprefix("systemd-credential://")
            if not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", name):
                raise ProbeFailure(
                    "probe_auth_token_reference_invalid",
                    "invalid systemd credential name",
                    blocked=True,
                )
            directory = os.environ.get("CREDENTIALS_DIRECTORY") or os.environ.get(
                "LIQI_CREDENTIALS_DIRECTORY"
            )
            if not directory:
                raise ProbeFailure(
                    "probe_auth_token_missing",
                    "credential directory is unavailable for the probe token reference",
                    blocked=True,
                )
            return _read_token_file(Path(directory) / name)
        if reference.startswith("env://"):
            name = reference.removeprefix("env://")
            if not re.fullmatch(r"[A-Z][A-Z0-9_]{1,63}", name):
                raise ProbeFailure(
                    "probe_auth_token_reference_invalid",
                    "invalid probe token environment reference",
                    blocked=True,
                )
            value = os.environ.get(name)
            if value is None:
                raise ProbeFailure(
                    "probe_auth_token_missing",
                    "probe token reference environment value is unavailable",
                    blocked=True,
                )
            return _validate_probe_token(value)
        raise ProbeFailure(
            "probe_auth_token_reference_invalid",
            "probe token reference must use file, systemd-credential, or env",
            blocked=True,
        )
    inline = os.environ.get("LIQI_PROBE_AUTH_TOKEN")
    if inline is None:
        raise ProbeFailure(
            "probe_auth_token_missing",
            "LIQI_PROBE_AUTH_TOKEN_REF is required for the live platform probe",
            blocked=True,
        )
    return _validate_probe_token(inline)


def normalize_base_url(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in ("", "/")
    ):
        raise ValueError("--base-url must be an origin-only HTTPS URL without credentials")
    port = f":{parsed.port}" if parsed.port and parsed.port != 443 else ""
    return urlunsplit(("https", parsed.hostname + port, "", "", ""))


@dataclass
class HttpResponse:
    status: int
    headers: dict[str, str]
    body: bytes

    def json(self) -> dict[str, Any]:
        try:
            value = json.loads(self.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ProbeFailure("invalid_json_response", "endpoint returned invalid JSON") from error
        if not isinstance(value, dict):
            raise ProbeFailure("invalid_json_response", "endpoint JSON must be an object")
        return value


class HttpClient:
    def __init__(self, recorder: "Recorder", timeout: float = 8.0):
        self.recorder = recorder
        self.timeout = timeout
        context = ssl.create_default_context()
        self.opener = build_opener(NoRedirect(), HTTPSHandler(context=context))

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        body: dict[str, Any] | None = None,
    ) -> HttpResponse:
        data = None
        request_headers = {
            "accept": "application/json",
            "user-agent": "liqi-platform-probe/1",
            **(headers or {}),
        }
        if body is not None:
            data = json.dumps(body, separators=(",", ":")).encode("utf-8")
            request_headers["content-type"] = "application/json"
        request = Request(url, data=data, headers=request_headers, method=method)
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                payload = response.read(MAX_HTTP_BODY + 1)
                if len(payload) > MAX_HTTP_BODY:
                    raise ProbeFailure("http_body_too_large", "endpoint response exceeded 1 MiB")
                headers_value = {key.lower(): value for key, value in response.headers.items()}
                self.recorder.inspect_secrets(payload)
                self.recorder.inspect_secrets("\n".join(f"{key}:{value}" for key, value in headers_value.items()))
                return HttpResponse(response.status, headers_value, payload)
        except HTTPError as error:
            payload = error.read(MAX_HTTP_BODY + 1)
            headers_value = {key.lower(): value for key, value in error.headers.items()}
            bounded_payload = payload[:MAX_HTTP_BODY]
            self.recorder.inspect_secrets(bounded_payload)
            self.recorder.inspect_secrets("\n".join(f"{key}:{value}" for key, value in headers_value.items()))
            return HttpResponse(error.code, headers_value, bounded_payload)
        except (URLError, TimeoutError, socket.timeout, ssl.SSLError, OSError) as error:
            raise ProbeFailure("https_unreachable", "HTTPS endpoint is unreachable") from error


class WebSocket:
    def __init__(self, sock: ssl.SSLSocket, initial: bytes = b"", recorder: "Recorder | None" = None):
        self.sock = sock
        self.buffer = bytearray(initial)
        self.closed = False
        self.recorder = recorder

    @classmethod
    def connect(
        cls,
        base_url: str,
        session_id: str,
        device_id: str,
        *,
        auth_token: str | None,
        timeout: float = 8.0,
        recorder: "Recorder | None" = None,
    ) -> "WebSocket":
        parsed = urlsplit(base_url)
        host = parsed.hostname or ""
        port = parsed.port or 443
        query = urlencode(
            {
                "vsn": "2.0.0",
                "protocolVersion": "1",
                "sessionId": session_id,
                "deviceId": device_id,
            }
        )
        path = f"/platform/v1/socket/websocket?{query}"
        try:
            raw = socket.create_connection((host, port), timeout=timeout)
        except OSError as error:
            raise ProbeFailure(
                "websocket_unreachable", "WebSocket endpoint is unreachable", blocked=True
            ) from error
        try:
            tls = ssl.create_default_context().wrap_socket(raw, server_hostname=host)
        except (OSError, ssl.SSLError) as error:
            raw.close()
            raise ProbeFailure(
                "websocket_tls_failed", "WebSocket TLS handshake failed", blocked=True
            ) from error
        tls.settimeout(timeout)
        key = base64.b64encode(secrets.token_bytes(16)).decode("ascii")
        headers = [
            f"GET {path} HTTP/1.1",
            f"Host: {host}{':' + str(port) if port != 443 else ''}",
            "Upgrade: websocket",
            "Connection: Upgrade",
            f"Sec-WebSocket-Key: {key}",
            "Sec-WebSocket-Version: 13",
            f"Origin: {base_url}",
            "User-Agent: liqi-platform-probe/1",
        ]
        if auth_token is not None:
            headers.append(f"x-liqi-probe-token: {auth_token}")
        request = ("\r\n".join(headers) + "\r\n\r\n").encode("ascii")
        tls.sendall(request)
        response = bytearray()
        while b"\r\n\r\n" not in response:
            if len(response) > 32_768:
                tls.close()
                raise WebSocketHandshakeError(None, "WebSocket handshake headers exceeded 32 KiB")
            chunk = tls.recv(4096)
            if not chunk:
                tls.close()
                raise WebSocketHandshakeError(None, "WebSocket handshake closed early")
            response.extend(chunk)
        if recorder is not None:
            recorder.inspect_secrets(bytes(response))
        header_bytes, initial = bytes(response).split(b"\r\n\r\n", 1)
        try:
            lines = header_bytes.decode("iso-8859-1").split("\r\n")
            status = int(lines[0].split(" ", 2)[1])
            response_headers = {}
            for line in lines[1:]:
                name, value = line.split(":", 1)
                response_headers[name.strip().lower()] = value.strip()
        except (ValueError, IndexError) as error:
            tls.close()
            raise WebSocketHandshakeError(None, "invalid WebSocket handshake response") from error
        if status != 101:
            tls.close()
            raise WebSocketHandshakeError(status, f"WebSocket handshake returned HTTP {status}")
        expected = base64.b64encode(
            hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode("ascii")).digest()
        ).decode("ascii")
        if response_headers.get("sec-websocket-accept") != expected:
            tls.close()
            raise WebSocketHandshakeError(status, "invalid Sec-WebSocket-Accept")
        if response_headers.get("upgrade", "").lower() != "websocket":
            tls.close()
            raise WebSocketHandshakeError(status, "missing WebSocket upgrade response")
        connection_tokens = {
            item.strip().lower()
            for item in response_headers.get("connection", "").split(",")
            if item.strip()
        }
        if "upgrade" not in connection_tokens:
            tls.close()
            raise WebSocketHandshakeError(status, "missing Connection: Upgrade response")
        return cls(tls, initial, recorder)

    def send_text(self, value: str) -> None:
        self._send_frame(0x1, value.encode("utf-8"))

    def send_json(self, value: Any) -> None:
        self.send_text(json.dumps(value, separators=(",", ":")))

    def recv_json(self, timeout: float) -> list[Any]:
        payload = self.recv_text(timeout)
        try:
            value = json.loads(payload)
        except json.JSONDecodeError as error:
            raise ProbeFailure("invalid_websocket_json", "WebSocket frame contained invalid JSON") from error
        if not isinstance(value, list) or len(value) < 5:
            raise ProbeFailure("invalid_phoenix_frame", "Phoenix frame must be a five-element array")
        return value

    def recv_text(self, timeout: float) -> str:
        deadline = time.monotonic() + timeout
        fragments = bytearray()
        text_started = False
        while True:
            opcode, final, payload = self._recv_frame(deadline)
            if opcode == 0x8:
                self.closed = True
                raise ProbeFailure("websocket_closed", "WebSocket closed before expected event")
            if opcode == 0x9:
                self._send_frame(0xA, payload)
                continue
            if opcode == 0xA:
                continue
            if opcode == 0x1:
                text_started = True
                fragments.extend(payload)
            elif opcode == 0x0 and text_started:
                fragments.extend(payload)
            else:
                raise ProbeFailure("unsupported_websocket_frame", f"unsupported opcode {opcode}")
            if len(fragments) > MAX_WS_FRAME:
                raise ProbeFailure("websocket_frame_too_large", "WebSocket message exceeded 1 MiB")
            if final:
                try:
                    decoded = fragments.decode("utf-8")
                    if self.recorder is not None:
                        self.recorder.inspect_secrets(decoded)
                    return decoded
                except UnicodeDecodeError as error:
                    raise ProbeFailure("invalid_websocket_utf8", "WebSocket text was not UTF-8") from error

    def close(self) -> None:
        if self.closed:
            return
        try:
            self._send_frame(0x8, struct.pack("!H", 1000))
        except (OSError, ProbeFailure):
            pass
        self.closed = True
        try:
            self.sock.close()
        except OSError:
            pass

    def _send_frame(self, opcode: int, payload: bytes) -> None:
        if self.closed:
            raise ProbeFailure("websocket_closed", "cannot send on a closed WebSocket")
        if len(payload) > MAX_WS_FRAME:
            raise ProbeFailure("websocket_frame_too_large", "outbound WebSocket frame exceeded 1 MiB")
        first = 0x80 | opcode
        mask = secrets.token_bytes(4)
        length = len(payload)
        if length < 126:
            header = bytes([first, 0x80 | length])
        elif length <= 0xFFFF:
            header = bytes([first, 0x80 | 126]) + struct.pack("!H", length)
        else:
            header = bytes([first, 0x80 | 127]) + struct.pack("!Q", length)
        masked = bytes(value ^ mask[index % 4] for index, value in enumerate(payload))
        self.sock.sendall(header + mask + masked)

    def _recv_frame(self, deadline: float) -> tuple[int, bool, bytes]:
        first_two = self._read_exact(2, deadline)
        first, second = first_two
        final = bool(first & 0x80)
        if first & 0x70:
            raise ProbeFailure("websocket_protocol_error", "server frame set unsupported RSV bits")
        opcode = first & 0x0F
        masked = bool(second & 0x80)
        if masked:
            raise ProbeFailure(
                "websocket_server_masked", "server WebSocket frames must not be masked"
            )
        length = second & 0x7F
        if length == 126:
            length = struct.unpack("!H", self._read_exact(2, deadline))[0]
        elif length == 127:
            length = struct.unpack("!Q", self._read_exact(8, deadline))[0]
        if opcode >= 0x8 and (not final or length > 125):
            raise ProbeFailure(
                "websocket_protocol_error", "invalid fragmented or oversized control frame"
            )
        if length > MAX_WS_FRAME:
            raise ProbeFailure("websocket_frame_too_large", "inbound WebSocket frame exceeded 1 MiB")
        payload = self._read_exact(length, deadline)
        return opcode, final, payload

    def _read_exact(self, size: int, deadline: float) -> bytes:
        while len(self.buffer) < size:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ProbeFailure("websocket_timeout", "WebSocket step exceeded its deadline")
            self.sock.settimeout(remaining)
            try:
                chunk = self.sock.recv(max(4096, size - len(self.buffer)))
            except socket.timeout as error:
                raise ProbeFailure("websocket_timeout", "WebSocket step exceeded its deadline") from error
            if not chunk:
                raise ProbeFailure("websocket_closed", "WebSocket closed unexpectedly")
            self.buffer.extend(chunk)
        result = bytes(self.buffer[:size])
        del self.buffer[:size]
        return result


class PhoenixChannel:
    def __init__(self, websocket: WebSocket, topic: str = "platform:v1"):
        self.websocket = websocket
        self.topic = topic
        self.join_ref = "1"
        self.next_ref = 1
        self.pending: list[list[Any]] = []

    def join(self, payload: dict[str, Any], timeout: float = 10.0) -> dict[str, Any]:
        ref = self._new_ref()
        self.websocket.send_json([self.join_ref, ref, self.topic, "phx_join", payload])
        reply = self._wait_reply(ref, timeout)
        if reply.get("status") != "ok":
            raise ProbeFailure("phoenix_join_rejected", f"Phoenix join rejected: {reply.get('status')}")
        response = reply.get("response")
        if not isinstance(response, dict):
            raise ProbeFailure("invalid_phoenix_reply", "Phoenix join response must be an object")
        return response

    def push(self, event: str, payload: dict[str, Any], timeout: float = 10.0) -> dict[str, Any]:
        ref = self._new_ref()
        self.websocket.send_json([self.join_ref, ref, self.topic, event, payload])
        reply = self._wait_reply(ref, timeout)
        if reply.get("status") != "ok":
            raise ProbeFailure("phoenix_push_rejected", f"Phoenix {event} rejected: {reply.get('status')}")
        response = reply.get("response")
        return response if isinstance(response, dict) else {}

    def wait_event(
        self, event: str, predicate: Callable[[dict[str, Any]], bool], timeout: float
    ) -> dict[str, Any]:
        message = self._wait_message(
            lambda item: item[2] == self.topic
            and item[3] == event
            and isinstance(item[4], dict)
            and predicate(item[4]),
            timeout,
        )
        return message[4]

    def _wait_reply(self, ref: str, timeout: float) -> dict[str, Any]:
        message = self._wait_message(
            lambda item: item[1] == ref and item[2] == self.topic and item[3] == "phx_reply",
            timeout,
        )
        payload = message[4]
        if not isinstance(payload, dict):
            raise ProbeFailure("invalid_phoenix_reply", "Phoenix reply payload must be an object")
        return payload

    def _wait_message(self, predicate: Callable[[list[Any]], bool], timeout: float) -> list[Any]:
        deadline = time.monotonic() + timeout
        for index, message in enumerate(self.pending):
            if predicate(message):
                return self.pending.pop(index)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ProbeFailure("phoenix_timeout", "Phoenix channel step exceeded its deadline")
            message = self.websocket.recv_json(remaining)
            if predicate(message):
                return message
            self.pending.append(message)
            if len(self.pending) > 256:
                raise ProbeFailure("phoenix_pending_overflow", "Phoenix pending frame buffer exceeded 256")

    def _new_ref(self) -> str:
        value = str(self.next_ref)
        self.next_ref += 1
        return value


class Recorder:
    def __init__(self, secrets_to_redact: tuple[str, ...] = ()):
        self.secrets_to_redact = tuple(secret for secret in secrets_to_redact if secret)
        self.checks = {
            name: {
                "name": name,
                "owner": CHECK_OWNERS[name],
                "status": "blocked",
                "duration_ms": 0,
                "evidence_ref": None,
                "failure_class": "not_executed",
            }
            for name in CHECK_ORDER
        }
        self.errors: list[str] = []
        self.correctness = {
            "authorization_bypass": 0,
            "secret_exposure": 0,
            "duplicate_durable_identity": 0,
            "event_before_commit": 0,
            "durable_event_loss": 0,
        }


    def inspect_secrets(self, value: bytes | str) -> None:
        if not self.secrets_to_redact:
            return
        text = value.decode("utf-8", errors="ignore") if isinstance(value, bytes) else value
        for secret in self.secrets_to_redact:
            if secret in text:
                self.correctness["secret_exposure"] += 1
                raise ProbeFailure(
                    "secret_exposure", "live response reflected protected probe material"
                )

    def run(self, name: str, evidence_ref: str, function: Callable[[], Any]) -> Any:
        started = monotonic_ms()
        try:
            value = function()
        except ProbeFailure as error:
            self.set(
                name,
                "blocked" if error.blocked else "failed",
                monotonic_ms() - started,
                evidence_ref,
                error.failure_class,
            )
            self.errors.append(sanitize_error(error, self.secrets_to_redact))
            raise
        except Exception as error:
            self.set(
                name,
                "failed",
                monotonic_ms() - started,
                evidence_ref,
                "unexpected_probe_error",
            )
            self.errors.append(sanitize_error(error, self.secrets_to_redact))
            raise ProbeFailure("unexpected_probe_error", "unexpected probe exception") from error
        self.set(name, "passed", monotonic_ms() - started, evidence_ref, None)
        return value

    def set(
        self,
        name: str,
        status: str,
        duration_ms: int,
        evidence_ref: str | None,
        failure_class: str | None,
    ) -> None:
        self.checks[name] = {
            "name": name,
            "owner": CHECK_OWNERS[name],
            "status": status,
            "duration_ms": max(0, min(duration_ms, 600_000)),
            "evidence_ref": evidence_ref,
            "failure_class": failure_class,
        }

    def block_remaining(self, names: list[str], failure_class: str) -> None:
        for name in names:
            if self.checks[name]["failure_class"] == "not_executed":
                self.set(name, "blocked", 0, None, failure_class)

    def status(self) -> str:
        if any(value > 0 for value in self.correctness.values()):
            return "failed"
        statuses = {item["status"] for item in self.checks.values()}
        if "failed" in statuses:
            return "failed"
        if "blocked" in statuses:
            return "blocked"
        return "passed"

    def ordered_checks(self) -> list[dict[str, Any]]:
        return [self.checks[name] for name in CHECK_ORDER]


def require_status(response: HttpResponse, expected: int, failure_class: str) -> dict[str, Any]:
    if response.status != expected:
        blocked = response.status == 503
        code = None
        try:
            code = response.json().get("error", {}).get("code")
        except ProbeFailure:
            pass
        detail = f"expected HTTP {expected}, received {response.status}"
        if code:
            detail += f" ({code})"
        raise ProbeFailure(failure_class, detail, blocked=blocked)
    return response.json()


def wait_for_terminal(
    client: HttpClient,
    base_url: str,
    token: str,
    probe_id: str,
    event_id: str,
    timeout: float = 20.0,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    url = f"{base_url}/platform/v1/probes/{probe_id}?{urlencode({'eventId': event_id})}"
    while True:
        response = client.request("GET", url, headers={"x-liqi-probe-token": token})
        document = require_status(response, 200, "probe_observation_unavailable")
        if document.get("terminal") is True:
            return document
        if time.monotonic() >= deadline:
            raise ProbeFailure("worker_terminal_timeout", "probe did not reach terminal effect within 20 seconds")
        time.sleep(0.25)


def post_probe(
    client: HttpClient, base_url: str, token: str, probe_id: str, idempotency_key: str
) -> dict[str, Any]:
    response = client.request(
        "POST",
        f"{base_url}/platform/v1/probes",
        headers={
            "x-liqi-probe-token": token,
            "idempotency-key": idempotency_key,
            "x-request-id": str(uuid.uuid4()),
            "x-liqi-deadline-ms": "5000",
        },
        body={"clientProbeId": probe_id},
    )
    return require_status(response, 202, "durable_command_unavailable")



def run_live_probe(base_url: str, release_id: str, token: str, recorder: Recorder) -> tuple[str, str]:
    client = HttpClient(recorder)
    observed_release = release_id
    environment = os.environ.get("LIQI_ENVIRONMENT", "staging")
    source_sha = git_sha()

    live = recorder.run(
        "https-health",
        "/health/live",
        lambda: require_status(client.request("GET", f"{base_url}/health/live"), 200, "https_health_failed"),
    )
    observed_release = str(live.get("releaseId") or release_id)
    if live.get("status") != "live" or observed_release != release_id:
        recorder.set("https-health", "failed", recorder.checks["https-health"]["duration_ms"], "/health/live", "release_identity_mismatch")
        raise ProbeFailure("release_identity_mismatch", "liveness release identity does not match requested release")

    def readiness_step() -> tuple[dict[str, Any], dict[str, Any]]:
        metadata = require_status(
            client.request("GET", f"{base_url}/platform/v1/metadata"),
            200,
            "runtime_metadata_failed",
        )
        ready = require_status(
            client.request("GET", f"{base_url}/health/ready"), 200, "runtime_not_ready"
        )
        if metadata.get("releaseId") != release_id or ready.get("releaseId") != release_id:
            raise ProbeFailure("release_identity_mismatch", "runtime metadata/readiness release mismatch")
        if metadata.get("sourceRevision") != source_sha:
            raise ProbeFailure("source_revision_mismatch", "live source revision does not match probe checkout")
        if metadata.get("environment") not in ("staging", "production"):
            raise ProbeFailure("environment_mismatch", "live environment is not staging or production")
        if ready.get("status") != "ready":
            raise ProbeFailure("runtime_not_ready", "runtime readiness is not green", blocked=True)
        return metadata, ready

    metadata, _ready = recorder.run("runtime-readiness", "/health/ready", readiness_step)
    observed_release = metadata["releaseId"]
    environment = metadata["environment"]

    session_id = str(uuid.uuid4())
    device_id = str(uuid.uuid4())
    first_probe = str(uuid.uuid4())
    first_actor = f"platform-probe:{first_probe}"

    def unauthorized_step() -> None:
        unauthorized_http = client.request(
            "POST",
            f"{base_url}/platform/v1/probes",
            headers={"idempotency-key": f"unauthorized:{first_probe}"},
            body={"clientProbeId": first_probe},
        )
        if unauthorized_http.status != 401:
            recorder.correctness["authorization_bypass"] += 1
            raise ProbeFailure("authorization_bypass", "HTTP probe accepted a request without auth")
        try:
            unauthorized = WebSocket.connect(
                base_url, session_id, device_id, auth_token=None, timeout=6.0, recorder=recorder
            )
        except WebSocketHandshakeError as error:
            if error.status in (400, 401, 403, 404):
                return
            raise ProbeFailure("websocket_auth_probe_failed", "unauthorized WebSocket did not reject cleanly") from error
        recorder.correctness["authorization_bypass"] += 1
        unauthorized.close()
        raise ProbeFailure("authorization_bypass", "WebSocket accepted a connection without probe auth")

    recorder.run("websocket-auth", "/platform/v1/socket/websocket", unauthorized_step)

    ws: WebSocket | None = None
    channel: PhoenixChannel | None = None

    def connect_step() -> tuple[WebSocket, PhoenixChannel, dict[str, Any]]:
        connection = WebSocket.connect(
            base_url, session_id, device_id, auth_token=token, timeout=8.0, recorder=recorder
        )
        phoenix = PhoenixChannel(connection)
        joined = phoenix.join({"actorKey": first_actor, "resumeCursor": 0}, timeout=10.0)
        if joined.get("protocolVersion") != "1" or joined.get("resumeCursor") != 0:
            connection.close()
            raise ProbeFailure("invalid_join_response", "initial Phoenix join returned invalid protocol/cursor")
        return connection, phoenix, joined

    ws, channel, _join = recorder.run(
        "websocket-connect", "/platform/v1/socket/websocket", connect_step
    )

    first_result: dict[str, Any]

    def command_step() -> dict[str, Any]:
        key = f"live-probe:{first_probe}"
        first = post_probe(client, base_url, token, first_probe, key)
        duplicate = post_probe(client, base_url, token, first_probe, key)
        if first.get("probeId") != first_probe or first.get("status") != "accepted":
            raise ProbeFailure("invalid_durable_outcome", "durable command returned invalid identity/status")
        if first.get("eventId") != duplicate.get("eventId"):
            recorder.correctness["duplicate_durable_identity"] += 1
            raise ProbeFailure("duplicate_durable_identity", "idempotent duplicate changed event identity")
        return first

    first_result = recorder.run(
        "durable-command-commit", "/platform/v1/probes", command_step
    )
    first_event_id = str(first_result["eventId"])

    def delivery_step() -> tuple[dict[str, Any], int]:
        assert channel is not None
        frame = channel.wait_event(
            "frame",
            lambda payload: payload.get("kind") == "event"
            and payload.get("payload", {}).get("event", {}).get("eventId") == first_event_id,
            20.0,
        )
        payload = frame.get("payload", {})
        sequence = payload.get("sequence")
        event = payload.get("event", {})
        if not isinstance(sequence, int) or sequence <= 0:
            raise ProbeFailure("invalid_handoff_sequence", "realtime event lacks a positive handoff sequence")
        if event.get("eventType") != "platform.probe.requested.v1":
            raise ProbeFailure("invalid_event_type", "realtime event type is not platform.probe.requested.v1")
        return frame, sequence

    delivery_started = monotonic_ms()
    try:
        frame, first_sequence = recorder.run(
            "realtime-delivery", "/platform/v1/socket/websocket#frame", delivery_step
        )
    except ProbeFailure as error:
        recorder.correctness["durable_event_loss"] += 1
        recorder.set(
            "outbox-handoff",
            "blocked" if error.blocked else "failed",
            monotonic_ms() - delivery_started,
            "/platform/v1/socket/websocket#frame",
            error.failure_class,
        )
        raise
    event = frame["payload"]["event"]
    if event.get("eventId") == first_event_id and first_sequence > 0:
        recorder.set("outbox-handoff", "passed", 0, "/platform/v1/socket/websocket#frame", None)
    else:
        recorder.set("outbox-handoff", "failed", 0, None, "handoff_identity_mismatch")
        raise ProbeFailure("handoff_identity_mismatch", "committed handoff identity did not match command")

    ack = channel.push("ack", {"sequence": first_sequence}, timeout=10.0)
    ack_cursor = ack.get("cursor")
    resume_token = ack.get("resume_token") or ack.get("resumeToken")
    if ack_cursor != first_sequence or not isinstance(resume_token, str):
        raise ProbeFailure("invalid_ack_response", "ACK did not return the expected cursor/resume token")

    terminal = recorder.run(
        "worker-job",
        f"/platform/v1/probes/{first_probe}",
        lambda: wait_for_terminal(client, base_url, token, first_probe, first_event_id),
    )
    if terminal.get("effectApplied") is not True or terminal.get("outboxState") != "succeeded":
        recorder.set("worker-job", "failed", recorder.checks["worker-job"]["duration_ms"], f"/platform/v1/probes/{first_probe}", "terminal_effect_invalid")
        raise ProbeFailure("terminal_effect_invalid", "worker terminal observation was not succeeded/applied")

    ws.close()
    ws = None

    second_probe = str(uuid.uuid4())
    second_actor = f"platform-probe:{second_probe}"
    second_result = post_probe(
        client, base_url, token, second_probe, f"live-probe:{second_probe}"
    )
    second_event_id = str(second_result.get("eventId"))
    if not second_event_id:
        raise ProbeFailure("invalid_durable_outcome", "second durable command did not return event identity")
    wait_for_terminal(client, base_url, token, second_probe, second_event_id)

    def resume_step() -> tuple[WebSocket, PhoenixChannel, int]:
        connection = WebSocket.connect(
            base_url, session_id, device_id, auth_token=token, timeout=8.0, recorder=recorder
        )
        phoenix = PhoenixChannel(connection)
        joined = phoenix.join(
            {
                "actorKeys": [first_actor, second_actor],
                "resumeCursor": first_sequence,
                "resumeToken": resume_token,
            },
            timeout=15.0,
        )
        repair = joined.get("repair")
        if not isinstance(repair, dict) or repair.get("cursor", 0) <= first_sequence:
            connection.close()
            raise ProbeFailure("resume_repair_missing", "reattach did not advance the durable cursor")
        resumed = phoenix.wait_event(
            "frame",
            lambda payload: payload.get("kind") == "event"
            and payload.get("payload", {}).get("event", {}).get("eventId") == second_event_id,
            15.0,
        )
        sequence = resumed.get("payload", {}).get("sequence")
        if not isinstance(sequence, int) or sequence <= first_sequence:
            connection.close()
            raise ProbeFailure("resume_sequence_invalid", "resumed event did not advance sequence")
        phoenix.push("ack", {"sequence": sequence}, timeout=10.0)
        return connection, phoenix, sequence

    ws, channel, _second_sequence = recorder.run(
        "resume-gap-repair", "/platform/v1/socket/websocket#resume", resume_step
    )

    def native_fallback_step() -> dict[str, Any]:
        response = client.request(
            "POST",
            f"{base_url}/platform/v1/probes/native",
            headers={"x-liqi-probe-token": token},
            body={
                "expectedFirst": 1,
                "expectedLast": 8,
                "observedSequences": [1, 2, 5, 5, 8],
            },
        )
        native = require_status(response, 200, "native_diagnostic_unavailable")
        configured = native.get("configured", {})
        reference = native.get("reference", {})
        fallback = native.get("fallbackExercise", {})
        fallback_ok = (
            native.get("parity") is True
            and configured.get("result") == reference.get("result")
            and fallback.get("result") == reference.get("result")
            and fallback.get("parity") is True
            and fallback.get("implementation") == "reference"
            and fallback.get("fallback") is True
            and fallback.get("fallbackReason") == "NATIVE_UNAVAILABLE"
        )
        if not fallback_ok:
            raise ProbeFailure(
                "native_fallback_mismatch",
                "native fallback diagnostic did not match the reference implementation",
            )
        return native

    native = recorder.run(
        "native-fallback",
        "/platform/v1/probes/native#fallbackExercise",
        native_fallback_step,
    )

    def native_kernel_step() -> None:
        configured = native.get("configured", {})
        readiness = native.get("readiness", {})
        if configured.get("implementation") != "native" or readiness.get("nativeAvailable") is not True:
            raise ProbeFailure(
                "native_artifact_unavailable",
                "native kernel artifact is unavailable or disabled on the live runtime",
                blocked=True,
            )

    try:
        recorder.run(
            "native-kernel",
            "/platform/v1/probes/native#configured",
            native_kernel_step,
        )
    except ProbeFailure as error:
        if not error.blocked:
            raise

    if ws is not None:
        ws.close()
    return observed_release, environment


def emit_result(
    output: Path,
    base_url: str,
    release_id: str,
    recorder: Recorder,
    started_at: str,
    observed_release_id: str,
    environment: str,
) -> int:
    result = {
        "schema_version": "live-platform-probe-v1",
        "evidence_mode": "live",
        "git_sha": git_sha(),
        "release_id": release_id,
        "observed_release_id": observed_release_id,
        "environment": environment if environment in ("staging", "production") else "staging",
        "endpoint": base_url,
        "started_at": started_at,
        "completed_at": utc_now(),
        "status": recorder.status(),
        "checks": recorder.ordered_checks(),
        "correctness_events": recorder.correctness,
        "errors": recorder.errors,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8", newline="\n")
    return 0 if result["status"] == "passed" else 2 if result["status"] == "blocked" else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--release-id", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        base_url = normalize_base_url(args.base_url)
    except ValueError as error:
        parser.error(str(error))
    if not RELEASE_ID.fullmatch(args.release_id):
        parser.error("--release-id must match liqi-v1-<identity>")

    started_at = utc_now()
    observed_release = args.release_id
    environment = os.environ.get("LIQI_ENVIRONMENT", "staging")
    try:
        token = load_probe_token()
    except ProbeFailure as error:
        recorder = Recorder()
        recorder.block_remaining(CHECK_ORDER, error.failure_class)
        recorder.errors.append(sanitize_error(error))
        return emit_result(
            args.output,
            base_url,
            args.release_id,
            recorder,
            started_at,
            observed_release,
            environment,
        )
    recorder = Recorder((token,))

    try:
        observed_release, environment = run_live_probe(
            base_url, args.release_id, token, recorder
        )
    except ProbeFailure as error:
        message = sanitize_error(error, recorder.secrets_to_redact)
        if message not in recorder.errors:
            recorder.errors.append(message)
        recorder.block_remaining(CHECK_ORDER, "upstream_probe_step_failed")
    except Exception as error:  # Always emit bounded machine-readable evidence.
        message = sanitize_error(error, recorder.secrets_to_redact)
        if message not in recorder.errors:
            recorder.errors.append(message)
        recorder.block_remaining(CHECK_ORDER, "unexpected_probe_error")
    return emit_result(
        args.output,
        base_url,
        args.release_id,
        recorder,
        started_at,
        observed_release,
        environment,
    )


if __name__ == "__main__":
    raise SystemExit(main())
