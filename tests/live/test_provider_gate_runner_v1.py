from __future__ import annotations

import copy
import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from operations.bin.run_provider_gates_v1 import classify_provider_outcome, expand
from operations.bin.validate_readiness_v1 import ROOT, validate_registry


class ProviderGateRunnerV1Tests(unittest.TestCase):
    def test_schema_valid_blocked_evidence_remains_blocked(self) -> None:
        status, code, message = classify_provider_outcome(69, "blocked")
        self.assertEqual((status, code), ("blocked", "PROVIDER_GATE_BLOCKED"))
        self.assertIn("exited 69", message or "")

    def test_nonzero_exit_without_result_status_fails(self) -> None:
        status, code, _message = classify_provider_outcome(2, None)
        self.assertEqual((status, code), ("failed", "PROVIDER_GATE_FAILED"))

    def test_passed_result_with_nonzero_exit_is_mismatch(self) -> None:
        status, code, _message = classify_provider_outcome(1, "passed")
        self.assertEqual((status, code), ("failed", "PROVIDER_RESULT_EXIT_MISMATCH"))

    def test_failed_result_fails_even_with_zero_exit(self) -> None:
        status, code, _message = classify_provider_outcome(0, "failed")
        self.assertEqual((status, code), ("failed", "PROVIDER_GATE_FAILED"))

    def test_zero_exit_without_status_passes(self) -> None:
        self.assertEqual(classify_provider_outcome(0, None), ("passed", None, None))

    def test_optional_environment_argument_is_omitted_when_unset(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            argv, displayed = expand(
                ["command", "{optional-env-arg:--rollback-target-descriptor:LIQI_ROLLBACK_TARGET_DESCRIPTOR}"],
                Path("result.json"),
            )
        self.assertEqual(argv, ["command"])
        self.assertEqual(displayed, "command")

    def test_optional_environment_argument_expands_and_redacts(self) -> None:
        with patch.dict(os.environ, {"LIQI_ROLLBACK_TARGET_DESCRIPTOR": "/protected/target.json"}, clear=True):
            argv, displayed = expand(
                ["command", "{optional-env-arg:--rollback-target-descriptor:LIQI_ROLLBACK_TARGET_DESCRIPTOR}"],
                Path("result.json"),
            )
        self.assertEqual(argv, ["command", "--rollback-target-descriptor", "/protected/target.json"])
        self.assertEqual(displayed, "command --rollback-target-descriptor <env:LIQI_ROLLBACK_TARGET_DESCRIPTOR>")
        self.assertNotIn("/protected/target.json", displayed)

    def test_malformed_optional_environment_argument_fails_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "invalid optional environment argument"):
            expand(["{optional-env-arg:-unsafe:LIQI_VALUE}"], Path("result.json"))

    def test_committed_registry_models_upgrade_descriptor_as_optional(self) -> None:
        registry = json.loads(
            (ROOT / "operations/readiness/provider-gates-v1.json").read_text(encoding="utf-8")
        )
        failures: list[str] = []
        validate_registry(registry, failures)
        self.assertEqual([], failures)
        gate = next(item for item in registry["gates"] if item["id"] == "deployment-artifact")
        self.assertIn(
            "{optional-env-arg:--rollback-target-descriptor:LIQI_ROLLBACK_TARGET_DESCRIPTOR}",
            gate["argv"],
        )
        self.assertNotIn("LIQI_ROLLBACK_TARGET_DESCRIPTOR", gate["required_environment"])
        self.assertNotIn("contracts/deployment/v0-rollback-compatibility-v1.schema.json", gate["required_paths"])

    def test_registry_rejects_optional_environment_marked_required(self) -> None:
        registry = json.loads(
            (ROOT / "operations/readiness/provider-gates-v1.json").read_text(encoding="utf-8")
        )
        mutated = copy.deepcopy(registry)
        gate = next(item for item in mutated["gates"] if item["id"] == "deployment-artifact")
        gate["required_environment"].append("LIQI_ROLLBACK_TARGET_DESCRIPTOR")
        failures: list[str] = []
        validate_registry(mutated, failures)
        self.assertTrue(any("optional environment must not be required" in item for item in failures))

    def test_registry_rejects_malformed_optional_environment_token(self) -> None:
        registry = json.loads(
            (ROOT / "operations/readiness/provider-gates-v1.json").read_text(encoding="utf-8")
        )
        mutated = copy.deepcopy(registry)
        gate = next(item for item in mutated["gates"] if item["id"] == "deployment-artifact")
        gate["argv"][-1] = "{optional-env-arg:-unsafe:LIQI_VALUE}"
        failures: list[str] = []
        validate_registry(mutated, failures)
        self.assertTrue(any("invalid optional environment argument token" in item for item in failures))


if __name__ == "__main__":
    unittest.main()
