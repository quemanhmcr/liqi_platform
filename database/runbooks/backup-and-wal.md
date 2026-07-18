# PostgreSQL V1 backup and WAL archive runbook

## Authority and topology

- PostgreSQL on the OCI application host is application durable authority.
- Encrypted pgBackRest backup/WAL authority is independent management storage at `/independent-storage/pgbackrest/liqi`.
- The client reaches pgBackRest server by mutual TLS on port 8432 over WireGuard. No public database/repository ingress and no S3/Object Storage credential are valid V1 inputs.
- JSON backup metadata on the application host is derived evidence. Durable identity lives in pgBackRest backup annotations and can be reconstructed after host loss.

## Management repository preflight

On independent management hardware:

1. provision dedicated `pgbackrest` ownership and the independent filesystem;
2. establish the WireGuard overlay and bind pgBackRest TLS server only to its private address;
3. render `database/management/pgbackrest-repository/pgbackrest-server.conf.template`;
4. validate server config, certificate CN authorization and repository filesystem backup/restore;
5. publish exact-SHA capacity evidence:

```bash
python database/management/pgbackrest-repository/report-capacity.py \
  --git-sha <exact-clean-source-sha> \
  --output /protected/evidence/database-backup-capacity-v1.json
```

Transfer the JSON and sidecar through the encrypted management path into `/run/liqi/management/`. Files must be root-owned, non-symlink and not group/world writable. A production timer is not enabled until a bounded recurring refresh mechanism is operational; evidence older than 15 minutes blocks backup.

## Application-host configuration

Render `/etc/liqi/database/backup.env` from `database/config/backup.env.template`, replacing repository hostname and exact source SHA. Render pgBackRest config:

```bash
set -a
. /etc/liqi/database/backup.env
set +a
database/bin/render-pgbackrest-config.sh
```

The persistent config contains only paths, hostname, port, retention and bounds. `archive-push-queue-max=2GiB` is a hard disk-safety bound, and `cmd` pins generated recovery commands to the reviewed credential wrapper. OCI Vault/systemd credentials provide repository CA, client certificate, client private key and cipher passphrase.

## First repository mutation

These commands mutate the independent repository and require the approved recovery mission:

```bash
database/bin/pgbackrest-command.sh --stanza=liqi stanza-create
database/bin/pgbackrest-command.sh --stanza=liqi check
```

Expected: TLS/mTLS succeeds, stanza status is `ok`, and a WAL segment is archived to the independent repository.

## Capacity preflight

```bash
database/bin/backup-capacity-check.sh
```

The guard validates schema, SHA-256 sidecar, exact source SHA and freshness. It subtracts the management provider reserve, then requires free space for the current database plus a safety margin of at least 1 GiB. Missing or insufficient evidence exits non-zero before a recovery probe or backup write.

## Full and differential backup

```bash
database/bin/backup.sh full
database/bin/backup.sh diff
```

A successful command must create a terminal recovery probe, pass repository/capacity checks, write all exact-source annotations, complete pgBackRest archive consistency checking, derive checksummed `database-backup-metadata-v1`, update local latest evidence atomically and emit `durableAuthority=pgbackrest-backup-annotations`.

The metadata can be reconstructed without the source host:

```bash
database/recovery/fetch-backup-metadata.sh \
  <pgBackRest-label> \
  /var/lib/liqi/recovery-exercises/<id>/metadata
```

## Recovery status

Backup freshness does not prove restore. Current backup/archive status and a checksummed isolated restore result must agree before recovery readiness passes:

```bash
LIQI_ENVIRONMENT=production \
LIQI_BACKUP_METADATA_FILE=/var/lib/liqi/postgresql/backup-staging/metadata/latest.json \
LIQI_RESTORE_RESULT_FILE=/var/lib/liqi/postgresql/backup-staging/restore/latest/restore-result.json \
LIQI_RECOVERY_STATUS_OUTPUT=/var/lib/liqi/postgresql/backup-staging/metadata/recovery-status-v0.json \
  database/bin/recovery-status.sh
```

`recovery-status-v1.sh` additionally binds backup/restore evidence to the exact release source SHA and enforces 300-second RPO and 3,600-second RTO targets.

## Failure handling

Repository TLS loss, archive failure, archive queue overflow, stale capacity evidence, a missing annotation, checksum mismatch or failed restore immediately invalidates recovery readiness. PostgreSQL remains current data authority, but command cutover and the 5-minute RPO claim stay blocked. Repair the provider seam, take a new full backup and repeat isolated restore; do not enlarge local spool without an approved capacity change.

Certificate/key or cipher rotation keeps old decrypt capability until every backup in the old retention window has expired and at least one backup under new material has passed isolated restore. Never delete the only readable material first.
