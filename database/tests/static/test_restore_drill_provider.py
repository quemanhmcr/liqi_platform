#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
import sys
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[3]
MODULE_PATH = ROOT / "database/tools/run_restore_drill_v1.py"
SPEC = importlib.util.spec_from_file_location("run_restore_drill_v1", MODULE_PATH)
assert SPEC and SPEC.loader
provider = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = provider
SPEC.loader.exec_module(provider)


class RestoreDrillProviderTests(unittest.TestCase):
    def test_step_document_is_closed_and_owner_attributed(self) -> None:
        step = provider.Step("elixir-read-only-probe", "Senior 1", "passed", 12, "file:///evidence/probe.json")
        self.assertEqual(
            {
                "name": "elixir-read-only-probe",
                "owner": "Senior 1",
                "status": "passed",
                "duration_ms": 12,
                "evidence_ref": "file:///evidence/probe.json",
            },
            step.document(),
        )

    def test_atomic_writer_emits_valid_json_and_replaces(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "result.json"
            provider.write_json_atomic(output, {"status": "failed"})
            provider.write_json_atomic(output, {"status": "passed"})
            self.assertEqual({"status": "passed"}, json.loads(output.read_text(encoding="utf-8")))
            self.assertEqual([], list(output.parent.glob(".*.tmp")))

    def test_restore_beam_probe_example_validates(self) -> None:
        schema = json.loads((ROOT / "contracts/database/restore-beam-probe-v1.schema.json").read_text(encoding="utf-8"))
        example = json.loads((ROOT / "contracts/database/restore-beam-probe-v1.example.json").read_text(encoding="utf-8"))
        errors = list(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(example))
        self.assertEqual([], errors)

    def test_source_has_no_object_storage_or_live_source_mutation(self) -> None:
        source = MODULE_PATH.read_text(encoding="utf-8")
        for forbidden in ("oci os ", "oci_objectstorage", "repo1-type=s3", "AWS_SHARED_CREDENTIALS_FILE"):
            self.assertNotIn(forbidden, source)
        self.assertIn('"source_database_mutated": False', source)
        self.assertIn('"production_traffic_changed": False', source)
        self.assertIn('"oci_mutated": False', source)
        self.assertIn("finally:", source)
        self.assertIn("self.cleanup()", source)


if __name__ == "__main__":
    unittest.main()
