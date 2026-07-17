# Database recovery evidence artifact V0

Promotion consumes an artifact produced by a completed Senior 2 recovery evidence workflow. The artifact is immutable evidence, not a backup repository and not permission to restore.

Required layout:

```text
metadata/
├── latest.json
├── latest.json.sha256
├── restore-source.json
├── restore-source.json.sha256
├── backup-status-v0.json
└── backup-status-v0.json.sha256
restore/
├── restore-result.json
└── restore-result.json.sha256
```

Senior 2 `database/bin/recovery-status.sh` verifies every checksum and contract, confirms current backup/archive readiness, requires a passing isolated restore, checks PostgreSQL major and current migration compatibility, and emits `recovery-status-v0`.

Senior 4 then applies `recovery-policy-v0` for backup age, WAL lag, restore freshness, RPO and RTO. The raw evidence package is not interpreted or repaired by Senior 4.

The workflow run ID and artifact name are manual promotion inputs. Evidence paths are passed by environment reference and are not rendered in provider command records.
