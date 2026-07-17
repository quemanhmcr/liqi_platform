#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

from pglast import parse_sql

ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = ROOT / "migrations"
MANIFEST = MIGRATIONS / "manifest.sha256"

failures: list[str] = []
files = sorted(MIGRATIONS.glob("[0-9]???????????_*.sql"))
if not files:
    failures.append("no migrations found")

name_re = re.compile(r"^[0-9]{12}_[a-z0-9_]+\.sql$")
versions: list[int] = []
for path in files:
    if not name_re.fullmatch(path.name):
        failures.append(f"invalid migration name: {path.name}")
    versions.append(int(path.name.split("_", 1)[0]))
    sql = path.read_text(encoding="utf-8")
    try:
        parse_sql(sql)
    except Exception as exc:
        failures.append(f"PostgreSQL parser rejected {path.name}: {exc}")
    if re.search(r"\b(DROP\s+(TABLE|SCHEMA|COLUMN)|ALTER\s+TABLE\s+.+\s+RENAME\s+)\b", sql, re.IGNORECASE):
        failures.append(f"destructive SQL requires decision note: {path.name}")
    if "PASSWORD" in sql.upper() or "postgresql://" in sql.lower():
        failures.append(f"secret-shaped SQL found: {path.name}")

if versions != sorted(set(versions)):
    failures.append("migration versions are not unique and ordered")

manifest_entries: dict[str, str] = {}
for line in MANIFEST.read_text(encoding="utf-8").splitlines() if MANIFEST.exists() else []:
    checksum, name = line.split(maxsplit=1)
    manifest_entries[name.lstrip("*")] = checksum

for path in files:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if manifest_entries.get(path.name) != digest:
        failures.append(f"manifest checksum mismatch: {path.name}")
if set(manifest_entries) != {path.name for path in files}:
    failures.append("manifest file set differs from migration directory")

config = (ROOT / "config/pgbouncer.ini").read_text(encoding="utf-8")
required_pgbouncer = {
    "pool_mode = transaction",
    "max_client_conn = 300",
    "max_db_connections = 40",
    "listen_addr = 127.0.0.1",
    "auth_type = scram-sha-256",
}
for line in required_pgbouncer:
    if line not in config:
        failures.append(f"missing PgBouncer contract line: {line}")

postgres_config = (ROOT / "config/postgresql-liqi.conf").read_text(encoding="utf-8")
for line in ["listen_addresses = ''", "max_connections = 80", "archive_timeout = '5min'", "password_encryption = 'scram-sha-256'"]:
    if line not in postgres_config:
        failures.append(f"missing PostgreSQL contract line: {line}")

text_suffixes = {".sql", ".sh", ".py", ".conf", ".ini", ".json", ".md", ".sha256"}
all_text = "\n".join(
    path.read_text(encoding="utf-8")
    for path in ROOT.rglob("*")
    if path.is_file() and path.suffix in text_suffixes
)
secret_patterns = [
    re.compile(r"postgres(?:ql)?://[^\s]+:[^\s]+@", re.IGNORECASE),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
]
for pattern in secret_patterns:
    if pattern.search(all_text):
        failures.append(f"secret material pattern detected: {pattern.pattern}")

if failures:
    for failure in failures:
        print(f"ERROR: {failure}", file=sys.stderr)
    raise SystemExit(1)

print(json.dumps({
    "validation": "database-source-v0",
    "migrations": len(files),
    "latestVersion": max(versions),
    "pgbouncerMode": "transaction",
    "plaintextSecrets": False,
    "passed": True,
}, separators=(",", ":")))
