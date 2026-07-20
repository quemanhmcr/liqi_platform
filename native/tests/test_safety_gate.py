from __future__ import annotations

import subprocess
import unittest
from pathlib import Path
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

    def test_fuzz_runner_uses_explicit_project_directory(self) -> None:
        root = Path(__file__).resolve().parents[2]
        runner = (root / "native/fuzz/run-fuzz.sh").read_text(encoding="utf-8")
        self.assertIn('--fuzz-dir "$ROOT_DIR/native/fuzz"', runner)
        self.assertNotIn('cd "$ROOT_DIR/native/fuzz"', runner)

    def test_fuzz_runner_writes_corpus_only_below_ignored_artifacts(self) -> None:
        root = Path(__file__).resolve().parents[2]
        runner = (root / "native/fuzz/run-fuzz.sh").read_text(encoding="utf-8")
        self.assertIn('CORPUS_DIR="$ROOT_DIR/.artifacts/native-fuzz/corpus/sequence_diff_parity"', runner)
        self.assertIn('sequence_diff_parity "$CORPUS_DIR" --', runner)
        self.assertNotIn('sequence_diff_parity -- \\', runner)

    def test_fuzz_toolchain_selector_uses_bash_ere(self) -> None:
        root = Path(__file__).resolve().parents[2]
        runner = (root / "native/fuzz/run-fuzz.sh").read_text(encoding="utf-8")
        self.assertIn(r'^nightly(-[0-9]{4}-[0-9]{2}-[0-9]{2})?$', runner)
        self.assertNotIn('(?:', runner)

    def test_publication_provisions_cross_target_and_preserves_failure_evidence(self) -> None:
        root = Path(__file__).resolve().parents[2]
        workflow = (root / ".github/workflows/v1-e5-artifact-release.yml").read_text(encoding="utf-8")
        self.assertIn(
            "rustup target add --toolchain 1.97.1 aarch64-unknown-linux-gnu",
            workflow,
        )
        upload = workflow.split("- name: Upload native safety evidence", 1)[1].split(
            "- name: Build exact x86_64 native artifact", 1
        )[0]
        self.assertIn("if: always()", upload)
        self.assertIn("path: .artifacts/e5/**", upload)
        self.assertIn("${{ github.run_attempt }}", upload)


if __name__ == "__main__":
    unittest.main()
