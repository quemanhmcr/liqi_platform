from __future__ import annotations

import importlib.util
import struct
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def load(name: str, relative: str):
    path = ROOT / relative
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PACKAGE = load("package_artifact_architecture", "native/scripts/package_artifact.py")
VERIFY = load("verify_artifact_architecture", "native/scripts/verify_artifact.py")


def elf64(machine: int) -> bytes:
    header = bytearray(64)
    header[:4] = b"\x7fELF"
    header[4] = 2
    header[5] = 1
    header[6] = 1
    header[16:18] = struct.pack("<H", 3)
    header[18:20] = struct.pack("<H", machine)
    return bytes(header)


class ArtifactArchitectureTests(unittest.TestCase):
    def test_native_build_entrypoints_are_executable_in_git(self) -> None:
        import subprocess

        paths = (
            "native/scripts/build-linux-artifact.sh",
            "native/scripts/build-arm64-artifact.sh",
            "native/scripts/build-x86_64-artifact.sh",
        )
        output = subprocess.check_output(["git", "ls-files", "-s", *paths], cwd=ROOT, text=True)
        modes = {line.split()[3]: line.split()[0] for line in output.splitlines()}
        self.assertEqual({path: "100755" for path in paths}, modes)

    def test_reviewed_target_pairings_are_exact(self) -> None:
        self.assertEqual(
            {
                "aarch64-unknown-linux-gnu": {"architecture": "aarch64", "elf_machine": 183, "elf_name": "AArch64"},
                "x86_64-unknown-linux-gnu": {"architecture": "x86_64", "elf_machine": 62, "elf_name": "x86-64"},
            },
            PACKAGE.TARGETS,
        )
        self.assertEqual(PACKAGE.TARGETS, VERIFY.TARGETS)

    def test_each_target_accepts_only_its_elf_machine(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            arm = root / "arm.so"
            x86 = root / "x86.so"
            arm.write_bytes(elf64(183))
            x86.write_bytes(elf64(62))
            for module in (PACKAGE, VERIFY):
                function = module.require_elf if module is PACKAGE else module.verify_elf
                function(arm, "aarch64-unknown-linux-gnu")
                function(x86, "x86_64-unknown-linux-gnu")
                with self.assertRaises(ValueError):
                    function(arm, "x86_64-unknown-linux-gnu")
                with self.assertRaises(ValueError):
                    function(x86, "aarch64-unknown-linux-gnu")

    def test_non_elf_and_unsupported_target_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            artifact = Path(directory) / "bad.so"
            artifact.write_bytes(b"not-elf")
            with self.assertRaises(ValueError):
                PACKAGE.require_elf(artifact, "aarch64-unknown-linux-gnu")
            with self.assertRaises(KeyError):
                VERIFY.verify_elf(artifact, "riscv64-unknown-linux-gnu")


if __name__ == "__main__":
    unittest.main()
