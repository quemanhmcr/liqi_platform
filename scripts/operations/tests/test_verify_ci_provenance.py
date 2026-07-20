from __future__ import annotations

import unittest

from scripts.operations.verify_ci_provenance import evaluate


HEAD = "d" * 40
MERGE = "a" * 40


class VerifyCiProvenanceTests(unittest.TestCase):
    def test_push_requires_after_checkout_and_release_suffix_to_match(self) -> None:
        failures, details = evaluate(
            actual_sha=HEAD,
            expected_sha=HEAD,
            github_sha=HEAD,
            event_name="push",
            payload={"after": HEAD},
            release_id=f"liqi-v1-ci-{HEAD}",
        )
        self.assertEqual(failures, [])
        self.assertEqual(details["github_sha_kind"], "source")

    def test_pull_request_records_but_does_not_promote_synthetic_merge_sha(self) -> None:
        failures, details = evaluate(
            actual_sha=HEAD,
            expected_sha=HEAD,
            github_sha=MERGE,
            event_name="pull_request",
            payload={"pull_request": {"head": {"sha": HEAD}, "merge_commit_sha": MERGE}},
            release_id=f"liqi-v1-ci-{HEAD}",
        )
        self.assertEqual(failures, [])
        self.assertEqual(details["github_sha_kind"], "pull-request-merge")

    def test_pull_request_accepts_regenerated_merge_ref_with_stale_payload_metadata(self) -> None:
        regenerated_merge = "b" * 40
        failures, details = evaluate(
            actual_sha=HEAD,
            expected_sha=HEAD,
            github_sha=regenerated_merge,
            event_name="pull_request",
            payload={"pull_request": {"head": {"sha": HEAD}, "merge_commit_sha": MERGE}},
            release_id=f"liqi-v1-ci-{HEAD}",
        )
        self.assertEqual(failures, [])
        self.assertEqual(details["github_sha"], regenerated_merge)
        self.assertEqual(details["pull_request_merge_sha"], MERGE)
        self.assertEqual(details["github_sha_kind"], "pull-request-merge")

    def test_mismatched_checkout_event_or_release_fails_closed(self) -> None:
        failures, _details = evaluate(
            actual_sha=MERGE,
            expected_sha=HEAD,
            github_sha=MERGE,
            event_name="pull_request",
            payload={"pull_request": {"head": {"sha": MERGE}, "merge_commit_sha": MERGE}},
            release_id=f"liqi-v1-ci-{MERGE}",
        )
        self.assertGreaterEqual(len(failures), 3)


if __name__ == "__main__":
    unittest.main()
