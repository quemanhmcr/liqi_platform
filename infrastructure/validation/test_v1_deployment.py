from __future__ import annotations

import hashlib
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
from infrastructure.deployment import stage_mix_release
from infrastructure.validation import validate_v1_plan


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


class HostBundleTests(unittest.TestCase):
    def test_inventory_is_bounded_and_has_unique_targets(self) -> None:
        targets = [record[1] for record in build_host_bundle.FILES]
        self.assertEqual(len(targets), len(set(targets)))
        self.assertGreaterEqual(len(targets), 10)
        self.assertLessEqual(len(targets), 128)
        self.assertIn("/etc/systemd/system/liqi-api.service", targets)
        self.assertIn("/etc/systemd/system/liqi-beam.service", targets)
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
