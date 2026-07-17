# ADR 0101: OCI Secure Landing Zone V0

## Status

Accepted for source and plan validation. No OCI apply has been approved.

## Context

V0 must provision one recoverable LIQI node in a clean OCI tenancy. The capacity envelope is 4 OCPUs, 24 GB RAM, and at most 200 GB combined boot and block storage. The tenancy currently has one subscribed region (`ap-singapore-2`) and one availability domain, so V0 cannot honestly claim high availability.

The host must expose an HTTPS edge while PostgreSQL, PgBouncer, Rust services, telemetry, metrics, and administrative listeners remain non-public. Paid or unverified services must remain disabled unless the owner explicitly acknowledges cost.

## Decision

### Environment and compartment topology

- Create one direct child compartment per environment: `liqi-development`, later `liqi-staging` and `liqi-production`.
- Keep tenancy-level dynamic groups and policies minimal because OCI IAM requires those resource types at tenancy scope.
- Apply free-form tags for project, environment, owner, manager, capacity profile, statefulness, and cost classification where the OCI resource supports tags.
- Maintain a machine-readable classification for all OCI resources, including resources that do not support tags.

A separate parent compartment adds hierarchy but no useful isolation for the single-environment V0 and makes tenancy policy paths harder to inspect. The sibling environment model remains compatible with a future shared-services compartment.

### Network

- One VCN per environment.
- One public edge subnet for the V0 node. The host needs a public edge because a paid load balancer and paid NAT path are disabled by default.
- An explicitly empty security list is attached to the subnet. Network Security Groups are the primary security boundary.
- Public TCP ingress is limited to 80 for redirect/ACME and 443 for TLS edge traffic.
- Public SSH is disabled by default. It can be enabled only for explicit non-world CIDRs through reviewed OpenTofu input.
- PostgreSQL, PgBouncer, Rust process ports, OTLP, metrics, and administrative ports are never permitted by public ingress rules.
- Egress is bounded to HTTP/HTTPS package and certificate traffic, DNS, NTP, and Object Storage through a Service Gateway.
- No paid NAT Gateway, bastion, OCI Load Balancer, Network Firewall, or WAF is enabled by default.

The single public-subnet host is a deliberate V0 compromise, not the target PAYG production topology. PAYG can introduce a managed edge, private application/data subnets, and multiple nodes without changing the `oci-host-v0` consumer contract.

### Compute and operating system

- Shape: `VM.Standard.A1.Flex`.
- Architecture: AArch64.
- Requested profile: 4 OCPUs and 24 GB RAM.
- Operating system: pinned Oracle Linux 9 AArch64 platform image OCID supplied as an input and emitted in the host output.
- Disable legacy IMDS endpoints; runtime access to OCI uses instance principals, never copied operator API credentials.
- Ephemeral public IP is accepted in V0 and explicitly reported as changing on host replacement.

### Storage

- 50 GB boot volume: operating system, release artifacts, container images, bounded logs, and temporary files. It is replaceable.
- 100 GB paravirtualized block volume: PostgreSQL data and local backup staging. It is attached at the OCI consistent device path and mounted by filesystem UUID.
- 50 GB of the Free Tier combined storage envelope remains unallocated for recovery or a later reviewed adjustment.
- The data volume and backup bucket have `prevent_destroy`; removing that protection requires a reviewed source change and explicit owner approval.
- Local backup staging on the data volume is not a backup. Durable backup destination is a private Object Storage bucket reached through the Service Gateway.

### Instance principal and backup permissions

- Dynamic-group membership matches the exact instance OCID rather than every instance in the compartment.
- The host can inspect the designated bucket and list/read/create objects in that bucket.
- The host cannot delete backup objects through its instance principal.
- Optional OCI Vault access is restricted to explicitly supplied secret OCIDs; no secret or Vault is created by default.

Exact-instance membership minimizes privilege but introduces a short IAM propagation period after host replacement. Backup and secret consumers must retry with bounded backoff and fail closed while permission is unavailable.

### Capacity allocation contract

