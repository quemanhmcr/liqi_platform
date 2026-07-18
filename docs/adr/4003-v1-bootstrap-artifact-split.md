# ADR 4003: Split baseline cloud-init from the signed host runtime bundle

- Status: Accepted
- Date: 2026-07-18
- Owner: Senior 4
- Consumers: Senior 5

## Context

The first V1 host design embedded package installation, all systemd units, Caddy, OpenTelemetry and deployment scripts in OCI instance metadata. The rendered gzip/base64 `user_data` measured 21,756 bytes, above OCI Compute's 16 KiB metadata limit. Compressing more aggressively or deleting validation code would make the bootstrap fragile and reduce evidence quality.

The baseline host also needs a supply-chain boundary distinct from infrastructure creation. A cloud-init retry should not silently install a different runtime revision, and a runtime package update should not require replacing the OCI instance or preserved data volume.

## Decision

Cloud-init contains only:

- Oracle Linux 9/aarch64 baseline checks.
- OCI CLI and minimal host tools.
- Stable users/groups and filesystem roots.
- Preserved block-volume discovery and mount.
- SSH masking and 80/443 host firewall policy.
- A guarded Object Storage installer plus an Ed25519 public trust root.

The current rendered bootstrap measures 12,188 bytes. Runtime files are built into a deterministic signed `host-bundle-v1` archive. The manifest binds every source/target path, mode, owner, size and SHA-256. Installation downloads through instance principal, verifies the exact manifest signature and archive inventory, installs package artifacts from pinned primary sources, validates systemd/Caddy/OpenTelemetry, and leaves Caddy fail-closed. It does not initialize PostgreSQL, activate an application release or enable public traffic.

Object Storage publication and host installation are separately approval-gated mutations. The source-validation public key is forbidden by the OpenTofu live-plan guard.

## Consequences

Host replacement remains reproducible while runtime installation is independently reviewable and rollbackable. The additional artifact is intentional operational complexity with a bounded owner and exact removal/retention policy. No temporary production fallback is introduced.
