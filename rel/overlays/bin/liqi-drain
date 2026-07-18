#!/usr/bin/env bash
set -euo pipefail
: "${LIQI_RUNTIME_CONFIG_PATH:?LIQI_RUNTIME_CONFIG_PATH is required}"
port="$(python3 -c 'import json,os; print(json.load(open(os.environ["LIQI_RUNTIME_CONFIG_PATH"]))["http"]["port"])')"
ref="$(python3 -c 'import json,os; print(json.load(open(os.environ["LIQI_RUNTIME_CONFIG_PATH"]))["shutdown"]["drainTokenRef"])')"
case "$ref" in
  file://*) token="$(cat "${ref#file://}")" ;;
  systemd-credential://*) token="$(cat "${CREDENTIALS_DIRECTORY:?}/${ref#systemd-credential://}")" ;;
  *) echo "unsupported drain token reference" >&2; exit 2 ;;
esac
printf 'header = "x-liqi-drain-token: %s"\n' "$token" |
  curl --silent --show-error --fail --config - --request POST "http://127.0.0.1:${port}/platform/v1/runtime/drain"
