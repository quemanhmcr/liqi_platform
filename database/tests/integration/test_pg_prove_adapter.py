from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
ADAPTER = ROOT / "database" / "tests" / "integration" / "bin" / "pg_prove"


@unittest.skipUnless(os.name == "posix", "the CI adapter is Linux-only")
class PgProveAdapterTests(unittest.TestCase):
    def environment(self, directory: Path) -> dict[str, str]:
        fake_docker = directory / "docker"
        fake_docker.write_text(
            "#!/usr/bin/env bash\nprintf '%s\\n' \"$@\"\n",
            encoding="utf-8",
            newline="\n",
        )
        fake_docker.chmod(0o755)
        environment = os.environ.copy()
        environment.update(
            {
                "GITHUB_WORKSPACE": str(ROOT),
                "LIQI_PGTAP_CONTAINER_ID": "a" * 12,
                "PATH": f"{directory}{os.pathsep}{environment['PATH']}",
            }
        )
        return environment

    def test_extension_value_is_preserved_and_suite_path_is_mapped(self) -> None:
        sql_path = ROOT / "database" / "tests" / "pgtap" / "000000000001_platform_metadata.sql"
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(
                [str(ADAPTER), "--ext", ".sql", str(sql_path)],
                env=self.environment(Path(directory)),
                check=False,
                capture_output=True,
                text=True,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        arguments = result.stdout.splitlines()
        self.assertIn(".sql", arguments)
        self.assertIn("/tmp/liqi-pgtap/000000000001_platform_metadata.sql", arguments)
        self.assertNotIn(str(sql_path), arguments)

    def test_absolute_sql_path_outside_suite_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(
                [str(ADAPTER), "/tmp/outside.sql"],
                env=self.environment(Path(directory)),
                check=False,
                capture_output=True,
                text=True,
            )
        self.assertEqual(result.returncode, 64)
        self.assertIn("outside the checked-out suite", result.stderr)


if __name__ == "__main__":
    unittest.main()
