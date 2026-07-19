from __future__ import annotations

import importlib.util
import os
import tarfile
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PATH = ROOT / "beam/scripts/build_linux_release.py"
SPEC = importlib.util.spec_from_file_location("build_linux_release_test", PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class LinuxReleaseBuilderTests(unittest.TestCase):
    def test_reviewed_targets_are_exact(self) -> None:
        self.assertEqual(
            {
                "aarch64-unknown-linux-gnu": {"architecture": "aarch64", "elf_machine": 183},
                "x86_64-unknown-linux-gnu": {"architecture": "x86_64", "elf_machine": 62},
            },
            MODULE.TARGETS,
        )

    def test_archive_is_deterministic_and_contains_no_links(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "release"
            (source / "bin").mkdir(parents=True)
            executable = source / "bin/liqi_platform"
            executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8", newline="\n")
            os.chmod(executable, 0o755)
            (source / "releases").mkdir()
            (source / "releases/start_erl.data").write_text("16.4.0.3 1.0.0-dev\n", encoding="utf-8")
            first = root / "first.tar.gz"
            second = root / "second.tar.gz"
            MODULE.deterministic_archive(source, first)
            MODULE.deterministic_archive(source, second)
            self.assertEqual(first.read_bytes(), second.read_bytes())
            with tarfile.open(first, "r:gz") as archive:
                self.assertEqual(["bin", "bin/liqi_platform", "releases", "releases/start_erl.data"], [m.name for m in archive.getmembers()])
                self.assertTrue(all(not (m.issym() or m.islnk() or m.isdev() or m.isfifo()) for m in archive.getmembers()))

    def test_builder_source_keeps_fail_closed_sequence(self) -> None:
        source = PATH.read_text(encoding="utf-8")
        for token in (
            "release build requires a clean exact-SHA worktree",
            "verify_deployment_manifest.py",
            "Mix release does not contain the exact verified native artifact",
            "artifact and manifest signing key IDs must be distinct",
            "artifact and manifest signing public keys must be distinct",
            "self-verification did not pass",
            "os.replace(staged_output, final_output)",
        ):
            self.assertIn(token, source)


if __name__ == "__main__":
    unittest.main()
