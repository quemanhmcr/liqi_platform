from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ASSEMBLER = ROOT / "scripts" / "operations" / "assemble_integration_result.py"
DEPLOYMENT = ROOT / "scripts" / "release" / "generate_deployment_spec.py"
FIX = ROOT / "tests" / "integration" / "fixtures"
OPS_FIX = ROOT / "tests" / "contract" / "fixtures" / "operations"


class PromotionCompositionTests(unittest.TestCase):
    def assemble(self, provider: Path, output: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run([
            sys.executable, str(ASSEMBLER),
            "--provider-result", str(provider),
            "--capacity-result", str(OPS_FIX / "capacity-result.valid.json"),
            "--recovery-result", str(OPS_FIX / "recovery-freshness-result.valid.json"),
            "--platform-probe", str(FIX / "platform-probe-result.valid.json"),
            "--output", str(output),
        ], cwd=ROOT, text=True, capture_output=True, check=False)

    def test_passed_provider_evidence_composes_and_deployment_is_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            integration = base / "integration.json"
            completed = self.assemble(FIX / "provider-result.passed.json", integration)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            result = json.loads(integration.read_text(encoding="utf-8"))
            self.assertEqual(result["overall_status"], "passed")
            self.assertEqual(result["capacity"]["declared_ocpu"], 2.9)
            outputs = [base / "deployment-a.json", base / "deployment-b.json"]
            for output in outputs:
                generated = subprocess.run([
                    sys.executable, str(DEPLOYMENT),
                    "--manifest", str(OPS_FIX / "release-manifest.valid.json"),
                    "--integration-result", str(integration),
                    "--host-output", str(FIX / "oci-host-output.valid.json"),
                    "--health-target", str(FIX / "health-gate-target.promotion.valid.json"),
                    "--output", str(output),
                ], cwd=ROOT, text=True, capture_output=True, check=False)
                self.assertEqual(generated.returncode, 0, generated.stderr)
            self.assertEqual(outputs[0].read_bytes(), outputs[1].read_bytes())
            spec = json.loads(outputs[0].read_text(encoding="utf-8"))
            self.assertFalse(spec["claims"]["high_availability"])
            self.assertFalse(spec["mutation_policy"]["automatic_activation"])

    def test_mock_provider_result_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "result.json"
            completed = self.assemble(OPS_FIX / "integration-result.valid.json", output)
            self.assertEqual(completed.returncode, 1)
            result = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(result["overall_status"], "failed")
            self.assertTrue(any("mock evidence" in item["message"] for item in result["violations"]))

    def test_unapproved_non_free_cost_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            manifest = json.loads((OPS_FIX / "release-manifest.valid.json").read_text(encoding="utf-8"))
            manifest["infrastructure"]["cost_classification"] = "free-trial-only"
            manifest_path = base / "manifest.json"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            host = json.loads((FIX / "oci-host-output.valid.json").read_text(encoding="utf-8"))
            host["capacity_profile"]["cost_classification"] = "free-trial-only"
            host_path = base / "host.json"
            host_path.write_text(json.dumps(host), encoding="utf-8")
            completed = subprocess.run([
                sys.executable, str(DEPLOYMENT),
                "--manifest", str(manifest_path),
                "--integration-result", str(FIX / "integration-result.promotion.valid.json"),
                "--host-output", str(host_path),
                "--health-target", str(FIX / "health-gate-target.promotion.valid.json"),
                "--output", str(base / "deployment.json"),
            ], cwd=ROOT, text=True, capture_output=True, check=False)
            self.assertEqual(completed.returncode, 1)
            self.assertIn("explicit approval reference", completed.stderr)


if __name__ == "__main__":
    unittest.main()
