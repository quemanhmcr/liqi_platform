#!/usr/bin/env bash
set -euo pipefail
: "${LIQI_RUNTIME_CONFIG_PATH:?LIQI_RUNTIME_CONFIG_PATH is required}"
port="$(python3 -c 'import json,os; print(json.load(open(os.environ["LIQI_RUNTIME_CONFIG_PATH"]))["http"]["port"])')"
curl --silent --show-error --fail "http://127.0.0.1:${port}/health/live"
curl --silent --show-error --fail "http://127.0.0.1:${port}/health/ready"
