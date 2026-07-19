from __future__ import annotations

import hashlib
import importlib.util
from importlib.machinery import SourceFileLoader
import json
import sys
import subprocess
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from infrastructure.deployment import adopt_v1_live_state
from infrastructure.deployment import build_host_bundle
from infrastructure.deployment import enable_backup_timers
from infrastructure.deployment import host_package_manifest
from infrastructure.deployment import release_control
from infrastructure.deployment import prepare_mix_deployment
from infrastructure.deployment import configure_live_edge
from infrastructure.deployment import discover_v1_live_adoption
from infrastructure.deployment import stage_mix_release
from infrastructure.validation import validate_adoption_result
from infrastructure.validation import validate_v1_plan




def load_host_bundle_installer():
    path = ROOT / "infrastructure/bin/liqi-install-host-bundle"
    loader = SourceFileLoader("liqi_host_bundle_installer", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    if spec is None:
        raise RuntimeError("cannot load host bundle installer")
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {"grp": types.ModuleType("grp"), "pwd": types.ModuleType("pwd")}):
        loader.exec_module(module)
    return module

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


    def test_runtime_artifact_result_must_bind_exact_archive(self) -> None:
        provider = {"artifact": {"sha256": "a" * 64}}
        result = {"status": "passed", "blockers": [], "release_id": "liqi-v1-example", "git_sha": "b" * 40, "artifact_sha256": "a" * 64}
        stage_mix_release.verify_runtime_result(result, provider, "liqi-v1-example", "b" * 40)
        result["artifact_sha256"] = "c" * 64
        with self.assertRaises(RuntimeError):
            stage_mix_release.verify_runtime_result(result, provider, "liqi-v1-example", "b" * 40)

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

    def test_native_provider_target_must_match_mix_release_target(self) -> None:
        document = json.loads((ROOT / "contracts/native/examples/native-artifact-v1.x86_64.example.json").read_text(encoding="utf-8"))
        document["source_revision"] = "a" * 40
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "native.json"
            path.write_text(json.dumps(document), encoding="utf-8")
            accepted = prepare_mix_deployment.load_native_provider(path, "a" * 40, "x86_64-unknown-linux-gnu")
            self.assertEqual(accepted["architecture"], "x86_64")
            with self.assertRaisesRegex(RuntimeError, "target"):
                prepare_mix_deployment.load_native_provider(path, "a" * 40, "aarch64-unknown-linux-gnu")

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

    def test_target_inventories_select_exactly_one_package_manifest(self) -> None:
        expected = {
            "aarch64-unknown-linux-gnu": "infrastructure/packages/oracle-linux-9-aarch64-v1.json",
            "x86_64-unknown-linux-gnu": "infrastructure/packages/oracle-linux-9-x86_64-v1.json",
        }
        for target, source in expected.items():
            records = build_host_bundle.files_for_target(target)
            package_records = [item for item in records if item[1] == "/usr/local/share/liqi/host-packages-v1.json"]
            self.assertEqual([(source, "/usr/local/share/liqi/host-packages-v1.json", 0o640, "root", "liqi")], package_records)
            self.assertEqual(len({item[1] for item in records}), len(records))

    def test_host_package_manifests_bind_architecture_and_urls(self) -> None:
        cases = [
            ("aarch64", "infrastructure/packages/oracle-linux-9-aarch64-v1.json", "aarch64-unknown-linux-gnu", "linux_arm64"),
            ("x86_64", "infrastructure/packages/oracle-linux-9-x86_64-v1.json", "x86_64-unknown-linux-gnu", "linux_amd64"),
        ]
        for architecture, relative, target, url_token in cases:
            document = json.loads((ROOT / relative).read_text(encoding="utf-8"))
            values = host_package_manifest.settings(document, architecture)
            self.assertEqual([architecture, target], values[:2])
            self.assertTrue(any(url_token in value for value in values))
            other = "x86_64" if architecture == "aarch64" else "aarch64"
            with self.assertRaisesRegex(ValueError, "architecture"):
                host_package_manifest.settings(document, other)

    def test_host_bundle_target_must_match_host_architecture(self) -> None:
        installer = load_host_bundle_installer()
        cases = [
            ("contracts/infrastructure/host-bundle-v1.example.json", "aarch64"),
            ("contracts/infrastructure/host-bundle-v1.x86_64.example.json", "x86_64"),
        ]
        for relative, architecture in cases:
            document = json.loads((ROOT / relative).read_text(encoding="utf-8"))
            accepted = installer.validate_manifest(document, document["bundle_id"], document["signing"]["key_id"], architecture)
            self.assertEqual(document["target_triple"], accepted["target_triple"])
            other = "x86_64" if architecture == "aarch64" else "aarch64"
            with self.assertRaisesRegex(RuntimeError, "architecture"):
                installer.validate_manifest(document, document["bundle_id"], document["signing"]["key_id"], other)




