# ADR 0102: Deterministic Host Bootstrap and Replacement

## Status

Accepted for V0 source. No host has been created.

## Context

The V0 host must be replaceable without manual configuration and must present a stable filesystem, identity, port, and readiness seam to database, runtime, and release consumers. The bootstrap must never format an unknown or root device, must not carry secrets, and must fail closed when security checks do not pass.

## Decision

### Bootstrap mechanism

Use OCI instance metadata `user_data` with a versioned cloud-init template. OpenTofu gzip-compresses and Base64-encodes the rendered template, enforces the OCI 16 KiB encoded limit, hashes the uncompressed rendered template, and marks a bootstrap revision change as a host-replacement trigger. `validate_infrastructure.py --with-tofu` renders the cloud-config without planning, parses it, verifies exact provider-owned file bytes, and reports encoded size. The Oracle Linux platform image OCID, provider lock file, bootstrap version, and output version together define the reproducible host input.

V0 guarantees deterministic semantics, identities, permissions, mount paths, and readiness checks. It does not claim bit-for-bit package reproducibility: packages are installed from the Oracle Linux repositories associated with the pinned image. A custom image pipeline is deferred until package drift or boot time justifies its operational cost.

### Identity and privilege

- `opc` is the approved administrative and release transport identity; it never runs application services.
- `liqi-api`, `liqi-realtime`, and `liqi-worker` are locked non-login service users with stable numeric IDs and shared group `liqi`.
- PostgreSQL directories use the stable OS identity name `postgres`; database packages, roles, cluster initialization, and tuning remain Senior 2 responsibilities.
- Release uploads land in `/var/tmp/liqi/releases`, owned by `opc`; promotion installs root-owned immutable release content under `/opt/liqi/releases`.
- Runtime secrets are materialized only under per-service `0700` directories. The shared root is traversal-only (`0710`) and does not let `opc` or sibling services read another service's material.

### Data-volume safety

The bootstrap waits for `/dev/oracleoci/oraclevdb`, resolves the actual block device, and refuses to continue if it resolves to the root filesystem. It formats XFS only when no filesystem and no unknown disk signatures are present. Existing non-XFS filesystems fail closed. The volume is mounted by UUID in `/etc/fstab`; required PostgreSQL directories are created only after the mount is verified.

The `nofail` mount option avoids an unbootable OS during device incidents, but the LIQI readiness service fails until the durable volume is mounted. Application and database units must require the data-volume and readiness units rather than relying only on `multi-user.target`.

### Security baseline

- Root, password, empty-password, keyboard-interactive, X11, agent-forwarding, and TCP-forwarding SSH paths are disabled.
- SELinux must be enforcing.
- Firewalld mirrors approved edge services; SSH follows the same opt-in decision as the NSG.
- Swap is disabled.
- Conservative kernel/network sysctls are applied.
- Legacy IMDS endpoints are disabled in OCI and checked from the host; IMDSv2 access requires the Oracle authorization header.
- Runtime base units are owned under `services/systemd/**`, execute as dedicated non-root identities, and fail before start when config or per-service secret material is absent.
- The infrastructure provider owns a default NGINX configuration that rejects HTTP and TLS traffic until an approved site file is installed; request sizes, upstream timeouts, WebSocket upgrade behavior, and filesystem/capability bounds are explicit.

### Readiness

`liqi-host-readiness.service` writes `/run/liqi/host-ready.json` atomically only after all required identities, permissions, data mount, swap, SELinux, firewall, SSH, IMDS, runtime-unit, capacity-control, and fail-closed-edge checks pass. The file contains no secret. Consumers require bootstrap version `0.3.0` and may read it directly, but must treat absence, invalid JSON, a wrong schema/version, or any non-`ready` status as not ready.

Readiness proves the host seam, not application/database readiness. Senior 2 and Senior 3 must publish their own readiness under their owned service contracts.

### Replacement model

- Boot volume and host are replaceable.
- Data block volume and Object Storage bucket are preserved.
- A replacement reruns the same bootstrap, detects and mounts the existing XFS volume, updates the exact-instance dynamic group, and emits a new host/public address in `oci_host_v0`.
- Replacement requires a plan review because it changes public IP, instance OCID, instance-principal propagation, and potentially the pinned OS image.
- Rollback means returning to a previously reviewed module/bootstrap version and replacing the host; no OCI Console edits are authoritative.

## Rejected alternatives

- **Manual SSH provisioning:** not reproducible and creates hidden state.
- **Formatting by Linux device name without signature/root checks:** can destroy the wrong device.
- **PostgreSQL on boot disk:** breaks host replacement and recovery semantics.
- **Shared runtime user or world-readable secret directory:** expands blast radius after process compromise.
- **Custom image pipeline in V0:** additional build, signing, patch, and registry lifecycle before it provides measured value.
- **Kubernetes/OKE:** exceeds V0 complexity and capacity requirements.

## V0 integration closeout

Bootstrap `0.3.0` is the first version that directly materializes the runtime provider base units and the infrastructure-owned fail-closed edge. The host output publishes `bootstrap_version`; deployment and activation contracts consume it directly. Runtime services remain disabled until release activation has verified artifacts, configuration, per-service secrets, database readiness, and health targets.

The compressed rendered cloud-config is machine-checked below the OCI `user_data` limit. This avoids a hidden portability failure while preserving the exact tracked bytes of the provider outputs.

Official platform references used for this decision:

- OCI instance metadata/user-data limits: https://docs.oracle.com/en-us/iaas/Content/Compute/Concepts/computeoverview.htm#Metadata_Key_Limits
- OpenTofu `base64gzip`: https://opentofu.org/docs/language/functions/base64gzip/
- cloud-init compressed user-data formats: https://docs.cloud-init.io/en/latest/explanation/format/index.html
- NGINX TLS handshake rejection: https://nginx.org/en/docs/http/ngx_http_ssl_module.html#ssl_reject_handshake

## Consequences

- A cloud-init defect causes replacement rather than an in-place partial repair; this is intentional for a deterministic host.
- Package repository drift remains a recorded risk until a custom image pipeline is justified.
- Host readiness is strict and can block services after security drift, preferring safe unavailability over permissive startup.
