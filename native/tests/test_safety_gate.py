from __future__ import annotations

import subprocess
import unittest
from unittest.mock import patch

from native.scripts import run_v1_safety_gates as safety


class SafetyGateTest(unittest.TestCase):
    def test_version_probe_returns_unavailable_when_executable_cannot_start(self) -> None:
        with patch.object(safety.subprocess, "run", side_effect=FileNotFoundError("batch executable")):
            self.assertEqual("unavailable", safety.version(["elixir", "--version"]))

    def test_version_probe_returns_unavailable_on_timeout(self) -> None:
        with patch.object(
            safety.subprocess,
            "run",
            side_effect=subprocess.TimeoutExpired(["erl"], timeout=30),
        ):
            self.assertEqual("unavailable", safety.version(["erl", "-noshell"]))


if __name__ == "__main__":
    unittest.main()
