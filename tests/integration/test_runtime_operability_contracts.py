from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/operations/validate_runtime_operability_contracts.py"
FIXTURES = ROOT / "tests/contract/fixtures/provider-compatibility"


class RuntimeOperabilityContractTests(unittest.TestCase):
    def invoke(self, fixture: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--provider-root", str(FIXTURES / fixture)],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_published_capacity_and_telemetry_contracts_pass(self) -> None:
        completed = self.invoke("compatible")
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_missing_runtime_operability_contracts_fail_closed(self) -> None:
        completed = self.invoke("incompatible")
        self.assertEqual(completed.returncode, 1)
        self.assertIn("runtime capacity declaration is missing", completed.stderr)
        self.assertIn("missing telemetry declaration", completed.stderr)


if __name__ == "__main__":
    unittest.main()
