from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "release" / "activate_release.py"
BASE_SPEC = ROOT / "tests" / "contract" / "fixtures" / "operations" / "deployment-spec.valid.json"
HOST = ROOT / "tests" / "integration" / "fixtures" / "host-readiness.valid.json"
DATABASE = ROOT / "tests" / "integration" / "fixtures" / "database-readiness.valid.json"
HEALTH = ROOT / "tests" / "integration" / "fixtures" / "health-gate-target.promotion.valid.json"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ActivationControlTests(unittest.TestCase):
    def prepare(self, root: Path) -> tuple[Path, Path]:
        staged = root / "staged"
        staged.mkdir()
        spec = json.loads(BASE_SPEC.read_text(encoding="utf-8"))
        for index, artifact in enumerate(spec["artifacts"]):
            payload = f"fixture-{artifact['name']}-{index}\n".encode()
            path = staged / artifact["name"]
            path.write_bytes(payload)
            artifact["size_bytes"] = len(payload)
            artifact["sha256"] = hashlib.sha256(payload).hexdigest()
        spec["health_gate"]["target_digest"] = digest(HEALTH)
        spec_path = root / "deployment-spec.json"
        spec_path.write_text(json.dumps(spec, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return spec_path, staged

    def run_activation(self, root: Path, spec: Path, staged: Path, database: Path = DATABASE, execute: bool = False) -> subprocess.CompletedProcess[str]:
        output = root / "activation-result.json"
        command = [
            sys.executable, str(SCRIPT),
            "--spec", str(spec),
            "--expected-spec-sha256", digest(spec),
            "--host-readiness", str(HOST),
            "--database-readiness", str(database),
            "--staged-root", str(staged),
            "--health-target", str(HEALTH),
            "--state-dir", str(root / "state"),
            "--output", str(output),
        ]
        if execute:
            command.append("--execute")
        return subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False)

    def test_dry_run_validates_without_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            spec, staged = self.prepare(root)
            result = self.run_activation(root, spec, staged)
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads((root / "activation-result.json").read_text(encoding="utf-8"))
            self.assertEqual(payload["status"], "planned")
            self.assertFalse(payload["mutation"]["performed"])
            self.assertEqual(payload["preflight"]["systemd_units"], "not-checked")

    def test_database_not_ready_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            spec, staged = self.prepare(root)
            database = json.loads(DATABASE.read_text(encoding="utf-8"))
            database.update({"ready": False, "reason": "migration-pending", "currentVersion": -1})
            db_path = root / "database-not-ready.json"
            db_path.write_text(json.dumps(database), encoding="utf-8")
            result = self.run_activation(root, spec, staged, db_path)
            self.assertEqual(result.returncode, 1)
            payload = json.loads((root / "activation-result.json").read_text(encoding="utf-8"))
            self.assertEqual(payload["status"], "failed")
            self.assertIn("database is not ready", payload["incident_reason"])

    def test_execute_requires_explicit_approval_before_host_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            spec, staged = self.prepare(root)
            result = self.run_activation(root, spec, staged, execute=True)
            self.assertEqual(result.returncode, 2)
            payload = json.loads((root / "activation-result.json").read_text(encoding="utf-8"))
            self.assertFalse(payload["mutation"]["performed"])
            self.assertIn("approved reference", payload["incident_reason"])


if __name__ == "__main__":
    unittest.main()
