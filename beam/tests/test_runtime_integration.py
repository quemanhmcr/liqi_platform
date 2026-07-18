from __future__ import annotations

import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import quote, urlunsplit

from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "beam/scripts/run_v1_integration.py"
spec = importlib.util.spec_from_file_location("run_v1_integration", MODULE_PATH)
assert spec and spec.loader
integration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(integration)


def admin_dsn(
    host: str = "127.0.0.1",
    port: int = 5432,
    database: str = "postgres",
    credential: str | None = None,
    query: str = "",
    fragment: str = "",
) -> str:
    userinfo = "postgres"
    if credential is not None:
        userinfo += ":" + quote(credential, safe="")
    return urlunsplit(
        ("postgresql", f"{userinfo}@{host}:{port}", f"/{database}", query, fragment)
    )


class RuntimeIntegrationTest(unittest.TestCase):
    def test_admin_url_accepts_loopback_trust_admin_databases(self):
        for database in ("postgres", "liqi_v1_ci"):
            parsed = integration.parse_admin_url(admin_dsn(port=55432, database=database))
            self.assertEqual(parsed["host"], "127.0.0.1")
            self.assertEqual(parsed["port"], "55432")
            self.assertEqual(parsed["username"], "postgres")
            self.assertEqual(parsed["admin_database"], database)

        rejected = [
            admin_dsn(host="database.internal"),
            admin_dsn(credential="test-value"),
            admin_dsn(database="liqi_v1_test_runtime_abcd"),
            admin_dsn(query="sslmode=disable"),
            admin_dsn(fragment="fragment"),
        ]
        for value in rejected:
            with self.subTest(value=value), self.assertRaises(ValueError):
                integration.parse_admin_url(value)

    def test_role_urls_are_exact_password_free_runtime_roles(self):
        admin = integration.parse_admin_url(admin_dsn())
        urls = integration.role_urls(admin, "liqi_v1_test_runtime_abcd")
        self.assertEqual(set(urls), {"command", "realtime", "worker"})
        self.assertEqual(
            urls["command"],
            "postgresql://liqi_api@127.0.0.1:5432/liqi_v1_test_runtime_abcd",
        )
        self.assertIn("liqi_realtime@", urls["realtime"])
        self.assertIn("liqi_worker@", urls["worker"])
        self.assertNotIn(":test-value@", json.dumps(urls))

    def test_redaction_removes_full_database_inputs(self):
        value = integration.redact(
            "failed role-url-value and admin-url-value",
            ["role-url-value", "admin-url-value"],
        )
        self.assertNotIn("url-value", value)
        self.assertIn("<redacted>", value)

    def test_dirty_worktree_fails_before_input_or_tooling(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "result.json"
            with patch.object(integration, "clean_worktree", return_value=False), patch.object(
                integration, "git_sha", return_value="d" * 40
            ):
                self.assertEqual(integration.main_with_args(["--output", str(output)]), 1)
            self.assert_schema(output, "failed")

    def test_missing_input_emits_schema_valid_blocked_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "result.json"
            with patch.dict(os.environ, {}, clear=True), patch.object(
                integration, "clean_worktree", return_value=True
            ), patch.object(integration, "git_sha", return_value="a" * 40):
                self.assertEqual(integration.main_with_args(["--output", str(output)]), 2)
            self.assert_schema(output, "blocked")

    def test_missing_tooling_is_blocked_after_safe_input_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "result.json"
            environment = {"LIQI_TEST_DATABASE_URL": admin_dsn()}
            with patch.dict(os.environ, environment, clear=True), patch.object(
                integration, "clean_worktree", return_value=True
            ), patch.object(integration, "git_sha", return_value="b" * 40), patch.object(
                integration.shutil, "which", return_value=None
            ):
                self.assertEqual(integration.main_with_args(["--output", str(output)]), 2)
            document = self.assert_schema(output, "blocked")
            self.assertIn(
                "required disposable database commands are missing", document["blockers"][0]
            )

    def test_unsafe_database_name_is_failed_before_tooling(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "result.json"
            environment = {
                "LIQI_TEST_DATABASE_URL": admin_dsn(),
                "LIQI_TEST_DATABASE": "liqi",
            }
            with patch.dict(os.environ, environment, clear=True), patch.object(
                integration, "clean_worktree", return_value=True
            ), patch.object(integration, "git_sha", return_value="c" * 40):
                self.assertEqual(integration.main_with_args(["--output", str(output)]), 1)
            self.assert_schema(output, "failed")

    def assert_schema(self, output: Path, status: str) -> dict[str, object]:
        document = json.loads(output.read_text(encoding="utf-8"))
        schema = json.loads(integration.RESULT_SCHEMA.read_text(encoding="utf-8"))
        errors = list(
            Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(document)
        )
        self.assertEqual(errors, [])
        self.assertEqual(document["status"], status)
        return document


if __name__ == "__main__":
    unittest.main()
