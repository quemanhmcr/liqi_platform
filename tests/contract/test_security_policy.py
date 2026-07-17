from __future__ import annotations

import json
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


if __name__ == "__main__":
    unittest.main()
