from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[2]
PATH = ROOT / "beam/scripts/validate_linux_release_build_result.py"
SPEC = importlib.util.spec_from_file_location("validate_linux_release_build_result_test", PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class LinuxReleaseBuildResultTests(unittest.TestCase):
    def test_contract_example_is_portable_and_closed(self) -> None:
        schema = json.loads((ROOT / "contracts/runtime/linux-release-build-result-v1.schema.json").read_text(encoding="utf-8"))
        example = json.loads((ROOT / "contracts/runtime/linux-release-build-result-v1.example.json").read_text(encoding="utf-8"))
        errors = list(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(example))
        self.assertEqual([], errors)

        def walk(value: object) -> None:
            if isinstance(value, dict):
                self.assertNotIn("path", value)
                for child in value.values():
                    walk(child)
            elif isinstance(value, list):
                for child in value:
                    walk(child)

        walk(example)
        for key, value in example.items():
            if isinstance(value, dict) and "filename" in value:
                self.assertEqual(Path(value["filename"]).name, value["filename"], key)

    def test_evidence_file_requires_exact_checksum(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            payload = root / "artifact.json"
            payload.write_text("{}\n", encoding="utf-8")
            good = hashlib.sha256(payload.read_bytes()).hexdigest()
            self.assertEqual(payload, MODULE.evidence_file(root, {"filename": payload.name, "sha256": good}, "artifact"))
            with self.assertRaisesRegex(ValueError, "SHA256"):
                MODULE.evidence_file(root, {"filename": payload.name, "sha256": "0" * 64}, "artifact")

    def test_evidence_file_rejects_traversal_and_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            outside = root.parent / f"{root.name}-outside.json"
            outside.write_text("{}\n", encoding="utf-8")
            try:
                with self.assertRaisesRegex(ValueError, "not portable"):
                    MODULE.evidence_file(root, {"filename": "../escape.json", "sha256": "0" * 64}, "artifact")
                link = root / "link.json"
                try:
                    os.symlink(outside, link)
                except (OSError, NotImplementedError):
                    self.skipTest("symlinks unavailable on this host")
                with self.assertRaises((FileNotFoundError, ValueError)):
                    MODULE.evidence_file(root, {"filename": link.name, "sha256": hashlib.sha256(outside.read_bytes()).hexdigest()}, "artifact")
            finally:
                outside.unlink(missing_ok=True)

    def test_distinct_public_keys_reject_duplicate_material(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            private = root / "private.pem"
            first = root / "first.pem"
            second = root / "second.pem"
            subprocess.run(["openssl", "genpkey", "-algorithm", "ED25519", "-out", str(private)], check=True, capture_output=True)
            subprocess.run(["openssl", "pkey", "-in", str(private), "-pubout", "-out", str(first)], check=True, capture_output=True)
            second.write_text(first.read_text(encoding="utf-8").replace("\n", "\r\n"), encoding="utf-8", newline="")
            with self.assertRaisesRegex(ValueError, "public key material is reused"):
                MODULE.require_distinct_public_keys([("first", first), ("second", second)])


    def test_ed25519_verification_rejects_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            private = root / "private.pem"
            public = root / "release-signing-v1.pem"
            payload = root / "payload.bin"
            signature = root / "payload.bin.sig"
            payload.write_bytes(b"exact-release-bytes")
            subprocess.run(["openssl", "genpkey", "-algorithm", "ED25519", "-out", str(private)], check=True, capture_output=True)
            subprocess.run(["openssl", "pkey", "-in", str(private), "-pubout", "-out", str(public)], check=True, capture_output=True)
            subprocess.run(["openssl", "pkeyutl", "-sign", "-rawin", "-inkey", str(private), "-in", str(payload), "-out", str(signature)], check=True, capture_output=True)
            MODULE.verify_ed25519(payload, signature, public)
            payload.write_bytes(b"tampered-release-bytes")
            with self.assertRaisesRegex(ValueError, "Signature Verification Failure|provider signature failure|command failed"):
                MODULE.verify_ed25519(payload, signature, public)

    def test_trusted_key_rejects_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            outside = root.parent / f"{root.name}-key.pem"
            outside.write_text("not-a-key\n", encoding="utf-8")
            try:
                link = root / "release-signing-v1.pem"
                try:
                    os.symlink(outside, link)
                except (OSError, NotImplementedError):
                    self.skipTest("symlinks unavailable on this host")
                with self.assertRaises(FileNotFoundError):
                    MODULE.trusted_key(root, "release-signing-v1", "release")
            finally:
                outside.unlink(missing_ok=True)



if __name__ == "__main__":
    unittest.main()
