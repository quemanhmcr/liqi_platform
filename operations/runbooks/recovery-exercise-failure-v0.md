# Recovery exercise failure V0

1. Confirm source database and production traffic were not changed.
2. Preserve the plan, result, redacted step logs, provider verification and freshness output.
3. If cleanup failed, fence the isolated target and declare an incident; do not reuse its data directory.
4. Return provider command failures to Senior 2 with the exact step and evidence reference.
5. Do not mark backup complete or fresh from backup creation alone.
6. A passing restore must still satisfy migration, probe, RPO and RTO invariants.
7. Re-run only after source fixes and a new approval reference where required.
