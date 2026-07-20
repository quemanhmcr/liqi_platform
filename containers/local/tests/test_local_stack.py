from __future__ import annotations

import importlib.util
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
LOCAL = ROOT / "containers" / "local"


def load_validator():
    spec = importlib.util.spec_from_file_location("liqi_local_validator", LOCAL / "validate_local_stack.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def load_materializer():
    spec = importlib.util.spec_from_file_location("liqi_local_materializer", LOCAL / "bin" / "materialize-secrets.py")
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
            secret_paths = sorted((state / "secrets").iterdir())
            values = {path.name: path.read_text(encoding="ascii") for path in secret_paths}
            self.assertTrue(all(value.strip() not in first.stdout for value in values.values()))
            self.assertTrue(all(value.strip() not in first.stderr for value in values.values()))
            manifest = json.loads((state / "secrets-manifest.json").read_text(encoding="utf-8"))
            self.assertNotIn(next(iter(values.values())).strip(), json.dumps(manifest))
            self.assertTrue(all({"sha256", "bytes", "gid", "mode"} == set(item) for item in manifest["files"].values()))
            if os.name == "posix":
                self.assertEqual(stat.S_IMODE((state / "secrets").stat().st_mode), 0o700)
                self.assertEqual({stat.S_IMODE(path.stat().st_mode) for path in secret_paths}, {0o640})
                self.assertEqual({path.stat().st_gid for path in secret_paths}, {10001})

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

    def test_secret_materializer_cleans_secure_temporary_on_ownership_failure(self) -> None:
        materializer = load_materializer()
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "endpoint_secret"

            def fail_after_mode_check(path: Path) -> None:
                if os.name == "posix":
                    self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
                raise PermissionError("simulated ownership failure")

            with mock.patch.object(materializer, "secure_secret", side_effect=fail_after_mode_check):
                with self.assertRaises(PermissionError):
                    materializer.write_secret(target, 128)
            self.assertEqual(list(Path(directory).iterdir()), [])

    @unittest.skipUnless(os.name == "posix", "POSIX directory modes are required")
    def test_secret_materializer_rejects_insecure_state_directory_without_materializing(self) -> None:
        script = LOCAL / "bin" / "materialize-secrets.py"
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            state.chmod(0o755)
            result = subprocess.run(
                [sys.executable, str(script), "--state-dir", str(state)],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.stdout, "")
            self.assertIn("must not grant group or other access", result.stderr)
            self.assertFalse((state / "secrets").exists())

    @unittest.skipUnless(os.name == "posix", "POSIX symbolic-link semantics are required")
    def test_secret_materializer_rejects_symlink_state_directory(self) -> None:
        script = LOCAL / "bin" / "materialize-secrets.py"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "target"
            target.mkdir(mode=0o700)
            link = root / "state-link"
            link.symlink_to(target, target_is_directory=True)
            result = subprocess.run(
                [sys.executable, str(script), "--state-dir", str(link)],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.stdout, "")
            self.assertIn("symbolic link", result.stderr)
            self.assertFalse((target / "secrets").exists())

    def test_startup_does_not_rerun_completed_database_init(self) -> None:
        startup = (LOCAL / "bin" / "up.sh").read_text(encoding="utf-8")
        self.assertIn("compose up --no-deps db-init", startup)
        self.assertIn("compose up --detach --no-deps pgbouncer", startup)
        self.assertIn("compose up --detach --no-deps runtime", startup)
        self.assertIn("compose up --detach --no-deps ingress", startup)

    def test_internal_runtime_uses_bounded_loopback_ingress(self) -> None:
        compose = (LOCAL / "compose.yaml").read_text(encoding="utf-8")
        self.assertIn("  ingress:\n", compose)
        self.assertIn("TCP:pod:8080", compose)
        self.assertIn("127.0.0.1:${LIQI_LOCAL_HTTP_PORT:-4100}:8080", compose)
        pod_section = compose.split("  pod:\n", 1)[1].split("  ingress:\n", 1)[0]
        ingress_section = compose.split("  ingress:\n", 1)[1].split("  pgbouncer:\n", 1)[0]
        self.assertNotIn("ports:", pod_section)
        self.assertIn("      - backend\n      - edge", ingress_section)
        self.assertIn("internal: true", compose)

    def test_non_root_runtime_reads_only_fixed_group_local_secrets(self) -> None:
        compose = (LOCAL / "compose.yaml").read_text(encoding="utf-8")
        materializer = (LOCAL / "bin" / "materialize-secrets.py").read_text(encoding="utf-8")
        runtime_dockerfile = (LOCAL / "Dockerfile.runtime").read_text(encoding="utf-8")
        self.assertIn("USER 10001:10001", runtime_dockerfile)
        self.assertNotIn("group_add:", compose)
        self.assertNotIn("LIQI_LOCAL_SECRET_GID", compose)
        self.assertIn("SECRET_MODE = 0o640", materializer)
        self.assertIn("RUNTIME_GID = 10001", materializer)
        self.assertIn("os.chown(path, -1, RUNTIME_GID)", materializer)
        self.assertIn("mode=0o700", materializer)
        self.assertIn('sudo --non-interactive "$python_bin"', (LOCAL / "bin" / "up.sh").read_text(encoding="utf-8"))

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
