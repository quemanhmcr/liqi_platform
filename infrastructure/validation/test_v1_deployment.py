from __future__ import annotations

import hashlib
import importlib.util
from importlib.machinery import SourceFileLoader
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from infrastructure.deployment import build_host_bundle
from infrastructure.deployment import enable_backup_timers
from infrastructure.deployment import release_control
from infrastructure.deployment import prepare_mix_deployment
from infrastructure.deployment import configure_live_edge
from infrastructure.deployment import stage_mix_release
from infrastructure.validation import validate_v1_plan


def load_database_credential_provider():
    path = ROOT / "infrastructure/bin/liqi-configure-database-credentials"
    loader = SourceFileLoader("liqi_database_credentials", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    if spec is None:
        raise RuntimeError("cannot load database credential provider")
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class ReleaseBoundaryTests(unittest.TestCase):
    def test_database_readiness_uses_provider_schema_version(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "database.json"
            path.write_text(
                json.dumps({
                    "schemaVersion": "database-readiness-v0",
                    "ready": True,
                    "reason": "ready",
                    "currentVersion": 4,
                }),
                encoding="utf-8",
            )
            self.assertEqual(release_control.database_version(path), 4)

    def test_database_readiness_rejects_competing_field(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "database.json"
            path.write_text(
                json.dumps({
                    "contractVersion": "database-v0",
                    "ready": True,
                    "reason": "ready",
                    "currentVersion": 4,
                }),
                encoding="utf-8",
            )
            with self.assertRaises(RuntimeError):
                release_control.database_version(path)


    def test_database_readiness_accepts_committed_v1_contract(self) -> None:
        document = json.loads((ROOT / "contracts/database/migration-readiness-v1.example.json").read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "migration-readiness.json"
            path.write_text(json.dumps(document), encoding="utf-8")
            self.assertEqual(release_control.database_version(path), 8)

    def test_prepare_adapter_rejects_v0_migration_gap(self) -> None:
        descriptor = json.loads((ROOT / "contracts/deployment/release-target-v1.example.json").read_text(encoding="utf-8"))
        descriptor["release_id"] = "liqi-v0-example"
        descriptor["runtime_generation"] = "rust-v0"
        descriptor["runtime_config_path"] = None
        descriptor["credential_directory"] = None
        descriptor["required_credentials"] = []
        descriptor["database_compatibility"] = {
            "minimum_migration": 0,
            "maximum_migration": 4,
            "rollback_safe_through": 4,
            "database_rollback_allowed": False,
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rollback.json"
            path.write_text(json.dumps(descriptor), encoding="utf-8")
            with self.assertRaises(RuntimeError):
                prepare_mix_deployment.verify_rollback(path, "liqi-v0-example", 8)

    def test_runtime_credentials_come_from_committed_runtime_config(self) -> None:
        runtime_config = json.loads((ROOT / "contracts/runtime/examples/runtime-config-v1.json").read_text(encoding="utf-8"))
        names = prepare_mix_deployment.runtime_credentials(runtime_config)
        self.assertEqual(set(names), {"phoenix-secret-key-base", "database-role-urls", "drain-token", "platform-probe-token"})

    def test_rollback_compatibility_is_bounded(self) -> None:
        target = {"database_compatibility": {
            "minimum_migration": 4,
            "maximum_migration": 5,
            "rollback_safe_through": 5,
            "database_rollback_allowed": False,
        }}
        rollback = {"database_compatibility": {
            "minimum_migration": 3,
            "maximum_migration": 4,
            "rollback_safe_through": 4,
            "database_rollback_allowed": False,
        }}
        release_control.compatibility(target, rollback, 4)
        with self.assertRaises(RuntimeError):
            release_control.compatibility(target, rollback, 5)

    def test_release_and_native_paths_reject_escape(self) -> None:
        for value in ("../bin/liqi", "/bin/liqi", "lib/../../escape"):
            with self.subTest(value=value), self.assertRaises(RuntimeError):
                stage_mix_release.safe_relative(value)
        self.assertEqual(stage_mix_release.safe_relative("lib/liqi/native.so").as_posix(), "lib/liqi/native.so")


class DatabaseCredentialProviderTests(unittest.TestCase):
    def test_role_url_and_pgpass_escape_are_bounded(self) -> None:
        provider = load_database_credential_provider()
        value = "Abc:def\\ghi-123456789"
        url = provider.role_url("liqi_api", value)
        self.assertTrue(url.startswith("postgresql://liqi_api:"))
        self.assertIn("%3A", url)
        line = provider.pgpass_line("127.0.0.1", "6432", "liqi_api", value)
        self.assertIn("\\:", line)
        self.assertIn("\\\\", line)

    def test_result_document_contains_no_secret_values(self) -> None:
        provider = load_database_credential_provider()
        document = provider.result_document("planned", "0" * 64, None, False)
        serialized = json.dumps(document)
        self.assertNotIn("postgresql://", serialized)
        self.assertEqual(document["status"], "planned")


class HostBundleTests(unittest.TestCase):
    def test_inventory_is_bounded_and_has_unique_targets(self) -> None:
        targets = [record[1] for record in build_host_bundle.FILES]
        self.assertEqual(len(targets), len(set(targets)))
        self.assertGreaterEqual(len(targets), 10)
        schema = json.loads((ROOT / "contracts/infrastructure/host-bundle-v1.schema.json").read_text(encoding="utf-8"))
        self.assertLessEqual(len(targets), schema["properties"]["files"]["maxItems"])
        self.assertIn("/etc/systemd/system/liqi-api.service", targets)
        self.assertIn("/etc/systemd/system/liqi-beam.service", targets)
        self.assertIn("/etc/systemd/system/liqi-database-credentials.service", targets)
        self.assertIn("/usr/local/libexec/liqi-configure-database-credentials", targets)
        self.assertIn("/usr/local/lib/liqi-database/database/bin/backup.sh", targets)




class PlanValidationTests(unittest.TestCase):
    def test_initial_plan_action_counts_are_exact(self) -> None:
        changes = []
        for resource_type, count in validate_v1_plan.EXPECTED_COUNTS.items():
            for index in range(count):
                changes.append({
                    "mode": "managed",
                    "type": resource_type,
                    "address": f"module.v1_live.{resource_type}.example[{index}]",
                    "change": {"actions": ["create"]},
                })
        validate_v1_plan.validate_actions({"resource_changes": changes}, allow_reserved_ip=False)
        changes[0]["change"]["actions"] = ["update"]
        with self.assertRaises(AssertionError):
            validate_v1_plan.validate_actions({"resource_changes": changes}, allow_reserved_ip=False)

    def test_public_ingress_is_exactly_80_and_443(self) -> None:
        def rule(port: int) -> dict[str, object]:
            return {
                "type": "oci_core_network_security_group_security_rule",
                "values": {
                    "direction": "INGRESS",
                    "source": "0.0.0.0/0",
                    "protocol": "6",
                    "tcp_options": [{"destination_port_range": [{"min": port, "max": port}]}],
                },
            }
        validate_v1_plan.validate_ingress([rule(80), rule(443)])
        with self.assertRaises(AssertionError):
            validate_v1_plan.validate_ingress([rule(22), rule(443)])

    def test_instance_metadata_is_bounded_and_hardened(self) -> None:
        import base64
        import gzip

        cloud_init = b"liqi-bootstrap-host liqi-install-host-bundle sshd.service sshd.socket"
        encoded = base64.b64encode(gzip.compress(cloud_init, mtime=0)).decode("ascii")
        resources = [{
            "type": "oci_core_instance",
            "values": {
                "shape": "VM.Standard.A1.Flex",
                "shape_config": [{"ocpus": 4, "memory_in_gbs": 24}],
                "source_details": [{"boot_volume_size_in_gbs": 50}],
                "metadata": {"user_data": encoded},
            },
        }]
        rendered = validate_v1_plan.validate_instance(resources)
        self.assertIn("liqi-bootstrap-host", rendered)



class EdgeProviderTests(unittest.TestCase):
    def test_edge_rendering_uses_committed_runtime_config(self) -> None:
        activation = json.loads((ROOT / "contracts/deployment/activation-v1.example.json").read_text(encoding="utf-8"))
        runtime_config = json.loads((ROOT / "contracts/runtime/examples/runtime-config-v1.json").read_text(encoding="utf-8"))
        activation["release_id"] = runtime_config["releaseId"]
        activation["status"] = "passed"
        activation["state"] = "health-gated"
        activation["traffic_enabled"] = False
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            activation_path = root / "activation.json"
            runtime_path = root / "runtime.json"
            rendered = root / "Caddyfile"
            evidence = root / "endpoint.json"
            activation_path.write_text(json.dumps(activation), encoding="utf-8")
            runtime_path.write_text(json.dumps(runtime_config), encoding="utf-8")
            argv = [
                "configure_live_edge.py",
                "--hostname", "example.com",
                "--acme-email", "operator@example.com",
                "--activation-evidence", str(activation_path),
                "--runtime-config", str(runtime_path),
                "--template", str(ROOT / "infrastructure/caddy/Caddyfile.v1-live.tftpl"),
                "--rendered-output", str(rendered),
                "--evidence-output", str(evidence),
            ]
            with patch.object(sys, "argv", argv), patch("infrastructure.deployment.configure_live_edge.shutil.which", return_value=None):
                self.assertEqual(configure_live_edge.main(), 0)
            text = rendered.read_text(encoding="utf-8")
            self.assertIn("127.0.0.1:4100", text)
            self.assertIn(f"max_size {runtime_config['requests']['bodyBytes']}B", text)
            result = json.loads(evidence.read_text(encoding="utf-8"))
            self.assertEqual(result["websocket"]["path"], runtime_config["http"]["websocketPath"] + "/websocket")
            self.assertEqual(result["backend"]["address"], "127.0.0.1:4100")

class BackupTimerTests(unittest.TestCase):
    def test_dry_run_accepts_matching_real_contract_examples(self) -> None:
        backup_source = ROOT / "contracts/platform/database-backup-status-v0.example.json"
        restore_source = ROOT / "contracts/platform/database-restore-result-v0.example.json"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            backup = root / "backup.json"
            restore = root / "restore.json"
            backup.write_bytes(backup_source.read_bytes())
            restore.write_bytes(restore_source.read_bytes())
            backup_checksum = root / "backup.sha256"
            restore_checksum = root / "restore.sha256"
            backup_checksum.write_text(f"{hashlib.sha256(backup.read_bytes()).hexdigest()}  {backup.name}\n", encoding="utf-8")
            restore_checksum.write_text(f"{hashlib.sha256(restore.read_bytes()).hexdigest()}  {restore.name}\n", encoding="utf-8")
            output = root / "result.json"
            argv = [
                "enable_backup_timers.py",
                "--backup-status", str(backup),
                "--backup-status-checksum", str(backup_checksum),
                "--restore-result", str(restore),
                "--restore-result-checksum", str(restore_checksum),
                "--output", str(output),
            ]
            with patch.object(sys, "argv", argv):
                self.assertEqual(enable_backup_timers.main(), 0)
            result = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(result["status"], "validated")
            self.assertFalse(result["mutation_performed"])

    def test_dry_run_rejects_non_recovery_ready_backup(self) -> None:
        backup_doc = json.loads((ROOT / "contracts/platform/database-backup-status-v0.example.json").read_text(encoding="utf-8"))
        backup_doc["recoveryReady"] = False
        backup_doc["reasons"] = ["no-backup"]
        restore_source = ROOT / "contracts/platform/database-restore-result-v0.example.json"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            backup = root / "backup.json"
            restore = root / "restore.json"
            backup.write_text(json.dumps(backup_doc), encoding="utf-8")
            restore.write_bytes(restore_source.read_bytes())
            backup_checksum = root / "backup.sha256"
            restore_checksum = root / "restore.sha256"
            backup_checksum.write_text(f"{hashlib.sha256(backup.read_bytes()).hexdigest()}\n", encoding="utf-8")
            restore_checksum.write_text(f"{hashlib.sha256(restore.read_bytes()).hexdigest()}\n", encoding="utf-8")
            argv = [
                "enable_backup_timers.py",
                "--backup-status", str(backup),
                "--backup-status-checksum", str(backup_checksum),
                "--restore-result", str(restore),
                "--restore-result-checksum", str(restore_checksum),
                "--output", str(root / "result.json"),
            ]
            with patch.object(sys, "argv", argv), self.assertRaises(SystemExit):
                enable_backup_timers.main()


if __name__ == "__main__":
    unittest.main()
