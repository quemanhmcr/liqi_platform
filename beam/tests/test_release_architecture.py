from __future__ import annotations

import importlib.util
import struct
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PATH = ROOT / "beam/scripts/validate_release_manifest.py"
SPEC = importlib.util.spec_from_file_location("validate_release_architecture", PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def elf_header(machine: int) -> bytes:
    header = bytearray(20)
    header[:4] = b"\x7fELF"
    header[4] = 2
    header[5] = 1
    header[18:20] = struct.pack("<H", machine)
    return bytes(header)


class ReleaseArchitectureTests(unittest.TestCase):
    def test_reviewed_target_machines_are_exact(self) -> None:
        self.assertEqual(
            {
                "aarch64-unknown-linux-gnu": {"elf_machine": 183, "elf_name": "AArch64"},
                "x86_64-unknown-linux-gnu": {"elf_machine": 62, "elf_name": "x86-64"},
            },
            MODULE.TARGETS,
        )

    def test_erts_header_must_match_manifest_target(self) -> None:
        arm = elf_header(183)
        x86 = elf_header(62)
        self.assertTrue(MODULE.elf_matches_target(arm, "aarch64-unknown-linux-gnu"))
        self.assertTrue(MODULE.elf_matches_target(x86, "x86_64-unknown-linux-gnu"))
        self.assertFalse(MODULE.elf_matches_target(arm, "x86_64-unknown-linux-gnu"))
        self.assertFalse(MODULE.elf_matches_target(x86, "aarch64-unknown-linux-gnu"))

    def test_invalid_elf_shape_fails(self) -> None:
        self.assertFalse(MODULE.elf_matches_target(b"not-elf", "x86_64-unknown-linux-gnu"))
        with self.assertRaises(KeyError):
            MODULE.elf_matches_target(elf_header(62), "riscv64-unknown-linux-gnu")


if __name__ == "__main__":
    unittest.main()
