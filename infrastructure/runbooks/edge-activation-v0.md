# V0 edge activation runbook

## Scope

The bootstrap installs and starts NGINX in a fail-closed state. TCP 80 accepts only the default server and closes requests with `444`; TCP 443 rejects the TLS handshake. No public application route exists until DNS, certificate paths, and an activation approval are supplied.

This runbook prepares a reviewed edge change. It does not issue a certificate, modify DNS, deploy a release, or authorize a host mutation.

## Provider sources

- Bootstrap default: `infrastructure/edge/nginx.conf`
- systemd hardening: `infrastructure/edge/nginx-liqi-hardening.conf`
- approved-site template: `infrastructure/edge/liqi-site-v0.conf.tftpl`

Cloud-init materializes only the default and hardening files. The site template is intentionally not rendered during bootstrap.

## Approval prerequisites

1. Host readiness reports `edge_fail_closed=pass` and bootstrap version `0.3.0`.
2. DNS name and ownership are approved.
3. Certificate and private-key files already exist on the host through an approved secret/certificate process.
4. Certificate paths are root-readable, are not stored in Git, and do not appear in runtime config.
5. API and realtime are healthy on loopback ports 8080 and 8081.
6. The reviewed template values contain one canonical DNS name and absolute certificate paths.
7. An operator approval reference identifies the exact Git SHA and rendered file digest.

## Reviewed rendering

Render the template in a secure temporary directory. Do not use the request `Host` header as a redirect target. The rendered HTTP redirect must contain the approved DNS name literally.

Expected routes:

- `/platform/v0/realtime` proxies to loopback realtime and preserves WebSocket upgrade headers;
- all other paths proxy to loopback API;
- upstream connection and response timeouts remain bounded;
- request body limit remains 1 MiB;
- upstream 502/503/504 responses become a fail-closed JSON 503.

## Owner-run activation

After review, the owner installs the rendered file under `/etc/nginx/liqi-enabled/` with root ownership and mode `0644`, runs `nginx -t`, and reloads NGINX. Installation and reload are host mutations and require explicit approval.

Rollback removes the approved site file, runs `nginx -t`, and reloads NGINX. The bootstrap default then resumes rejecting public traffic.
