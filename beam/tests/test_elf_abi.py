from __future__ import annotations

import unittest

from beam.elf_abi import EL9_MAXIMUMS, parse_requirements, policy_violations, version_exceeds


class ElfAbiPolicyTests(unittest.TestCase):
    def test_production_el9_symbol_ceilings_are_exact(self) -> None:
        self.assertEqual(
            {
                "GLIBC": (2, 35),
                "GLIBCXX": (3, 4, 29),
                "CXXABI": (1, 3, 13),
                "GCC": (7, 0, 0),
            },
            EL9_MAXIMUMS,
        )

    def test_parser_keeps_highest_requirement_and_ignores_definitions(self) -> None:
        requirements = parse_requirements(
            "Version definition section '.gnu.version_d' contains 1 entry:\n"
            "  Name: GLIBC_9.99\n"
            "Version needs section '.gnu.version_r' contains 2 entries:\n"
            "  Name: GLIBC_2.17\n"
            "  Name: GLIBC_2.34\n"
            "  Name: GLIBCXX_3.4.29\n"
            "  Name: CXXABI_1.3.13\n"
            "  Name: GCC_7.0.0\n"
        )
        self.assertEqual((2, 34), requirements["GLIBC"])
        self.assertEqual((3, 4, 29), requirements["GLIBCXX"])
        self.assertNotEqual((9, 99), requirements["GLIBC"])

    def test_version_comparison_treats_trailing_zero_as_equal(self) -> None:
        self.assertFalse(version_exceeds((2, 35, 0), (2, 35)))
        self.assertFalse(version_exceeds((7, 0), (7, 0, 0)))
        self.assertTrue(version_exceeds((2, 35, 1), (2, 35)))

    def test_policy_rejects_observed_failed_release_requirements(self) -> None:
        violations = policy_violations(
            {
                "erts/bin/beam.smp": {
                    "GLIBC": (2, 38),
                    "GLIBCXX": (3, 4, 30),
                    "GCC": (12, 0, 0),
                }
            }
        )
        self.assertEqual(3, len(violations))
        self.assertTrue(any("GLIBC_2.38" in item for item in violations))
        self.assertTrue(any("GLIBCXX_3.4.30" in item for item in violations))
        self.assertTrue(any("GCC_12.0.0" in item for item in violations))

    def test_policy_accepts_equal_or_older_requirements(self) -> None:
        self.assertEqual(
            [],
            policy_violations(
                {
                    "erts/bin/beam.smp": {
                        "GLIBC": (2, 35),
                        "GLIBCXX": (3, 4, 29),
                        "CXXABI": (1, 3, 13),
                        "GCC": (7, 0, 0),
                    }
                }
            ),
        )


if __name__ == "__main__":
    unittest.main()
