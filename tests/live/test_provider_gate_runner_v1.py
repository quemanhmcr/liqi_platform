from __future__ import annotations

import unittest

from operations.bin.run_provider_gates_v1 import classify_provider_outcome


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


if __name__ == "__main__":
    unittest.main()
