from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from jsonschema import Draft202012Validator, FormatChecker

from infrastructure.validation import pre_apply_readiness as module
from infrastructure.validation import validate_pre_apply_readiness as binding_validator

ROOT = Path(__file__).resolve().parents[2]
SHA = "1" * 40


class PreApplyReadinessTests(unittest.TestCase):
    def write_json(self, root: Path, name: str, document: dict) -> Path:
        path = root / name
        path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8", newline="\n")
        return path

    def test_contract_example_is_valid_and_has_exact_checks(self) -> None:
        schema = json.loads((ROOT / "contracts/infrastructure/pre-apply-readiness-v1.schema.json").read_text(encoding="utf-8"))
        example = json.loads((ROOT / "contracts/infrastructure/pre-apply-readiness-v1.example.json").read_text(encoding="utf-8"))
        errors = list(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(example))
        self.assertEqual([], errors)
        self.assertEqual(list(module.CHECK_ORDER), [item["name"] for item in example["checks"]])

    def test_adoption_schema_accepts_official_nsg_rule_composite_import_id(self) -> None:
        schema = json.loads((ROOT / "contracts/infrastructure/adoption-manifest-v1.schema.json").read_text(encoding="utf-8"))
        document = json.loads((ROOT / "contracts/infrastructure/adoption-manifest-v1.example.json").read_text(encoding="utf-8"))
        document["imports"][0]["resource_type"] = "oci_core_network_security_group_security_rule"
        document["imports"][0]["address"] = 'module.v1_live.oci_core_network_security_group_security_rule.bastion_ssh_ingress["10.42.20.100/32"]'
        document["imports"][0]["id"] = "networkSecurityGroups/ocid1.networksecuritygroup.oc1.example/securityRules/RULE123"
        errors = list(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(document))
        self.assertEqual([], errors)

    def test_incomplete_oci_handoff_is_blocked_without_ocid_leakage(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            document = json.loads((ROOT / "contracts/infrastructure/adoption-manifest-v1.example.json").read_text(encoding="utf-8"))
            document.update({
                "git_sha": SHA,
                "status": "blocked",
                "imports": [],
                "missing_addresses": ["module.v1_live.oci_kms_vault.main"],
                "blockers": ["adopted internet gateway is disabled"],
                "oci_mutation_performed": False,
            })
            path = self.write_json(root, "adoption.json", document)
            result, _ = module.adoption_check(path, SHA)
            self.assertEqual("blocked", result["status"])
            self.assertIn("internet gateway is disabled", result["detail"])
            self.assertNotIn("ocid1.", result["detail"])

    def test_passed_adoption_may_partition_imports_and_plan_creates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            document = json.loads((ROOT / "contracts/infrastructure/adoption-manifest-v1.example.json").read_text(encoding="utf-8"))
            all_addresses = sorted(module.EXPECTED_ADDRESSES)
            document.update({
                "git_sha": SHA,
                "status": "passed",
                "imports": [
                    {"address": address, "id": f"ocid1.example.{index}", "resource_type": "oci_example", "display_name": f"resource-{index}"}
                    for index, address in enumerate(all_addresses[:7])
                ],
                "missing_addresses": all_addresses[7:],
                "blockers": [],
                "oci_mutation_performed": False,
            })
            path = self.write_json(root, "adoption.json", document)
            result, _ = module.adoption_check(path, SHA)
            self.assertEqual("passed", result["status"])
            self.assertIn(f"may create {len(all_addresses) - 7}", result["detail"])

    def test_protected_tfvars_pass_and_placeholders_block(self) -> None:
        now = datetime(2026, 7, 19, tzinfo=timezone.utc)
        expiry = (now + timedelta(days=30)).isoformat().replace("+00:00", "Z")
        valid = f'''tenancy_ocid = "ocid1.tenancy.oc1..live"
region = "ap-singapore-2"
availability_domain = "nwuj:AP-SINGAPORE-2-AD-1"
oracle_linux_image_ocid = "ocid1.image.oc1.ap-singapore-2.live"
source_git_sha = "{SHA}"
capacity_profile = "e5-temporary"
temporary_e5_expires_at = "{expiry}"
acknowledge_capacity_availability_and_cost = true
operation_mode = "plan"
public_backend_enabled = false
acknowledge_public_cutover = false
fallback_desired_state = "STOPPED"
retained_fallback_instance_ocid = "ocid1.instance.oc1.ap-singapore-2.retained"
retained_fallback_private_ipv4 = "10.42.10.61"
legacy_host_subnet_cidr = "10.42.10.0/24"
legacy_host_subnet_label = "live"
host_subnet_cidr = "10.42.20.0/24"
host_subnet_label = "livepriv"
public_edge_subnet_cidr = "10.42.30.0/24"
public_edge_subnet_label = "edge"
vcn_cidr = "10.42.0.0/16"
vcn_dns_label = "liqilive"
compartment = "liqi-live"
vcn = "liqi-live-vcn"
internet_gateway = "liqi-live-igw"
nat_gateway = "liqi-live-nat-gw"
service_gateway = "liqi-live-service-gw"
legacy_route_table = "liqi-live-public-rt"
route_table = "liqi-live-private-rt"
public_edge_route_table = "liqi-live-nlb-public-rt"
security_list = "liqi-live-egress-only-sl"
legacy_subnet = "liqi-live-public-subnet"
subnet = "liqi-live-private-subnet"
public_edge_subnet = "liqi-live-nlb-public-subnet"
nsg = "liqi-live-workload-nsg"
nlb_nsg = "liqi-live-edge-nlb-nsg"
network_load_balancer = "liqi-live-edge-nlb"
legacy_instance = "liqi-live-primary"
legacy_vnic = "liqi-live-primary-v2"
legacy_fallback_instance = "liqi-live-primary-fallback-stopped"
instance = "liqi-live-v1-private-primary"
vnic = "liqi-live-v1-private-primary-vnic"
fallback_instance = "liqi-live-v1-private-fallback"
fallback_vnic = "liqi-live-v1-private-fallback-vnic"
data_volume = "liqi-live-data"
data_attachment = "liqi-live-data-attachment"
vault = "liqi-live-vault"
key = "liqi-live-software-key"
reserved_public_ip = "liqi-live-edge-ip"
dynamic_group = "liqi_v1_live_host"
policy = "liqi_v1_live_host_policy"
bastion_ssh_source_cidrs = ["10.42.20.100/32", "10.42.20.109/32"]
management_plane_evidence_id = "management-evidence-v1"
state_backend_lock_evidence_id = "state-evidence-v1"
host_bundle_signing_key_id = "production-host-key-v1"
host_bundle_signing_public_key_pem = <<-EOT
-----BEGIN PUBLIC KEY-----
MCowBQYDK2VwAyEAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=
-----END PUBLIC KEY-----
EOT
acknowledge_host_bundle_signing_key = true
'''
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "live.tfvars"
            path.write_text(valid, encoding="utf-8", newline="\n")
            if os.name == "posix":
                os.chmod(path, 0o600)
            result, digest = module.tfvars_check(path, SHA, now)
            self.assertEqual("passed", result["status"])
            self.assertRegex(digest or "", r"^[0-9a-f]{64}$")
            path.write_text(valid.replace('nwuj:AP-SINGAPORE-2-AD-1', 'nwuj:AP-SINGAPORE-2-AD-2'), encoding="utf-8", newline="\n")
            result, _ = module.tfvars_check(path, SHA, now)
            self.assertEqual("blocked", result["status"])
            self.assertIn("availability_domain", result["detail"])

            online_without_recovery = valid.replace(
                "public_backend_enabled = false", "public_backend_enabled = true"
            )
            path.write_text(online_without_recovery, encoding="utf-8", newline="\n")
            result, _ = module.tfvars_check(path, SHA, now)
            self.assertEqual("blocked", result["status"])
            self.assertIn("cutover acknowledgement", result["detail"])

            online_with_running_fallback = online_without_recovery.replace(
                "acknowledge_public_cutover = false", "acknowledge_public_cutover = true"
            ).replace('fallback_desired_state = "STOPPED"', 'fallback_desired_state = "RUNNING"')
            path.write_text(online_with_running_fallback, encoding="utf-8", newline="\n")
            result, _ = module.tfvars_check(path, SHA, now)
            self.assertEqual("blocked", result["status"])
            self.assertIn("fallback_desired_state=STOPPED", result["detail"])

    def test_passed_readiness_binds_exact_plan_inputs_and_rejects_tamper(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = root / "state.json"
            state.write_bytes(b"state-evidence")
            var_file = root / "live.tfvars"
            var_file.write_bytes(b"protected-tfvars")
            manifest_sha = "c" * 64
            adoption = self.write_json(root, "adoption-result.json", {"manifest_sha256": manifest_sha})
            document = json.loads((ROOT / "contracts/infrastructure/pre-apply-readiness-v1.example.json").read_text(encoding="utf-8"))
            document["git_sha"] = SHA
            document["inputs"].update({
                "state_backend_evidence_sha256": binding_validator.digest(state),
                "adoption_result_sha256": binding_validator.digest(adoption),
                "var_file_sha256": binding_validator.digest(var_file),
                "adoption_manifest_sha256": manifest_sha,
            })
            binding_validator.validate_result(document, SHA, state, adoption, var_file)
            var_file.write_bytes(b"tampered-tfvars")
            with self.assertRaisesRegex(ValueError, "var_file_sha256"):
                binding_validator.validate_result(document, SHA, state, adoption, var_file)

    def test_blocked_readiness_cannot_bind_a_plan(self) -> None:
        document = json.loads((ROOT / "contracts/infrastructure/pre-apply-readiness-v1.example.json").read_text(encoding="utf-8"))
        document["git_sha"] = SHA
        document["status"] = "blocked"
        document["checks"][0]["status"] = "blocked"
        document["blockers"] = ["handoff incomplete"]
        with self.assertRaisesRegex(ValueError, "not passed"):
            binding_validator.validate_document(document, SHA)

    def test_plan_and_apply_result_contracts_fail_closed_without_breaking_a1(self) -> None:
        plan_schema = json.loads((ROOT / "contracts/infrastructure/plan-result-v1.schema.json").read_text(encoding="utf-8"))
        apply_schema = json.loads((ROOT / "contracts/infrastructure/apply-result-v1.schema.json").read_text(encoding="utf-8"))
        plan = json.loads((ROOT / "contracts/infrastructure/plan-result-v1.example.json").read_text(encoding="utf-8"))
        apply = json.loads((ROOT / "contracts/infrastructure/apply-result-v1.example.json").read_text(encoding="utf-8"))
        plan_validator = Draft202012Validator(plan_schema, format_checker=FormatChecker())
        apply_validator = Draft202012Validator(apply_schema, format_checker=FormatChecker())
        self.assertEqual([], list(plan_validator.iter_errors(plan)))
        self.assertEqual([], list(apply_validator.iter_errors(apply)))

        plan["inputs"]["pre_apply_readiness_sha256"] = None
        self.assertNotEqual([], list(plan_validator.iter_errors(plan)))

        a1 = json.loads((ROOT / "contracts/infrastructure/plan-result-v1.example.json").read_text(encoding="utf-8"))
        a1.update({"mode": "plan", "capacity_profile": "a1-target", "plan_mode": "initial-create", "approval_reference": None})
        for name in (
            "adoption_result_sha256", "pre_apply_readiness_sha256", "adoption_manifest_sha256",
            "linux_release_build_result_sha256", "recovery_target_sha256",
        ):
            a1["inputs"][name] = None
        self.assertEqual([], list(plan_validator.iter_errors(a1)))

        apply["oci_mutation_performed"] = False
        self.assertNotEqual([], list(apply_validator.iter_errors(apply)))

    def test_first_release_recovery_binds_exact_publication_and_sha(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            build = json.loads((ROOT / "contracts/runtime/linux-release-build-result-v1.example.json").read_text(encoding="utf-8"))
            manifest = json.loads((ROOT / "contracts/deployment/mix-release-v1.e5-temporary.example.json").read_text(encoding="utf-8"))
            recovery = json.loads((ROOT / "contracts/infrastructure/first-release-recovery-v1.example.json").read_text(encoding="utf-8"))
            build["git_sha"] = SHA
            manifest["git_sha"] = SHA
            manifest["rollback_target_release_id"] = None
            manifest["database_compatibility"] = {"minimum_migration": 8, "maximum_migration": 8, "rollback_safe_through": 8}
            recovery["git_sha"] = SHA
            manifest_path = self.write_json(root, build["manifest"]["filename"], manifest)
            build["manifest"]["sha256"] = module.sha256(manifest_path)
            build_path = self.write_json(root, "build.json", build)
            recovery_path = self.write_json(root, "recovery.json", recovery)
            result = module.recovery_check(recovery_path, build_path, build, SHA)
            self.assertEqual("passed", result["status"])

            cutover_vars = root / "cutover.tfvars"
            cutover_vars.write_text("public_backend_enabled = true\n", encoding="utf-8", newline="\n")
            result = module.recovery_check(recovery_path, build_path, build, SHA, cutover_vars)
            self.assertEqual("passed", result["status"])

            recovery["primary"]["subnet_public_ip_prohibited"] = False
            self.write_json(root, "recovery.json", recovery)
            result = module.recovery_check(recovery_path, build_path, build, SHA, cutover_vars)
            self.assertEqual("failed", result["status"])

            recovery["primary"]["subnet_public_ip_prohibited"] = True
            recovery["git_sha"] = "2" * 40
            self.write_json(root, "recovery.json", recovery)
            result = module.recovery_check(recovery_path, build_path, build, SHA)
            self.assertEqual("failed", result["status"])

    def test_environment_check_never_emits_connection_string(self) -> None:
        secret = "sentinel-sensitive-backend-value?sslmode=verify-full"
        values = {
            "TF_ENCRYPTION": "encrypted-config",
            "PG_CONN_STR": secret,
            "PG_SCHEMA_NAME": "opentofu_v1_live",
            "PG_SKIP_SCHEMA_CREATION": "true",
            "PG_SKIP_TABLE_CREATION": "true",
            "PG_SKIP_INDEX_CREATION": "true",
        }
        with patch.dict(os.environ, values, clear=True):
            result = module.environment_check()
        self.assertEqual("passed", result["status"])
        self.assertNotIn(secret, result["detail"])
        with patch.dict(os.environ, {"PG_CONN_STR": secret}, clear=True):
            blocked = module.environment_check()
        self.assertEqual("blocked", blocked["status"])
        self.assertNotIn(secret, blocked["detail"])

    def test_executed_adoption_accepts_idempotent_noop_and_requires_exact_addresses(self) -> None:
        base = {
            "schema_version": "liqi.infrastructure.adoption-result/v1",
            "git_sha": SHA,
            "capacity_profile": "e5-temporary",
            "operation": "execute",
            "approval_reference": "approval-123",
            "manifest_sha256": "a" * 64,
            "var_file_sha256": "b" * 64,
            "status": "passed",
            "imported_addresses": sorted(module.EXPECTED_ADDRESSES),
            "already_present_addresses": [],
            "blockers": [],
            "state_mutation_performed": False,
            "oci_mutation_performed": False,
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = self.write_json(root, "result.json", base)
            expected = set(base["imported_addresses"])
            result, _ = module.adoption_result_check(path, SHA, "a" * 64, "b" * 64, expected)
            self.assertEqual("passed", result["status"])
            base["already_present_addresses"] = base["imported_addresses"]
            base["imported_addresses"] = []
            path = self.write_json(root, "result.json", base)
            result, _ = module.adoption_result_check(path, SHA, "a" * 64, "b" * 64, expected)
            self.assertEqual("passed", result["status"])


if __name__ == "__main__":
    unittest.main()
