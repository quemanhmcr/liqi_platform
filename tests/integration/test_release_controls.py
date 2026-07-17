from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PYTHON = sys.executable
HEALTH_FIXTURE = ROOT / "tests" / "integration" / "fixtures" / "health-gate-target.valid.json"
RECOVERY_FIXTURE = ROOT / "tests" / "integration" / "fixtures" / "recovery-status.valid.json"
RELEASE_FIXTURE = ROOT / "tests" / "contract" / "fixtures" / "operations" / "release-manifest.valid.json"


def run(*args: str, expected: int = 0) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(args, cwd=ROOT, text=True, capture_output=True, check=False)
    if result.returncode != expected:
        raise AssertionError(
            f"command returned {result.returncode}, expected {expected}: {args}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


class HealthHandler(BaseHTTPRequestHandler):
    readiness = True

    def do_GET(self) -> None:  # noqa: N802
        payload = {"release_id": "liqi-v0-test"}
        status = 200
        if self.path == "/health/live":
            payload["status"] = "live"
        elif self.path == "/health/ready":
            payload["status"] = "ready" if self.readiness else "not-ready"
            status = 200 if self.readiness else 503
        elif self.path == "/health/platform":
            payload.update({"status": "ok", "database_ready": True})
        else:
            status = 404
            payload = {"status": "missing"}
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


class ServerContext:
    def __init__(self, readiness: bool) -> None:
        handler = type("ConfiguredHealthHandler", (HealthHandler,), {"readiness": readiness})
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self) -> int:
        self.thread.start()
        return int(self.server.server_address[1])

    def __exit__(self, *args: object) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


class ReleaseControlTests(unittest.TestCase):
    def write_health_target(self, directory: str, port: int) -> Path:
        target = json.loads(HEALTH_FIXTURE.read_text(encoding="utf-8"))
        for check in target["checks"]:
            path = check["url"].split("/", 3)[-1]
            check["url"] = f"http://127.0.0.1:{port}/{path}"
        output = Path(directory) / "target.json"
        output.write_text(json.dumps(target), encoding="utf-8")
        return output

    def test_health_gate_requires_readiness_and_platform_probe(self) -> None:
        with tempfile.TemporaryDirectory() as directory, ServerContext(readiness=True) as port:
            target = self.write_health_target(directory, port)
            output = Path(directory) / "result.json"
            run(PYTHON, "scripts/release/health_gate.py", "--target", str(target), "--output", str(output))
            result = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual("passed", result["status"])
            self.assertEqual({"liveness", "readiness", "platform-probe"}, {item["kind"] for item in result["checks"]})

    def test_liveness_does_not_mask_readiness_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory, ServerContext(readiness=False) as port:
            target = self.write_health_target(directory, port)
            output = Path(directory) / "result.json"
            run(
                PYTHON,
                "scripts/release/health_gate.py",
                "--target",
                str(target),
                "--output",
                str(output),
                expected=1,
            )
            result = json.loads(output.read_text(encoding="utf-8"))
            by_kind = {item["kind"]: item for item in result["checks"]}
            self.assertEqual("failed", result["status"])
            self.assertEqual("passed", by_kind["liveness"]["status"])
            self.assertEqual("failed", by_kind["readiness"]["status"])

    def test_rollback_target_must_be_retained_and_predeclared(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            history = Path(directory) / "history"
            history.mkdir()
            base = json.loads(RELEASE_FIXTURE.read_text(encoding="utf-8"))
            previous = copy.deepcopy(base)
            previous["release_id"] = "liqi-v0-previous"
            previous["git_sha"] = "1" * 40
            previous["deployment"]["status"] = "active"
            previous["deployment"]["activated_at"] = "2026-07-16T00:00:00Z"
            previous_path = history / "liqi-v0-previous.json"
            previous_path.write_text(json.dumps(previous), encoding="utf-8")

            current = copy.deepcopy(base)
            current["release_id"] = "liqi-v0-current"
            current["git_sha"] = "2" * 40
            current["rollback"]["compatible"] = True
            current["rollback"]["previous_release_id"] = "liqi-v0-previous"
            current["rollback"]["first_release_reason"] = None
            current_path = Path(directory) / "current.json"
            current_path.write_text(json.dumps(current), encoding="utf-8")
            output = Path(directory) / "rollback.json"

            run(
                PYTHON,
                "scripts/release/select_rollback.py",
                "--current",
                str(current_path),
                "--history-dir",
                str(history),
                "--output",
                str(output),
            )
            result = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual("passed", result["status"])
            self.assertFalse(result["database_rollback_allowed"])

    def test_stale_recovery_evidence_fails(self) -> None:
        result = run(
            PYTHON,
            "scripts/operations/check_recovery_freshness.py",
            "--status",
            str(RECOVERY_FIXTURE),
            "--as-of",
            "2026-08-20T00:00:00Z",
            expected=1,
        )
        report = json.loads(result.stdout)
        self.assertEqual("failed", report["status"])
        self.assertTrue(any("restore verification age" in failure for failure in report["failures"]))


if __name__ == "__main__":
    unittest.main()
