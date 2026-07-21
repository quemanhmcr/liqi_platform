#!/bin/bash
set -euo pipefail
systemctl show pgbouncer.service -p ActiveState -p SubState -p Result -p ExecMainStatus -p Slice --no-pager
ss -H -lnt | awk '$4 ~ /:6432$/ {print}'
p=$(find /run/liqi -maxdepth 4 -type f -name 'monitor-pgpass' -print -quit)
[[ -n "$p" ]]
sudo -n -u postgres env PATH=/usr/pgsql-17/bin:/usr/bin:/bin PGHOST=127.0.0.1 PGPORT=6432 PGDATABASE=liqi PGUSER=liqi_monitor PGPASSFILE="$p" LIQI_REQUIRED_MIGRATION_VERSION=8 LIQI_REQUIRED_OBAN_MIGRATION_VERSION=14 /usr/local/lib/liqi-database/database/bin/readiness-v1.sh | sudo -n tee /var/lib/liqi/recovery/migration-readiness-v1.json
sudo -n -u postgres env PATH=/usr/pgsql-17/bin:/usr/bin:/bin PGHOST=127.0.0.1 PGPORT=6432 PGDATABASE=liqi PGUSER=liqi_monitor PGPASSFILE="$p" /usr/local/lib/liqi-database/database/bin/durable-work-metrics-v1.sh | sudo -n tee /var/lib/liqi/recovery/durable-work-metrics-v1.prom
sudo -n chmod 0640 /var/lib/liqi/recovery/migration-readiness-v1.json /var/lib/liqi/recovery/durable-work-metrics-v1.prom
echo readiness_gate=passed
