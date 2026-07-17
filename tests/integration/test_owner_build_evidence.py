from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.operations.owner_build_evidence import ROOT, repository_ref, sha256, validate_owner_evidence
from scripts.operations.run_owner_build_gate import windows_msvc_linker_error

RUNNER = ROOT / "scripts/operations/run_provider_gates.py"


class OwnerBuildEvidenceTests(unittest.TestCase):
    def test_git_unix_linker_is_rejected_for_msvc_host(self) -> None:
        error = windows_msvc_linker_error(
            "win32",
            "x86_64-pc-windows-msvc",
            r"C:\Program Files\Git\usr\bin\link.exe",
        )
        self.assertIsNotNone(error)
        self.assertIn("Git Unix linker", error or "")

    def test_gnu_host_does_not_require_msvc_linker(self) -> None:
        self.assertIsNone(windows_msvc_linker_error("win32", "x86_64-pc-windows-gnu", None))

    def test_non_git_msvc_linker_is_accepted(self) -> None:
        self.assertIsNone(
            windows_msvc_linker_error(
                "win32",
                "x86_64-pc-windows-msvc",
                r"C:\BuildTools\VC\Tools\MSVC\bin\Hostx64\x64\link.exe",
            )
        )

    def gate(self, gate_id: str = "runtime-owner-evidence-test") -> dict[str, object]:
        return {
            "id": gate_id,
            "owner": "Senior 3",
            "seam": "test owner evidence",
            "stages": ["source"],
            "provider_state": "pending-owner-build",
            "mutation_class": "read-only",
            "required_paths": ["Cargo.toml"],
            "argv": ["cargo", "+1.97.1", "definitely-not-run"],
            "timeout_seconds": 30,
            "result_mode": "exit-code",
            "failure_class": "runtime",
            "action_required": "Project owner must provide exact-SHA evidence.",
        }

    def make_evidence(self, directory: Path, git_sha: str, gate: dict[str, object] | None = None) -> Path:
        gate = gate or self.gate()
        gate_id = str(gate["id"])
        log = directory / f"{gate_id}.log"
        log.write_text("owner approved test evidence\n", encoding="utf-8", newline="\n")
        record = {
            "schema_version": "owner-build-evidence-v0",
            "producer": "project-owner",
            "approval_ref": "approval://test/owner-build",
            "gate_id": gate_id,
            "git_sha": git_sha,
            "command": gate["argv"],
            "status": "passed",
            "exit_code": 0,
            "started_at": "2026-07-17T00:00:00Z",
            "completed_at": "2026-07-17T00:00:01Z",
            "log_ref": repository_ref(log),
            "log_sha256": sha256(log),
        }
        path = directory / f"{gate_id}.json"
        path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8", newline="\n")
        return path

    def test_exact_sha_and_digest_are_accepted(self) -> None:
        sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
        (ROOT / ".artifacts").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT / ".artifacts") as tmp:
            record_path = self.make_evidence(Path(tmp), sha)
            record, failures = validate_owner_evidence(record_path, self.gate(), sha)
            self.assertIsNotNone(record)
            self.assertEqual([], failures)

    def test_tampered_artifact_is_rejected(self) -> None:
        sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
        (ROOT / ".artifacts").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT / ".artifacts") as tmp:
            directory = Path(tmp)
            record_path = self.make_evidence(directory, sha)
            (directory / "runtime-owner-evidence-test.log").write_text("tampered\n", encoding="utf-8")
            _, failures = validate_owner_evidence(record_path, self.gate(), sha)
            self.assertTrue(any("exact artifact bytes" in failure for failure in failures))

    def test_provider_runner_consumes_evidence_without_running_command(self) -> None:
        sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
        (ROOT / ".artifacts").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT / ".artifacts") as tmp:
            directory = Path(tmp)
            gates = [self.gate(f"runtime-owner-evidence-test-{index}") for index in range(1, 4)]
            for gate in gates:
                self.make_evidence(directory, sha, gate)
            registry = directory / "registry.json"
            registry.write_text(json.dumps({
                "schema_version": "provider-gates-v0",
                "registry_version": "0.6.0",
                "gates": gates,
            }), encoding="utf-8", newline="\n")
            output = directory / "provider-result.json"
            result = subprocess.run([
                sys.executable,
                str(RUNNER),
                "--registry", str(registry),
                "--stage", "source",
                "--owner-evidence-dir", str(directory),
                "--output", str(output),
            ], cwd=ROOT, text=True, capture_output=True, check=False)
            self.assertEqual(0, result.returncode, result.stderr)
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual("passed", payload["overall_status"])
            self.assertTrue(all(item["status"] == "passed" for item in payload["provider_results"]))


if __name__ == "__main__":
    unittest.main()
