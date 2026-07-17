# Database systemd provider templates

These units are provider templates, not an instruction to enable services automatically.

Senior 1 must provide:

- `/etc/liqi/database/backup.env` with non-secret host paths, Object Storage namespace/region/bucket and `oci-host-v0` references.
- systemd `LoadCredential=` drop-ins for `pgbackrest-s3-key`, `pgbackrest-s3-secret` and `pgbackrest-cipher-passphrase`.
- bounded writable directories for pgBackRest spool/logs and backup metadata.
- the stable deployment path `/opt/liqi/current` or a compatible adapter path.

Senior 4 may enable timers only after source validation, PostgreSQL integration tests, `pgbackrest stanza-create/check`, a successful full backup and an isolated restore drill. The units do not create OCI resources.

Backup and repository-check units share `/run/lock/liqi-database-backup.lock`; they must not overlap. Resource limits implement `contracts/platform/database-capacity-budget-v0.json`.
