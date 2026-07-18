#!/usr/bin/env bash
set -euo pipefail
umask 077
STATE_DATABASE=${STATE_DATABASE:-liqi_infra_state}
STATE_SCHEMA=${STATE_SCHEMA:-opentofu_v1_live}
STATE_ROLE=${STATE_ROLE:-liqi_tofu_state}
fail(){ printf 'ERROR: %s
' "$*" >&2; exit 1; }
require_command(){ command -v "$1" >/dev/null 2>&1 || fail "required command missing: $1"; }
require_file_0600(){ local p=$1; [ -f "$p" ] || fail "required protected file missing: $p"; local m; m=$(stat -c '%a' "$p" 2>/dev/null || true); [ -z "$m" ] || [ "$m" = 600 ] || [ "$m" = 400 ] || fail "$p must be mode 0600 or 0400"; }
validate_identifier(){ [[ "$1" =~ ^[a-z_][a-z0-9_]{0,62}$ ]] || fail "invalid PostgreSQL identifier"; }
validate_common(){ validate_identifier "$STATE_DATABASE"; validate_identifier "$STATE_SCHEMA"; validate_identifier "$STATE_ROLE"; [ -n "${PGSERVICEFILE:-}" ] || fail 'PGSERVICEFILE is required'; require_file_0600 "$PGSERVICEFILE"; }
json_sha(){ sha256sum "$1" | awk '{print $1}'; }
