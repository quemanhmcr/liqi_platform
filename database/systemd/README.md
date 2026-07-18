# Database systemd provider templates

These units are provider templates and do not enable services automatically.

The application host requires `/etc/liqi/database/backup.env` rendered from `database/config/backup.env.template` with the exact source SHA, reviewed WireGuard-overlay repository hostname, fixed TLS port 8432, independent repository path and checksummed capacity evidence paths. The file contains no secret value.

OCI Vault materialization and systemd `LoadCredential=` supply:

- `pgbackrest-repo-ca`;
- `pgbackrest-repo-client-cert`;
- `pgbackrest-repo-client-key`;
- `pgbackrest-cipher-passphrase`.

The PostgreSQL `archive_command` uses the same root-materialized files under `/run/liqi/secrets/database`; backup and repository-check units receive private per-unit credential copies. No S3 key, Customer Secret Key, bucket or OCI Object Storage namespace is accepted.

Senior 4 may enable timers only after source validation, PostgreSQL integration tests, `pgbackrest stanza-create/check`, a successful full backup, checksummed fresh capacity evidence and an isolated restore drill. Backup and repository-check units share `/run/lock/liqi-database-backup.lock` and cannot overlap.
