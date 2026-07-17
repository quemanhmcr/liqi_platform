from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "scripts" / "operations" / "run_provider_gates.py"
REGISTRY = ROOT / "tests" / "contract" / "fixtures" / "provider-gates" / "registry.json"
SCHEMA = ROOT / "contracts" / "operations" / "integration-result-v0.schema.json"


class ProviderGateTests(unittest.TestCase):
    def invoke(self, allow_blocked: bool) -> tuple[subprocess.CompletedProcess[str], dict, Path]:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        base = Path(temporary.name)
        output = base / "result.json"
        command = [
            sys.executable, str(RUNNER), "--registry", str(REGISTRY), "--stage", "source",
            "--output", str(output), "--evidence-dir", str(base / "evidence")
        ]
        if allow_blocked:
            command.append("--allow-blocked")
        environment = os.environ.copy()
        environment["LIQI_TEST_PLAN_PATH"] = "C:/private/never-render-this-plan.json"
        completed = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False, env=environment)
        return completed, json.loads(output.read_text(encoding="utf-8")), base

    def test_allow_blocked_emits_valid_owner_attributed_result(self) -> None:
        completed, result, _ = self.invoke(True)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(result["overall_status"], "blocked")
        self.assertTrue(any(item["owner"] == "Senior 3" and item["status"] == "blocked" for item in result["provider_results"]))
        schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
        self.assertEqual(list(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(result)), [])

    def test_strict_mode_fails_on_pending_provider(self) -> None:
        completed, result, _ = self.invoke(False)
        self.assertEqual(completed.returncode, 2)
        self.assertEqual(result["overall_status"], "blocked")

    def test_environment_placeholder_is_not_exposed(self) -> None:
        _, result, _ = self.invoke(True)
        matching = [item for item in result["provider_results"] if item["seam"] == "secret-safe plan path fixture"]
        self.assertEqual(len(matching), 1)
        self.assertIn("<env:LIQI_TEST_PLAN_PATH>", matching[0]["command"])
        self.assertNotIn("never-render-this-plan", matching[0]["command"])

    def test_json_provider_result_is_addressable_by_gate_id(self) -> None:
        _, result, _ = self.invoke(True)
        matching = [item for item in result["provider_results"] if item.get("gate_id") == "senior2-json-output"]
        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0]["status"], "passed")
        self.assertTrue(matching[0]["output_ref"].endswith("senior2-json-output.json"))
        output_path = ROOT / matching[0]["output_ref"] if not Path(matching[0]["output_ref"]).is_absolute() else Path(matching[0]["output_ref"])
        self.assertTrue(output_path.is_file())

    def test_provider_logs_are_redacted(self) -> None:
        _, result, _ = self.invoke(True)
        logs = [ROOT / item["output_ref"] if item["output_ref"] and not Path(item["output_ref"]).is_absolute() else Path(item["output_ref"] or "") for item in result["provider_results"] if item["output_ref"]]
        contents = "\n".join(path.read_text(encoding="utf-8") for path in logs)
        self.assertNotIn("TEST_ONLY_REDACTION_VALUE", contents)
        self.assertIn("password=[REDACTED]", contents)


if __name__ == "__main__":
    unittest.main()
