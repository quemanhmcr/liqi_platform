#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
PYTHONDONTWRITEBYTECODE=1 python "$ROOT_DIR/database/tools/validate_database_contract.py"
PYTHONDONTWRITEBYTECODE=1 python "$ROOT_DIR/database/tests/contract/validate_v1_contracts.py"
PYTHONDONTWRITEBYTECODE=1 python "$ROOT_DIR/database/tools/validate_database_capacity.py"
if [[ -f "$ROOT_DIR/contracts/platform/oci-host-v0.example.json" ]]; then
  PYTHONDONTWRITEBYTECODE=1 python "$ROOT_DIR/database/tools/validate_oci_host_adapter.py" \
    "$ROOT_DIR/contracts/platform/oci-host-v0.example.json"
fi
if [[ -f "$ROOT_DIR/contracts/infrastructure/oci-live-v1.example.json" ]]; then
  PYTHONDONTWRITEBYTECODE=1 python "$ROOT_DIR/database/tools/validate_v1_host_adapter.py" \
    "$ROOT_DIR/contracts/infrastructure/oci-live-v1.example.json"
fi
PYTHONDONTWRITEBYTECODE=1 python "$ROOT_DIR/database/tools/recovery_contract.py" validate-contracts
PYTHONDONTWRITEBYTECODE=1 python "$ROOT_DIR/database/tests/static/validate_database_source.py"
PYTHONDONTWRITEBYTECODE=1 python "$ROOT_DIR/database/tests/static/validate_beam_provider_source.py"
if [[ -f "$ROOT_DIR/contracts/events/examples/platform-probe-requested-v0.json" ]]; then
  PYTHONDONTWRITEBYTECODE=1 python "$ROOT_DIR/database/tests/contract/validate_wire_mapping.py" \
    "$ROOT_DIR/contracts/events/examples/platform-probe-requested-v0.json"
fi
PYTHONDONTWRITEBYTECODE=1 python "$ROOT_DIR/database/tests/static/test_recovery_contract.py"
PYTHONDONTWRITEBYTECODE=1 python "$ROOT_DIR/database/tests/static/test_restore_drill_provider.py"
for test_script in "$ROOT_DIR"/database/tests/static/test_*.sh; do
  bash "$test_script"
done
for script in \
  "$ROOT_DIR"/database/bin/*.sh \
  "$ROOT_DIR"/database/tests/integration/*.sh \
  "$ROOT_DIR"/database/tests/static/*.sh \
  "$ROOT_DIR"/database/recovery/*.sh; do
  bash -n "$script"
done
printf '%s\n' '{"validation":"database-shell-syntax-v0","passed":true}'