The infrastructure reserves at least 1 OCPU and 4 GB RAM for the operating system, incident response, and recovery. Consumer hard limits must remain at or below the following coordination envelope:

| Component | CPU envelope | Hard memory ceiling |
|---|---:|---:|
| PostgreSQL | 1.50 OCPU | 9.0 GB |
| PgBouncer | 0.10 OCPU | 0.25 GB |
| `liqi-api` | 0.45 OCPU | 2.0 GB |
| `liqi-realtime` | 0.65 OCPU | 3.0 GB |
| `liqi-worker` | 0.35 OCPU | 2.0 GB |
| Reverse proxy/TLS edge | 0.15 OCPU | 0.25 GB |
| OpenTelemetry Collector | 0.15 OCPU | 0.50 GB |
| Host observability and container supervision | 0.15 OCPU | 0.50 GB |
| **Consumer total** | **3.50 OCPU** | **17.50 GB** |
| **Host/recovery reserve** | **0.50 OCPU steady; 1 OCPU scheduling reserve** | **6.50 GB** |

The CPU rows are steady-state planning budgets, not a promise of simultaneous hard quotas. Senior 2, 3, and 4 own final service limits inside the total envelope. Swap remains disabled and cannot be used to hide leaks.

Boot volume budget:

| Use | Budget |
|---|---:|
| OS and package reserve | 18 GB |
| Releases and container images | 14 GB |
| Structured/journal logs | 8 GB |
| Temporary/staging files | 3 GB |
| Emergency free space | 7 GB |

Data volume budget:

| Use | Budget |
|---|---:|
| PostgreSQL data and indexes | 70 GB |
| WAL/recovery working headroom | 15 GB |
| Local backup staging | 10 GB |
| Emergency free space | 5 GB |

Consumers must alert before exceeding their allocation and must not assume the unallocated OCI storage is automatically available.

## Threat assumptions and controls

| Threat | V0 control |
|---|---|
| Internet scan or direct database access | NSG exposes only 80/443; database and internal ports are absent from public rules. |
| Brute-force or stolen SSH credential | SSH disabled by default; root/password/keyboard-interactive auth disabled; optional ingress is CIDR allowlisted. |
| Compromised application process | Non-root service identities, per-service `0700` secret directories, loopback internal listeners, bounded instance-principal IAM. |
| Compromised host/opc account | Runtime principal cannot delete backup objects; stateful data remains on a separate volume; operator access is an audited exception. |
| User API credential copied to host | Prohibited by contract and static validation; instance principal is the only runtime OCI identity. |
| Misconfigured default security list | Subnet receives an explicitly empty security list; NSG source is validated. |
| Root disk or host loss | Deterministic bootstrap, separate preserved data volume, pinned image ID, documented replacement path. |
| Data volume loss or logical corruption | Object Storage backup reference and restore-gated lifecycle; implementation and restore tests belong to Senior 2. |
| OCI availability-domain or regional outage | Not solved in V0; this is an explicit single-node, single-AD risk. |
| OpenTofu state loss or concurrent operators | No apply until state handling and a single-writer procedure are explicitly approved. |

## State management constraint

Validation uses the local backend with `-backend=false` and creates no state. Before the first apply, the owner must approve a durable state strategy. OCI Resource Manager manages Terraform state but does not currently provide an OpenTofu execution contract equivalent to this repository; OCI's S3-compatible Object Storage backend path is deprecated in favor of Terraform's OCI-native backend. V0 therefore does not silently choose an incompatible or credential-heavy backend.

Until that decision is closed, `tofu apply` remains prohibited even if plan validation succeeds.

## Consequences

- V0 is simple, inspectable, and honest about single-node availability.
- The edge host has a public IP, increasing the importance of NSG, host firewall, patching, and non-root services.
- Host replacement changes instance OCID and public IP, requiring IAM propagation and edge/DNS coordination.
- The separate data volume enables host recreation but is not a substitute for tested Object Storage restore.
- The 4/24 profile remains `free-trial-only` until tenancy-specific billing evidence says otherwise.
