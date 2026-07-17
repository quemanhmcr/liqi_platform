from __future__ import annotations

import json
import re
import tempfile
import unittest
from datetime import date
from pathlib import Path

from scripts.operations.scan_repository_secrets import scan_paths

ROOT = Path(__file__).resolve().parents[2]
POLICY = ROOT / "operations" / "release" / "dependency-policy-v0.json"


class SecurityPolicyTests(unittest.TestCase):
    def test_secret_scanner_rejects_key_and_password_dsn(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            base = Path(temporary)
            key = base / "leak.txt"
            key.write_text("-----BEGIN " + "PRIVATE KEY-----\nnot-a-real-key\n", encoding="utf-8")
            dsn = base / "dsn.txt"
            dsn.write_text("postgresql://user:" + "actual-secret@localhost/liqi\n", encoding="utf-8")
            failures = scan_paths([key, dsn])
            self.assertTrue(any("private key material" in failure for failure in failures))
            self.assertTrue(any("PostgreSQL DSN" in failure for failure in failures))

    def test_secret_scanner_allows_explicit_test_sentinel(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            path = Path(temporary) / "safe.txt"
            path.write_text("password=TEST_ONLY_REDACTION_VALUE\n", encoding="utf-8")
            self.assertEqual(scan_paths([path]), [])

    def test_dependency_policy_has_no_license_overlap(self) -> None:
        policy = json.loads(POLICY.read_text(encoding="utf-8"))
        self.assertFalse(set(policy["licenses"]["allow"]) & set(policy["licenses"]["deny"]))
        self.assertTrue(all(date.fromisoformat(item["expires_at"]) >= date.today() for item in policy["exceptions"]))

    def test_cargo_deny_policy_matches_private_workspace_contract(self) -> None:
        policy = json.loads(POLICY.read_text(encoding="utf-8"))
        cargo_deny = (ROOT / "operations/release/cargo-deny-v0.toml").read_text(encoding="utf-8")
        self.assertIn("ignore = true", cargo_deny)
        for license_id in ("CDLA-Permissive-2.0", "MIT-0"):
            self.assertIn(license_id, policy["licenses"]["allow"])
            self.assertIn(f'"{license_id}"', cargo_deny)

    def test_internal_path_dependencies_are_explicitly_versioned(self) -> None:
        manifests = sorted((ROOT / "crates").glob("*/Cargo.toml")) + sorted((ROOT / "services").glob("*/Cargo.toml"))
        unversioned: list[str] = []
        for manifest in manifests:
            for line_number, line in enumerate(manifest.read_text(encoding="utf-8").splitlines(), start=1):
                if re.search(r"=\s*\{[^}]*\bpath\s*=", line) and 'version = "0.1.0"' not in line:
                    unversioned.append(f"{manifest.relative_to(ROOT)}:{line_number}")
        self.assertEqual([], unversioned)

    def test_database_source_runner_is_not_executable_bit_dependent(self) -> None:
        runner = (ROOT / "database/tests/run-source-validation.sh").read_text(encoding="utf-8")
        self.assertIn('bash "$test_script"', runner)


if __name__ == "__main__":
    unittest.main()