class StateBackendBootstrapTests(unittest.TestCase):
    def test_lock_test_uses_isolated_admin_owned_database(self) -> None:
        source = (ROOT / "infrastructure/management/state-postgres/scripts/test-locking.sh").read_text(encoding="utf-8")
        for token in (
            "STATE_ADMIN_SERVICE", "lock test database must never be the live state database",
            "createdb", "dropdb", "--owner=", "GRANT CREATE ON SCHEMA public",
            "lock test requires a PostgreSQL connection URI",
            "unset PGSERVICEFILE PGPASSFILE", "first contender failed before lock verification",
            '"isolated_database":True',
        ):
            self.assertIn(token, source)
        self.assertNotIn("GRANT CREATE ON SCHEMA public", source.split("dbname=$lock_database", 1)[0])
        lock_main = (ROOT / "infrastructure/management/state-postgres/lock-test/main.tf").read_text(encoding="utf-8")
        self.assertIn('interpreter = ["bash", "-c"]', lock_main)



    def test_protected_environment_enforces_tls_and_encryption_without_logging_material(self) -> None:
        source = (ROOT / "infrastructure/management/state-postgres/scripts/with-protected-environment.sh").read_text(encoding="utf-8")
        for token in (
            "STATE_RUNTIME_CREDENTIAL_FILE", "STATE_ENCRYPTION_PASSPHRASE_FILE",
            "32-byte or 48-byte lowercase hex", "pg_root_cert_native", "cygpath -m",
            "sslmode=verify-full", "PG_SKIP_SCHEMA_CREATION", "PG_SKIP_TABLE_CREATION",
            "PG_SKIP_INDEX_CREATION", "pbkdf2", "aes_gcm", "600000", "sha512",
            'unset PGSERVICEFILE PGPASSFILE', 'exec "$@"',
        ):
            self.assertIn(token, source)
        self.assertNotIn("set -x", source)
        self.assertNotIn("echo $runtime_credential", source)
        self.assertNotIn("echo $encryption_passphrase", source)

    def test_runtime_passfile_is_optional_protected_and_not_reported(self) -> None:
        source = (ROOT / "infrastructure/management/state-postgres/scripts/bootstrap.sh").read_text(encoding="utf-8")
        self.assertIn('STATE_RUNTIME_PASSFILE', source)
        self.assertIn('protect_file "$STATE_RUNTIME_PASSFILE" 600', source)
        self.assertIn("printf '%s:%s:%s:%s:%s\\n'", source)
        result_line = next(line for line in source.splitlines() if 'ready-for-first-tofu-init' in line)
        self.assertNotIn('role_credential', result_line)
        self.assertNotIn('STATE_RUNTIME_PASSFILE', result_line)


