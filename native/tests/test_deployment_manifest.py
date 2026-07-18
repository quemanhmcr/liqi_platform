from __future__ import annotations

import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "native" / "scripts" / "prepare_deployment_manifest.py"
SPEC = importlib.util.spec_from_file_location("prepare_deployment_manifest", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class DeploymentManifestTest(unittest.TestCase):
    def provider_manifest(self, artifact: Path) -> dict:
        document = json.loads((ROOT / "contracts/native/examples/native-artifact-v1.example.json").read_text(encoding="utf-8"))
        document["artifact_path"] = artifact.name
        document["artifact_sha256"] = hashlib.sha256(artifact.read_bytes()).hexdigest()
        document["artifact_size_bytes"] = artifact.stat().st_size
        return document

    def test_maps_one_artifact_identity_to_deployment_contract(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifact = root / "libliqi_sequence_diff_nif.so"
            signature = root / "libliqi_sequence_diff_nif.so.sig"
            artifact.write_bytes(b"same-artifact-bytes")
            signature.write_bytes(b"ed25519-signature-fixture")
            provider = self.provider_manifest(artifact)

            result = MODULE.deployment_document(provider, artifact, signature, "native-signing-v1")
            schema = json.loads((ROOT / "contracts/deployment/native-artifact-v1.schema.json").read_text(encoding="utf-8"))
            errors = list(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(result))
            self.assertEqual([], errors)
            self.assertEqual(provider["artifact_sha256"], result["artifact"]["sha256"])
            self.assertEqual(provider["source_revision"], result["git_sha"])
            self.assertEqual(
                "lib/liqi_native-1.0.0/priv/native/libliqi_sequence_diff_nif.so",
                result["artifact"]["install_relative_path"],
            )
            self.assertEqual(["bin/liqi_platform", "eval"], result["load"]["probe_command"][:2])

    def test_rejects_artifact_identity_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifact = root / "libliqi_sequence_diff_nif.so"
            signature = root / "libliqi_sequence_diff_nif.so.sig"
            artifact.write_bytes(b"artifact")
            signature.write_bytes(b"signature")
            provider = self.provider_manifest(artifact)
            provider["artifact_sha256"] = "0" * 64
            with self.assertRaisesRegex(ValueError, "SHA256"):
                MODULE.deployment_document(provider, artifact, signature, "native-signing-v1")


if __name__ == "__main__":
    unittest.main()
