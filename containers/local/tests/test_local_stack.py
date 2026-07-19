from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
LOCAL = ROOT / "containers" / "local"


def load_validator():
    spec = importlib.util.spec_from_file_location("liqi_local_validator", LOCAL / "validate_local_stack.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class LocalContainerSourceTests(unittest.TestCase):
    def test_static_validator_passes(self) -> None:
        self.assertEqual(load_validator().validate(), [])

    def test_secret_materializer_reuses_and_rotates_explicitly(self) -> None:
        script = LOCAL / "bin" / "materialize-secrets.py"
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            first = subprocess.run(
                [sys.executable, str(script), "--state-dir", str(state)],
                check=True,
                capture_output=True,
                text=True,
            )
            first_result = json.loads(first.stdout)
            self.assertEqual(sorted(first_result["created"]), ["drain_token", "endpoint_secret", "probe_token"])
            values = {path.name: path.read_text(encoding="ascii") for path in (state / "secrets").iterdir()}
            manifest = json.loads((state / "secrets-manifest.json").read_text(encoding="utf-8"))
            self.assertNotIn(next(iter(values.values())).strip(), json.dumps(manifest))

            second = subprocess.run(
                [sys.executable, str(script), "--state-dir", str(state)],
                check=True,
                capture_output=True,
                text=True,
            )
            second_result = json.loads(second.stdout)
            self.assertEqual(sorted(second_result["reused"]), ["drain_token", "endpoint_secret", "probe_token"])
            self.assertEqual(values, {path.name: path.read_text(encoding="ascii") for path in (state / "secrets").iterdir()})

            subprocess.run(
                [sys.executable, str(script), "--state-dir", str(state), "--rotate"],
                check=True,
                capture_output=True,
                text=True,
            )
            rotated = {path.name: path.read_text(encoding="ascii") for path in (state / "secrets").iterdir()}
            self.assertTrue(all(rotated[name] != values[name] for name in values))

    def test_startup_does_not_rerun_completed_database_init(self) -> None:
        startup = (LOCAL / "bin" / "up.sh").read_text(encoding="utf-8")
        self.assertIn("compose up --no-deps db-init", startup)
        self.assertIn("compose up --detach --no-deps pgbouncer", startup)
        self.assertIn("compose up --detach --no-deps runtime", startup)

    def test_pgbouncer_version_matches_production_timeout_contract(self) -> None:
        sidecars = (LOCAL / "Dockerfile.sidecars").read_text(encoding="utf-8")
        config = (LOCAL / "config" / "pgbouncer.ini").read_text(encoding="utf-8")
        production = (
            ROOT / "infrastructure" / "packages" / "oracle-linux-9-aarch64-v1.json"
        ).read_text(encoding="utf-8")
        self.assertIn("PGBOUNCER_ALPINE_IMAGE=alpine:3.24.1@sha256:", sidecars)
        self.assertIn("pgbouncer=1.25.2-r0", sidecars)
        self.assertIn('"pgbouncer-1.25.2"', production)
        self.assertIn("transaction_timeout = 60", config)

    def test_local_database_authentication_is_explicitly_scoped(self) -> None:
        compose = (LOCAL / "compose.yaml").read_text(encoding="utf-8")
        init = (LOCAL / "bin" / "database-init.sh").read_text(encoding="utf-8")
        pgbouncer = (LOCAL / "config" / "pgbouncer.ini").read_text(encoding="utf-8")
        self.assertIn("POSTGRES_HOST_AUTH_METHOD: trust", compose)
        self.assertIn("internal: true", compose)
        self.assertNotIn("ports:\n      - 5432", compose)
        self.assertIn('"authentication_scope": "docker-internal-trust-only"', init)
        self.assertIn("auth_type = trust", pgbouncer)


if __name__ == "__main__":
    unittest.main()