class AdoptionStateTests(unittest.TestCase):
    def test_state_ids_preserve_module_addresses_and_ids(self) -> None:
        state = {
            "resources": [
                {
                    "module": "module.v1_live",
                    "type": "oci_core_vcn",
                    "name": "main",
                    "instances": [{"schema_version": 0, "attributes": {"id": "ocid1.vcn.oc1.example"}}],
                },
                {
                    "module": "module.v1_live",
                    "type": "oci_core_public_ip",
                    "name": "reserved",
                    "instances": [{"index_key": 0, "attributes": {"id": "ocid1.publicip.oc1.example"}}],
                },
            ]
        }
        self.assertEqual(
            adopt_v1_live_state.state_ids(state),
            {
                "module.v1_live.oci_core_vcn.main": "ocid1.vcn.oc1.example",
                "module.v1_live.oci_core_public_ip.reserved[0]": "ocid1.publicip.oc1.example",
            },
        )

    def test_redaction_removes_oci_identifiers(self) -> None:
        redacted = adopt_v1_live_state.redact("failure for ocid1.instance.oc1.ap-singapore-2.secret-value")
        self.assertNotIn("secret-value", redacted)
        self.assertIn("<oci-id-redacted>", redacted)

    def test_oci_nlb_paginated_collection_returns_items(self) -> None:
        completed = subprocess.CompletedProcess(args=[], returncode=0, stdout='{"data":{"items":[]}}', stderr="")
        with patch("infrastructure.deployment.discover_v1_live_adoption.subprocess.run", return_value=completed):
            self.assertEqual(discover_v1_live_adoption.oci("DEFAULT", "ap-singapore-2", "nlb", "network-load-balancer", "list"), [])

    def test_oci_empty_list_is_empty_but_empty_get_fails(self) -> None:
        completed = subprocess.CompletedProcess(["oci"], 0, stdout="", stderr="")
        with patch("infrastructure.deployment.discover_v1_live_adoption.subprocess.run", return_value=completed):
            self.assertEqual(discover_v1_live_adoption.oci("DEFAULT", "ap-singapore-2", "bv", "volume", "list"), [])
            with self.assertRaises(RuntimeError):
                discover_v1_live_adoption.oci("DEFAULT", "ap-singapore-2", "bv", "volume", "get")

    def test_exact_adoption_result_requires_executed_pass(self) -> None:
        document = json.loads((ROOT / "contracts/infrastructure/adoption-result-v1.example.json").read_text(encoding="utf-8"))
        document.update({
            "git_sha": "a" * 40,
            "operation": "execute",
            "approval_reference": "APPROVAL-1",
            "status": "passed",
            "state_mutation_performed": True,
        })
        validate_adoption_result.validate_result(document, "a" * 40)
        document["state_mutation_performed"] = False
        with self.assertRaises(ValueError):
            validate_adoption_result.validate_result(document, "a" * 40)
        document["state_mutation_performed"] = True
        document["operation"] = "validate"
        with self.assertRaises(ValueError):
            validate_adoption_result.validate_result(document, "a" * 40)

    def test_plan_and_apply_bind_state_adoption(self) -> None:
        plan = (ROOT / "infrastructure/deployment/plan_v1_live.sh").read_text(encoding="utf-8")
        apply = (ROOT / "infrastructure/deployment/apply_v1_live.sh").read_text(encoding="utf-8")
        self.assertIn("validate_adoption_result.py", plan)
        self.assertIn("validate_pre_apply_readiness.py", plan)
        for token in ("adoption_result_sha256", "pre_apply_readiness_sha256", "linux_release_build_result_sha256", "rollback_target_sha256"):
            self.assertIn(token, plan)
        self.assertIn("approved apply requires an adopt-existing plan", plan)
        self.assertIn('doc.get("capacity_profile") != "e5-temporary"', apply)
        self.assertIn('doc.get("plan_mode") != "adopt-existing"', apply)
        self.assertIn("pre-apply readiness digest mismatch", apply)
        self.assertIn("pre-apply readiness/plan binding mismatch", apply)
        self.assertIn('for field in ("plan_json", "validation")', apply)
        for token in (
            "live plan output directory must not already exist",
            "live plan output must remain outside the source repository",
            "input-state-backend-evidence.json",
            "input-adoption-result.json",
            "input-pre-apply-readiness.json",
            "input-live.tfvars",
            "install -m 0600",
        ):
            self.assertIn(token, plan)
        self.assertIn("--pre-apply-readiness", apply)
        self.assertIn("plan-result-v1.schema.json", plan)
        self.assertIn("plan-result-v1.schema.json", apply)
        self.assertIn("apply-result-v1.schema.json", apply)
        self.assertIn("oci-live-v1.schema.json", apply)
        self.assertIn("OCI live output identity does not match approved apply", apply)
        self.assertIn('mutation.get("plan_sha256") is not None', apply)
        self.assertNotIn('mutation.get("plan_sha256") != plan["saved_plan"]["sha256"]', apply)


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

    def test_adoption_actions_reject_replacement(self) -> None:
        changes = [
            {
                "mode": "managed",
                "type": "oci_core_instance",
                "address": "module.v1_live.oci_core_instance.host",
                "change": {"actions": ["update"]},
            },
            {
                "mode": "managed",
                "type": "oci_core_volume",
                "address": "module.v1_live.oci_core_volume.data",
                "change": {"actions": ["create"]},
            },
        ]
        validate_v1_plan.validate_actions(
            {"resource_changes": changes}, allow_reserved_ip=False, plan_mode="adopt-existing"
        )
        changes[0]["change"]["actions"] = ["delete", "create"]
        with self.assertRaises(AssertionError):
            validate_v1_plan.validate_actions(
                {"resource_changes": changes}, allow_reserved_ip=False, plan_mode="adopt-existing"
            )

    def test_e5_instance_profile_is_explicit(self) -> None:
        import base64
        import gzip

        cloud_init = b"liqi-bootstrap-host liqi-install-host-bundle liqi-configure-bastion-ssh"
        encoded = base64.b64encode(gzip.compress(cloud_init, mtime=0)).decode("ascii")
        resources = [{
            "type": "oci_core_instance",
            "values": {
                "shape": "VM.Standard.E5.Flex",
                "shape_config": [{"ocpus": 4, "memory_in_gbs": 24}],
                "source_details": [{"boot_volume_size_in_gbs": 200}],
                "metadata": {"user_data": encoded},
                "create_vnic_details": [{"assign_public_ip": "false"}],
                "agent_config": [{"plugins_config": [{"name": "Compute Instance Run Command", "desired_state": "ENABLED"}]}],
            },
        }]
        validate_v1_plan.validate_instance(resources, "e5-temporary")
        resources[0]["values"]["shape"] = "VM.Standard.A1.Flex"
        with self.assertRaises(AssertionError):
            validate_v1_plan.validate_instance(resources, "e5-temporary")

    def test_public_ingress_is_exactly_nlb_80_and_443_with_exact_bastion_ssh(self) -> None:
        def rule(address: str, port: int, source: str, source_type: str = "CIDR_BLOCK") -> dict[str, object]:
            return {
                "address": address,
                "type": "oci_core_network_security_group_security_rule",
                "values": {
                    "direction": "INGRESS",
                    "source": source,
                    "source_type": source_type,
                    "protocol": "6",
                    "tcp_options": [{"destination_port_range": [{"min": port, "max": port}]}],
                },
            }
        resources = [
            rule('module.v1_live.oci_core_network_security_group_security_rule.public_edge_ingress["http"]', 80, "0.0.0.0/0"),
            rule('module.v1_live.oci_core_network_security_group_security_rule.public_edge_ingress["https"]', 443, "0.0.0.0/0"),
            rule('module.v1_live.oci_core_network_security_group_security_rule.bastion_ssh_ingress["10.42.20.100/32"]', 22, "10.42.20.100/32"),
            rule('module.v1_live.oci_core_network_security_group_security_rule.bastion_ssh_ingress["10.42.20.109/32"]', 22, "10.42.20.109/32"),
            rule('module.v1_live.oci_core_network_security_group_security_rule.host_edge_ingress["http"]', 80, "ocid1.networksecuritygroup.oc1.edge", "NETWORK_SECURITY_GROUP"),
            rule('module.v1_live.oci_core_network_security_group_security_rule.host_edge_ingress["https"]', 443, "ocid1.networksecuritygroup.oc1.edge", "NETWORK_SECURITY_GROUP"),
        ]
        validate_v1_plan.validate_ingress(resources)
        resources[2]["values"]["source"] = "0.0.0.0/0"
        with self.assertRaises(AssertionError):
            validate_v1_plan.validate_ingress(resources)


    def test_instance_metadata_is_bounded_and_hardened(self) -> None:
        import base64
        import gzip

        cloud_init = b"liqi-bootstrap-host liqi-install-host-bundle liqi-configure-bastion-ssh"
        encoded = base64.b64encode(gzip.compress(cloud_init, mtime=0)).decode("ascii")
        resources = [{
            "type": "oci_core_instance",
            "values": {
                "shape": "VM.Standard.A1.Flex",
                "shape_config": [{"ocpus": 4, "memory_in_gbs": 24}],
                "source_details": [{"boot_volume_size_in_gbs": 50}],
                "metadata": {"user_data": encoded},
                "create_vnic_details": [{"assign_public_ip": "false"}],
                "agent_config": [{"plugins_config": [{"name": "Compute Instance Run Command", "desired_state": "ENABLED"}]}],
            },
        }]
        rendered = validate_v1_plan.validate_instance(resources, "a1-target")
        self.assertIn("liqi-bootstrap-host", rendered)

    def test_management_rejects_superseded_external_wireguard_egress(self) -> None:
        dns = [{
            "type": "oci_core_network_security_group_security_rule",
            "values": {
                "direction": "EGRESS", "protocol": "17", "destination": "169.254.169.254/32",
                "udp_options": [{"destination_port_range": [{"min": 53, "max": 53}]}],
            },
        }]
        validate_v1_plan.validate_management(dns)
        dns.append({
            "type": "oci_core_network_security_group_security_rule",
            "values": {
                "direction": "EGRESS", "protocol": "17", "destination": "198.51.100.10/32",
                "udp_options": [{"destination_port_range": [{"min": 51820, "max": 51820}]}],
            },
        })
        with self.assertRaises(AssertionError):
            validate_v1_plan.validate_management(dns)

    def test_nlb_is_fail_closed_and_websocket_timeout_is_long_lived(self) -> None:
        resources = []
        for key, port in (("http", 80), ("https", 443)):
            resources.extend([
                {"address": f'module.v1_live.oci_network_load_balancer_backend_set.edge["{key}"]', "type": "oci_network_load_balancer_backend_set", "values": {"policy": "FIVE_TUPLE", "is_fail_open": False, "is_preserve_source": False, "health_checker": [{"protocol": "TCP", "port": port}]}},
                {"address": f'module.v1_live.oci_network_load_balancer_backend.host["{key}"]', "type": "oci_network_load_balancer_backend", "values": {"port": port, "is_backup": False, "is_drain": False, "is_offline": False}},
                {"address": f'module.v1_live.oci_network_load_balancer_listener.edge["{key}"]', "type": "oci_network_load_balancer_listener", "values": {"port": port, "protocol": "TCP", "tcp_idle_timeout": 3600}},
            ])
        validate_v1_plan.validate_nlb(resources)
        resources[-1]["values"]["tcp_idle_timeout"] = 60
        with self.assertRaises(AssertionError):
            validate_v1_plan.validate_nlb(resources)




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
