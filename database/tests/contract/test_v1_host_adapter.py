from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
ADAPTER = ROOT / "database" / "tools" / "validate_v1_host_adapter.py"
EXAMPLE = ROOT / "contracts" / "infrastructure" / "oci-live-v1.example.json"


class V1HostAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.document = json.loads(EXAMPLE.read_text(encoding="utf-8"))

    def validate(self, document: dict[str, object]) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "oci-live-v1.json"
            path.write_text(json.dumps(document), encoding="utf-8", newline="\n")
            return subprocess.run(
                [sys.executable, str(ADAPTER), str(path)],
                check=False,
                capture_output=True,
                text=True,
            )

    def test_current_private_bastion_and_run_command_contract_passes(self) -> None:
        result = self.validate(self.document)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)["passed"])

    def test_legacy_wireguard_management_contract_fails(self) -> None:
        document = copy.deepcopy(self.document)
        network = document["network"]
        network.pop("management_access")
        network["management_tunnel"] = {
            "mode": "outbound-only",
            "protocol": "wireguard-udp",
            "public_ingress_required": False,
        }
        result = self.validate(document)
        self.assertEqual(result.returncode, 1)
        self.assertIn("private OCI Bastion plus Run Command", result.stderr)

    def test_public_host_or_widened_ssh_source_fails(self) -> None:
        for mutation in ("public-ip", "world-ssh"):
            with self.subTest(mutation=mutation):
                document = copy.deepcopy(self.document)
                if mutation == "public-ip":
                    document["network"]["host_public_ip_enabled"] = True
                    document["host"]["public_ip_mode"] = "ephemeral"
                    document["host"]["public_ipv4"] = "203.0.113.9"
                else:
                    document["network"]["ssh_source_cidrs"] = ["0.0.0.0/0"]
                result = self.validate(document)
                self.assertEqual(result.returncode, 1)


if __name__ == "__main__":
    unittest.main()
