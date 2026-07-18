# Backup, WAL and restore freshness V1

## Trigger
- backup age, WAL lag or restore freshness alert fires
- Promotion is missing an approved isolated restore result

## Expected degradation and safety

Promotion and cutover remain blocked. No claim of recoverability is accepted until an isolated restore plus PITR and read-only Elixir probe pass.

## Operator procedure
1. Do not restore over the live database.
2. Capture latest valid backup metadata/checksum, WAL archive position, release/schema compatibility and prior restore result.
3. Obtain explicit approval before the Senior 2 provider command mutates an isolated restore target.
4. Run the published restore sequence: select backup, isolated restore, PITR, migrations, invariants, read-only probe, cleanup.
5. Keep V1 NOT READY when cleanup, compatibility, checksum, RPO or RTO evidence is missing.

## Evidence to retain

Exact Git SHA and release ID; UTC start/end; alert and dashboard references; provider-owned command/result; p50/p95/p99/max where applicable; CPU, memory, disk and queue state; correctness counters; operator and approval reference for any mutation.

Do not place credentials, tokens, raw session tokens, PEM material or unredacted crash dumps in evidence.

## Recovery acceptance

The exact release compatibility window has a fresh passed restore result, cleanup passed and observed RPO/RTO meet approved objectives.

A missing, stale, synthetic, blocked or release-mismatched result is **not** a pass.
