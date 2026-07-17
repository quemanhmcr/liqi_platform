from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PYTHON = sys.executable
CAPACITY_FIXTURES = ROOT / "tests" / "contract" / "fixtures" / "capacity"
RELEASE_INPUT = ROOT / "tests" / "contract" / "fixtures" / "release-input" / "release-input.json"
FIXED_SHA = "2d72ce4176993324d36d6f4ea115ff2b43fd5355"
FIXED_EPOCH = "1784246400"


def run(*args: str, expected: int = 0) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(args, cwd=ROOT, text=True, capture_output=True, check=False)
    if result.returncode != expected:
        raise AssertionError(
            f"command returned {result.returncode}, expected {expected}: {args}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


class OperationsToolTests(unittest.TestCase):
    def test_contract_fixtures_validate(self) -> None:
        run(PYTHON, "scripts/operations/validate_contracts.py", "--quiet")

    def test_capacity_aggregate_preserves_headroom(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "capacity.json"
            run(
                PYTHON,
                "scripts/operations/check_capacity.py",
                str(CAPACITY_FIXTURES / "infrastructure.valid.json"),
                str(CAPACITY_FIXTURES / "database.valid.json"),
                str(CAPACITY_FIXTURES / "runtime.valid.json"),
                str(CAPACITY_FIXTURES / "operations.valid.json"),
                "--output",
                str(output),
            )
            result = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual("passed", result["status"])
            envelope = result["envelope"]
            for resource in ("ocpu", "memory_mib", "disk_gib"):
                self.assertLessEqual(
                    result["steady_state_totals"][resource],
                    envelope["steady_state_limit"][resource],
                )
                self.assertLessEqual(
                    result["hard_limit_totals"][resource],
                    envelope["provider_hard_limit"][resource],
                )
            self.assertLessEqual(
                result["hard_limit_totals"]["postgres_connections"],
                envelope["provider_hard_limit"]["postgres_connections"],
            )
            self.assertEqual(result["totals"], result["hard_limit_totals"])
            connections = result["postgres_connection_accounting"]
            self.assertLessEqual(connections["pooled_runtime_demand"], connections["pooled_server_capacity"])

    def test_capacity_fails_when_steady_cpu_consumes_host_reserve(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = json.loads((CAPACITY_FIXTURES / "runtime.valid.json").read_text(encoding="utf-8"))
            for component in runtime["components"]:
                component["steady_state"]["ocpu"] = 0.8
                component["hard_limit"]["ocpu"] = 0.8
            path = Path(directory) / "runtime.json"
            path.write_text(json.dumps(runtime), encoding="utf-8")
            result = run(
                PYTHON, "scripts/operations/check_capacity.py",
                str(CAPACITY_FIXTURES / "infrastructure.valid.json"),
                str(CAPACITY_FIXTURES / "database.valid.json"), str(path),
                str(CAPACITY_FIXTURES / "operations.valid.json"), expected=1,
            )
            payload = json.loads(result.stdout)
            self.assertGreater(payload["hard_limit_totals"]["ocpu"], payload["envelope"]["steady_state_limit"]["ocpu"])
            self.assertTrue(any("steady-state capacity exceeded for ocpu" in item for item in payload["failures"]))

    def test_capacity_fails_when_hard_cpu_exceeds_physical_host(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = json.loads((CAPACITY_FIXTURES / "runtime.valid.json").read_text(encoding="utf-8"))
            runtime["components"][0]["hard_limit"]["ocpu"] = 1.2
            path = Path(directory) / "runtime.json"
            path.write_text(json.dumps(runtime), encoding="utf-8")
            result = run(
                PYTHON, "scripts/operations/check_capacity.py",
                str(CAPACITY_FIXTURES / "infrastructure.valid.json"),
                str(CAPACITY_FIXTURES / "database.valid.json"), str(path),
                str(CAPACITY_FIXTURES / "operations.valid.json"), expected=1,
            )
            payload = json.loads(result.stdout)
            self.assertIn("hard ceiling exceeded for ocpu", payload["failures"][0])
            schema = json.loads((ROOT / "contracts/operations/capacity-result-v0.schema.json").read_text(encoding="utf-8"))
            from jsonschema import Draft202012Validator
            self.assertEqual(list(Draft202012Validator(schema).iter_errors(payload)), [])

    def test_capacity_fails_when_pooled_demand_exceeds_pgbouncer(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = json.loads((CAPACITY_FIXTURES / "runtime.valid.json").read_text(encoding="utf-8"))
            runtime["components"][0]["postgres_connections"] = 40
            path = Path(directory) / "runtime.json"
            path.write_text(json.dumps(runtime), encoding="utf-8")
            result = run(
                PYTHON, "scripts/operations/check_capacity.py",
                str(CAPACITY_FIXTURES / "infrastructure.valid.json"),
                str(CAPACITY_FIXTURES / "database.valid.json"), str(path),
                str(CAPACITY_FIXTURES / "operations.valid.json"), expected=1,
            )
            self.assertTrue(any("pooled runtime demand exceeds" in item for item in json.loads(result.stdout)["failures"]))

    def test_capacity_fails_when_provider_budget_is_missing(self) -> None:
        result = run(
            PYTHON,
            "scripts/operations/check_capacity.py",
            str(CAPACITY_FIXTURES / "infrastructure.valid.json"),
            str(CAPACITY_FIXTURES / "database.valid.json"),
            str(CAPACITY_FIXTURES / "runtime.valid.json"),
            expected=1,
        )
        report = json.loads(result.stdout)
        self.assertEqual("failed", report["status"])
        self.assertIn("missing provider budgets: operations", report["failures"])

    def test_release_manifest_is_byte_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            first = Path(directory) / "first.json"
            second = Path(directory) / "second.json"
            common = (
                PYTHON,
                "scripts/release/generate_release_manifest.py",
                "--input",
                str(RELEASE_INPUT),
                "--git-sha",
                FIXED_SHA,
                "--source-date-epoch",
                FIXED_EPOCH,
            )
            run(*common, "--output", str(first))
            run(*common, "--output", str(second))
            first_bytes = first.read_bytes()
            self.assertEqual(first_bytes, second.read_bytes())
            manifest = json.loads(first_bytes)
            self.assertEqual(64, len(hashlib.sha256(first_bytes).hexdigest()))
            self.assertEqual(
                hashlib.sha256((ROOT / "tests/contract/fixtures/release-input/release.spdx.json").read_bytes()).hexdigest(),
                manifest["supply_chain"]["sbom"]["sha256"],
            )
            self.assertEqual(
                hashlib.sha256((ROOT / "tests/contract/fixtures/release-input/release.intoto.jsonl").read_bytes()).hexdigest(),
                manifest["supply_chain"]["provenance"]["sha256"],
            )


if __name__ == "__main__":
    unittest.main()
