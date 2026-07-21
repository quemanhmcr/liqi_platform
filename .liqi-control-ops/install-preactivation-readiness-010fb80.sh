#!/bin/bash
set -euo pipefail
release_sha='33e7a093005f5bdf671cb0ced592c5594ec56c18'
readiness_source='/home/opc/liqi-host-readiness-010fb80'
caddy_source='/home/opc/Caddyfile.fail-closed-010fb80'
readiness_sha='043e853c9231f6c2d032cd4ff4ecf890530d36ae25a331d5c835d48d416baaa8'
caddy_sha='b92161b0521a69738c8ba5579c65406c91c80946ace86c8bca17e676a47c02ef'
readiness_target='/usr/local/libexec/liqi-host-readiness'
caddy_target='/etc/caddy/Caddyfile'
backup_root='/var/lib/liqi/recovery/preactivation-readiness-010fb80'
host_evidence='/var/lib/liqi/recovery/host-runtime-33e7a09.json'
[[ "$(id -u)" -eq 0 ]]
printf "%s  %s\n" "$readiness_sha" "$readiness_source" | sha256sum --check --strict
printf "%s  %s\n" "$caddy_sha" "$caddy_source" | sha256sum --check --strict
[[ -f "$backup_root/liqi-host-readiness.before" ]]
[[ -f "$backup_root/Caddyfile.before" ]]
/usr/bin/python3.11 -m py_compile "$readiness_source"
/usr/bin/caddy validate --config "$caddy_source"
rollback() {
  status=$?
  /usr/bin/install -o root -g root -m 0755 "$backup_root/liqi-host-readiness.before" "$readiness_target" || true
  /usr/bin/install -o root -g caddy -m 0640 "$backup_root/Caddyfile.before" "$caddy_target" || true
  /bin/systemctl reload caddy.service || true
  exit "$status"
}
trap rollback ERR
readiness_tmp=$(mktemp /usr/local/libexec/.liqi-host-readiness.XXXXXX)
caddy_tmp=$(mktemp /etc/caddy/.Caddyfile.XXXXXX)
/usr/bin/install -o root -g root -m 0755 "$readiness_source" "$readiness_tmp"
/usr/bin/install -o root -g caddy -m 0640 "$caddy_source" "$caddy_tmp"
/usr/bin/mv -f "$readiness_tmp" "$readiness_target"
/usr/bin/mv -f "$caddy_tmp" "$caddy_target"
/bin/systemctl reload caddy.service
/bin/systemctl is-active --quiet caddy.service
http_code=$(curl --silent --show-error --output /dev/null --write-out "%{http_code}" http://127.0.0.1/)
https_code=$(curl --insecure --silent --show-error --output /dev/null --write-out "%{http_code}" https://127.0.0.1/)
[[ "$http_code" == 503 ]]
[[ "$https_code" == 503 ]]
"$readiness_target" --source-git-sha "$release_sha" --output "$host_evidence"
printf "readiness_sha=%s\ncaddy_sha=%s\nhttp=%s\nhttps=%s\n" "$readiness_sha" "$caddy_sha" "$http_code" "$https_code" > "$backup_root/result.txt"
chown root:liqi "$host_evidence" "$backup_root/result.txt"
chmod 0640 "$host_evidence" "$backup_root/result.txt"
trap - ERR
echo preactivation_readiness_remediation_passed
