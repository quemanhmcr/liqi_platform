from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

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

    def test_complete_publication_cross_binding_and_tamper_rejection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            publication = root / "publication"
            release_trust = root / "release-trust"
            native_trust = root / "native-trust"
            publication.mkdir()
            release_trust.mkdir()
            native_trust.mkdir()
            git_sha = "1" * 40
            target = "x86_64-unknown-linux-gnu"
            release_id = "liqi-v1-e5-test"

            def keypair(key_id: str, trust_dir: Path) -> tuple[Path, Path]:
                private = root / f"{key_id}.private.pem"
                public = trust_dir / f"{key_id}.pem"
                subprocess.run(["openssl", "genpkey", "-algorithm", "ED25519", "-out", str(private)], check=True, capture_output=True)
                subprocess.run(["openssl", "pkey", "-in", str(private), "-pubout", "-out", str(public)], check=True, capture_output=True)
                return private, public

            def sign(payload: Path, private: Path, signature: Path) -> None:
                subprocess.run([
                    "openssl", "pkeyutl", "-sign", "-rawin", "-inkey", str(private),
                    "-in", str(payload), "-out", str(signature),
                ], check=True, capture_output=True)

            artifact_private, _ = keypair("release-artifact-v1", release_trust)
            manifest_private, _ = keypair("release-manifest-v1", release_trust)
            _, _ = keypair("deployment-signing-v1", native_trust)

            archive = publication / f"{release_id}.tar.gz"
            archive.write_bytes(b"synthetic-release-archive")
            archive_signature = publication / f"{archive.name}.sig"
            sign(archive, artifact_private, archive_signature)

            native_provider = json.loads((ROOT / "contracts/native/examples/native-artifact-v1.x86_64.example.json").read_text(encoding="utf-8"))
            native_provider.update({"release_id": release_id, "source_revision": git_sha, "target_triple": target, "architecture": "x86_64"})
            native_provider_path = publication / f"native-artifact-{release_id}.json"
            native_provider_path.write_text(json.dumps(native_provider, indent=2, sort_keys=True) + "\n", encoding="utf-8")

            native_deployment = json.loads((ROOT / "contracts/deployment/native-artifact-v1.x86_64.example.json").read_text(encoding="utf-8"))
            native_deployment["git_sha"] = git_sha
            native_deployment["target_triple"] = target
            native_deployment["artifact"]["sha256"] = native_provider["artifact_sha256"]
            native_deployment["artifact"]["size_bytes"] = native_provider["artifact_size_bytes"]
            native_deployment["artifact"]["signature"]["key_id"] = "deployment-signing-v1"
            native_deployment_path = publication / f"native-deployment-{release_id}.json"
            native_deployment_path.write_text(json.dumps(native_deployment, indent=2, sort_keys=True) + "\n", encoding="utf-8")

            manifest = json.loads((ROOT / "contracts/deployment/mix-release-v1.e5-temporary.example.json").read_text(encoding="utf-8"))
            manifest["release_id"] = release_id
            manifest["git_sha"] = git_sha
            manifest["target_triple"] = target
            manifest["artifact"].update({
                "filename": archive.name,
                "size_bytes": archive.stat().st_size,
                "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
            })
            manifest["artifact"]["signature"].update({
                "key_id": "release-artifact-v1",
                "signature_filename": archive_signature.name,
                "signature_sha256": hashlib.sha256(archive_signature.read_bytes()).hexdigest(),
            })
            manifest["native_artifacts"] = [{
                "artifact_id": native_deployment["artifact_id"],
                "manifest_filename": native_deployment_path.name,
                "manifest_sha256": hashlib.sha256(native_deployment_path.read_bytes()).hexdigest(),
                "required": True,
            }]
            manifest["installation"]["release_directory"] = f"/opt/liqi/releases/{release_id}"
            manifest["manifest_signature"].update({
                "key_id": "release-manifest-v1",
                "signature_filename": f"{release_id}.json.sig",
            })
            manifest_path = publication / f"{release_id}.json"
            manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            manifest_signature = publication / manifest["manifest_signature"]["signature_filename"]
            sign(manifest_path, manifest_private, manifest_signature)

            runtime_result = {
                "schema_version": "runtime-artifact-result-v1",
                "git_sha": git_sha,
                "release_id": release_id,
                "observed_at": "2026-07-19T00:00:00Z",
                "status": "passed",
                "artifact_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
                "checks": [{"name": "synthetic-publication", "status": "passed"}],
                "blockers": [],
            }
            runtime_result_path = publication / f"{release_id}.runtime-artifact-result-v1.json"
            runtime_result_path.write_text(json.dumps(runtime_result, indent=2, sort_keys=True) + "\n", encoding="utf-8")

            result = {
                "schema_version": "liqi.runtime.linux-release-build-result/v1",
                "release_id": release_id,
                "git_sha": git_sha,
                "target_triple": target,
                "status": "passed",
                "build_on_host": False,
                "signing_mode": "sigstore-keyless-native-plus-ed25519-release",
                "manifest": {"filename": manifest_path.name, "sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest()},
                "artifact": {"filename": archive.name, "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(), "size_bytes": archive.stat().st_size},
                "artifact_signature": {"filename": archive_signature.name, "sha256": hashlib.sha256(archive_signature.read_bytes()).hexdigest()},
                "manifest_signature": {"filename": manifest_signature.name, "sha256": hashlib.sha256(manifest_signature.read_bytes()).hexdigest()},
                "runtime_artifact_result": {"filename": runtime_result_path.name, "sha256": hashlib.sha256(runtime_result_path.read_bytes()).hexdigest(), "status": "passed"},
                "native_provider_manifest": {"filename": native_provider_path.name, "sha256": hashlib.sha256(native_provider_path.read_bytes()).hexdigest()},
                "native_deployment_manifest": {"filename": native_deployment_path.name, "sha256": hashlib.sha256(native_deployment_path.read_bytes()).hexdigest()},
                "created_at": "2026-07-19T00:00:00Z",
            }
            result_path = publication / f"{release_id}.linux-release-build-result-v1.json"
            result_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")

            original_run = MODULE.run

            def bounded_run(argv: list[str], timeout: int = 600) -> str:
                if argv and argv[0] == sys.executable and str(MODULE.NATIVE_HANDOFF_VERIFIER) in argv:
                    return '{"status":"passed"}'
                return original_run(argv, timeout)

            with mock.patch.object(MODULE, "run", side_effect=bounded_run):
                verified = MODULE.verify(result_path, git_sha, target, release_trust, native_trust)
                self.assertEqual("passed", verified["status"])
                archive.write_bytes(b"tampered-release-archive")
                with self.assertRaisesRegex(ValueError, "SHA256 mismatch"):
                    MODULE.verify(result_path, git_sha, target, release_trust, native_trust)



if __name__ == "__main__":
    unittest.main()
