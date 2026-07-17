# Runbook: recovery evidence stale V0

Owner: Senior 2 for backup/restore implementation; Senior 4 for promotion gate and incident coordination.

1. Block environment promotion when backup, WAL archive or restore verification freshness fails.
2. Distinguish telemetry missing from evidence genuinely stale; both fail the gate.
3. Verify backup evidence is encrypted and off-host. A file on the same boot/block volume is not a backup.
4. Run the Senior 2-owned restore procedure into an isolated target.
5. Verify schema/data invariants and record observed RPO/RTO.
6. Publish a new `recovery-status-v0` document and rerun the freshness gate.
7. Escalate when RPO/RTO objectives are exceeded or evidence cannot be trusted.
