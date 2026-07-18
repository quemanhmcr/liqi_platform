from __future__ import annotations

import json
import os
import struct
import uuid
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jsonschema import Draft202012Validator, FormatChecker

from beam.scripts import platform_probe as probe


class SendSocket:
    def __init__(self):
        self.sent = bytearray()

    def sendall(self, value: bytes) -> None:
        self.sent.extend(value)

    def close(self) -> None:
        pass


class ReceiveSocket:
    def __init__(self, payload: bytes):
        self.payload = bytearray(payload)

    def settimeout(self, _value: float) -> None:
        pass

    def recv(self, size: int) -> bytes:
        value = bytes(self.payload[:size])
        del self.payload[:size]
        return value

    def close(self) -> None:
        pass


class FakeWebSocket:
    def __init__(self, messages):
        self.messages = list(messages)
        self.sent = []

    def send_json(self, value):
        self.sent.append(value)

    def recv_json(self, _timeout):
        return self.messages.pop(0)


class PlatformProbeTest(unittest.TestCase):
    def test_missing_token_emits_schema_valid_blocked_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "result.json"
            with patch.dict(os.environ, {}, clear=True), patch.object(
                probe, "git_sha", return_value="a" * 40
            ):
                rc = probe.main(
                    [
                        "--base-url",
                        "https://probe.example.test",
                        "--release-id",
                        "liqi-v1-test-release",
                        "--output",
                        str(output),
                    ]
                )
            self.assertEqual(rc, 2)
            result = json.loads(output.read_text(encoding="utf-8"))
            schema = json.loads(
                (probe.ROOT / "contracts/readiness/live-platform-probe-v1.schema.json").read_text(
                    encoding="utf-8"
                )
            )
            errors = list(
                Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(result)
            )
            self.assertEqual(errors, [])
            self.assertEqual(result["status"], "blocked")
            self.assertEqual(len(result["checks"]), 11)
            self.assertTrue(
                all(
                    item["failure_class"] == "probe_auth_token_missing"
                    for item in result["checks"]
                )
            )

    def test_client_text_frames_are_masked_and_round_trip(self):
        sock = SendSocket()
        websocket = probe.WebSocket(sock)  # type: ignore[arg-type]
        websocket.send_text("hello")
        frame = bytes(sock.sent)
        self.assertEqual(frame[0], 0x81)
        self.assertTrue(frame[1] & 0x80)
        length = frame[1] & 0x7F
        self.assertEqual(length, 5)
        mask = frame[2:6]
        masked = frame[6:]
        decoded = bytes(value ^ mask[index % 4] for index, value in enumerate(masked))
        self.assertEqual(decoded, b"hello")

    def test_server_text_frame_is_decoded(self):
        payload = b'["1","1","platform:v1","phx_reply",{}]'
        frame = bytes([0x81, len(payload)]) + payload
        websocket = probe.WebSocket(ReceiveSocket(frame))  # type: ignore[arg-type]
        self.assertEqual(websocket.recv_json(1.0)[3], "phx_reply")

    def test_phoenix_reply_matching_preserves_unrelated_events(self):
        websocket = FakeWebSocket(
            [
                ["1", None, "platform:v1", "frame", {"kind": "event"}],
                ["1", "1", "platform:v1", "phx_reply", {"status": "ok", "response": {"cursor": 0}}],
            ]
        )
        channel = probe.PhoenixChannel(websocket)  # type: ignore[arg-type]
        response = channel.join({"actorKey": "platform-probe:00000000-0000-4000-8000-000000000000"})
        self.assertEqual(response["cursor"], 0)
        event = channel.wait_event("frame", lambda payload: payload["kind"] == "event", 1.0)
        self.assertEqual(event["kind"], "event")


    def test_file_token_reference_is_bounded_and_single_line(self):
        with tempfile.TemporaryDirectory() as directory:
            token_path = Path(directory) / "probe-token"
            token_path.write_text("protected-token\n", encoding="utf-8", newline="\n")
            with patch.dict(
                os.environ,
                {
                    "LIQI_PROBE_AUTH_TOKEN_REF": f"file://{token_path}",
                    "LIQI_PROBE_AUTH_TOKEN": "",
                },
                clear=False,
            ):
                self.assertEqual(probe.load_probe_token(), "protected-token")

            token_path.write_text("first\nsecond\n", encoding="utf-8", newline="\n")
            with patch.dict(
                os.environ,
                {"LIQI_PROBE_AUTH_TOKEN_REF": f"file://{token_path}"},
                clear=False,
            ):
                with self.assertRaises(probe.ProbeFailure) as raised:
                    probe.load_probe_token()
                self.assertEqual(raised.exception.failure_class, "probe_auth_token_invalid")

    def test_secret_reflection_is_a_hard_correctness_failure(self):
        recorder = probe.Recorder(("protected-token",))
        with self.assertRaises(probe.ProbeFailure) as raised:
            recorder.inspect_secrets(b'{"echo":"protected-token"}')
        self.assertEqual(raised.exception.failure_class, "secret_exposure")
        self.assertEqual(recorder.correctness["secret_exposure"], 1)

    def test_masked_server_frame_is_rejected(self):
        payload = b"{}"
        mask = b"abcd"
        masked = bytes(value ^ mask[index % 4] for index, value in enumerate(payload))
        frame = bytes([0x81, 0x80 | len(payload)]) + mask + masked
        websocket = probe.WebSocket(ReceiveSocket(frame))  # type: ignore[arg-type]
        with self.assertRaises(probe.ProbeFailure) as raised:
            websocket.recv_text(1.0)
        self.assertEqual(raised.exception.failure_class, "websocket_server_masked")

    def test_rsv_server_frame_is_rejected(self):
        frame = bytes([0xC1, 2]) + b"{}"
        websocket = probe.WebSocket(ReceiveSocket(frame))  # type: ignore[arg-type]
        with self.assertRaises(probe.ProbeFailure) as raised:
            websocket.recv_text(1.0)
        self.assertEqual(raised.exception.failure_class, "websocket_protocol_error")

    def test_base_url_rejects_credentials_paths_and_plain_http(self):
        for value in (
            "http://example.test",
            "https://user@example.test",
            "https://example.test/path",
            "https://example.test/?token=secret",
        ):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    probe.normalize_base_url(value)

    def test_synthetic_transport_exercises_all_eleven_orchestration_checks(self):
        state = {"events": {}, "order": [], "connections": 0}
        source_sha = "a" * 40

        class SyntheticHttpClient:
            def __init__(self, recorder, timeout=8.0):
                self.recorder = recorder
                self.timeout = timeout

            def request(self, method, url, *, headers=None, body=None):
                if url.endswith("/health/live"):
                    return probe.HttpResponse(200, {}, json.dumps({"status": "live", "releaseId": "liqi-v1-test-release"}).encode())
                if url.endswith("/platform/v1/metadata"):
                    return probe.HttpResponse(200, {}, json.dumps({"releaseId": "liqi-v1-test-release", "sourceRevision": source_sha, "environment": "staging"}).encode())
                if url.endswith("/health/ready"):
                    return probe.HttpResponse(200, {}, json.dumps({"status": "ready", "releaseId": "liqi-v1-test-release"}).encode())
                if url.endswith("/platform/v1/probes") and method == "POST":
                    if not headers or "x-liqi-probe-token" not in headers:
                        return probe.HttpResponse(
                            401,
                            {},
                            json.dumps({"error": {"code": "auth.unauthorized"}}).encode(),
                        )
                    probe_id = body["clientProbeId"]
                    if probe_id not in state["events"]:
                        event_id = str(uuid.uuid5(uuid.NAMESPACE_URL, probe_id))
                        state["events"][probe_id] = event_id
                        state["order"].append((probe_id, event_id))
                    event_id = state["events"][probe_id]
                    return probe.HttpResponse(202, {}, json.dumps({"probeId": probe_id, "eventId": event_id, "status": "accepted"}).encode())
                if "/platform/v1/probes/" in url and method == "GET":
                    probe_id = url.split("/platform/v1/probes/", 1)[1].split("?", 1)[0]
                    event_id = state["events"][probe_id]
                    return probe.HttpResponse(200, {}, json.dumps({"probeId": probe_id, "eventId": event_id, "probeStatus": "completed", "outboxState": "succeeded", "effectApplied": True, "terminal": True, "observedAt": "2026-07-18T00:00:00Z"}).encode())
                if url.endswith("/platform/v1/probes/native"):
                    result = {"missing_ranges": [{"first": 3, "last": 4}], "observed_count": 5, "unique_count": 4, "duplicate_count": 1}
                    return probe.HttpResponse(200, {}, json.dumps({"parity": True, "configured": {"implementation": "native", "fallback": False, "result": result}, "reference": {"result": result}, "fallbackExercise": {"parity": True, "implementation": "reference", "fallback": True, "fallbackReason": "NATIVE_UNAVAILABLE", "result": result}, "readiness": {"ready": True, "required": False, "nativeAvailable": True, "reason": None}}).encode())
                raise AssertionError((method, url, body))

        class SyntheticWebSocket:
            def close(self):
                pass

            @classmethod
            def connect(
                cls,
                _base,
                _session,
                _device,
                *,
                auth_token,
                timeout=8.0,
                recorder=None,
            ):
                if auth_token is None:
                    raise probe.WebSocketHandshakeError(403, "rejected")
                state["connections"] += 1
                return cls()

        class SyntheticPhoenixChannel:
            def __init__(self, websocket, topic="platform:v1"):
                self.websocket = websocket
                self.topic = topic
                self.connection = state["connections"]

            def join(self, payload, timeout=10.0):
                if self.connection == 1:
                    return {"protocolVersion": "1", "resumeCursor": 0, "resumeToken": "initial", "repair": {"cursor": 0, "delivered": 0, "scanned": 0}}
                return {"protocolVersion": "1", "resumeCursor": 1, "resumeToken": "resume-1", "repair": {"cursor": 2, "delivered": 1, "scanned": 1}}

            def wait_event(self, event, predicate, timeout):
                index = 0 if self.connection == 1 else 1
                probe_id, event_id = state["order"][index]
                frame = {"kind": "event", "payload": {"sequence": index + 1, "event": {"eventId": event_id, "eventType": "platform.probe.requested.v1", "aggregateKey": f"platform-probe:{probe_id}"}}}
                assert event == "frame" and predicate(frame)
                return frame

            def push(self, event, payload, timeout=10.0):
                assert event == "ack"
                return {"cursor": payload["sequence"], "resume_token": f"resume-{payload['sequence']}"}

        recorder = probe.Recorder()
        with patch.object(probe, "HttpClient", SyntheticHttpClient), patch.object(probe, "WebSocket", SyntheticWebSocket), patch.object(probe, "PhoenixChannel", SyntheticPhoenixChannel), patch.object(probe, "git_sha", return_value=source_sha):
            observed, environment = probe.run_live_probe("https://probe.example.test", "liqi-v1-test-release", "token", recorder)
        self.assertEqual(observed, "liqi-v1-test-release")
        self.assertEqual(environment, "staging")
        self.assertEqual(recorder.status(), "passed")
        self.assertEqual([item["status"] for item in recorder.ordered_checks()], ["passed"] * 11)
        self.assertEqual(recorder.correctness, {"authorization_bypass": 0, "secret_exposure": 0, "duplicate_durable_identity": 0, "event_before_commit": 0, "durable_event_loss": 0})

    def test_token_reference_and_error_redaction(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "probe-token"
            path.write_text("super-secret\n", encoding="utf-8")
            with patch.dict(
                os.environ,
                {"LIQI_PROBE_AUTH_TOKEN_REF": f"file://{path}"},
                clear=True,
            ):
                self.assertEqual(probe.load_probe_token(), "super-secret")
            sanitized = probe.sanitize_error(RuntimeError("contains super-secret"), ("super-secret",))
            self.assertNotIn("super-secret", sanitized)
            self.assertIn("<redacted>", sanitized)

    def test_correctness_event_forces_failed_status(self):
        recorder = probe.Recorder()
        for name in probe.CHECK_ORDER:
            recorder.set(name, "passed", 0, "synthetic", None)
        self.assertEqual(recorder.status(), "passed")
        recorder.correctness["secret_exposure"] = 1
        self.assertEqual(recorder.status(), "failed")

    def test_git_sha_fallback_is_schema_safe_but_cannot_match_live_metadata(self):
        with patch.object(probe, "GIT_EXECUTABLE", None):
            self.assertEqual(probe.git_sha(), "0" * 40)

    def test_phoenix_frame_requires_exactly_five_elements(self):
        payload = b'["1","1","platform:v1","phx_reply",{},"extra"]'
        frame = bytes([0x81, len(payload)]) + payload
        websocket = probe.WebSocket(ReceiveSocket(frame))  # type: ignore[arg-type]
        with self.assertRaises(probe.ProbeFailure) as raised:
            websocket.recv_json(1.0)
        self.assertEqual(raised.exception.failure_class, "invalid_phoenix_frame")

    def test_new_text_frame_before_continuation_is_rejected(self):
        first = bytes([0x01, 1]) + b"["
        second = bytes([0x81, 1]) + b"]"
        websocket = probe.WebSocket(ReceiveSocket(first + second))  # type: ignore[arg-type]
        with self.assertRaises(probe.ProbeFailure) as raised:
            websocket.recv_text(1.0)
        self.assertEqual(raised.exception.failure_class, "websocket_protocol_error")


if __name__ == "__main__":
    unittest.main()
