# ADR 4001: V1 secret material and OpenTofu state boundary

- Status: Accepted for source implementation; remote lock capability evidence pending
- Date: 2026-07-18
- Owner: Senior 4
- Consumers: Senior 2, Senior 5

## Context

OpenTofu state contains managed resource attributes, including values marked sensitive. Creating OCI Vault secret versions with plaintext or base64 secret content in OpenTofu would therefore create a second durable copy of production secrets in state. V1 also needs a single-writer remote state with recovery and locking, but OpenTofu has no native OCI backend. OCI's S3-compatible Object Storage API can store state, while compatibility of conditional-write lock files must be proven before an apply.

## Decision

OpenTofu manages the Vault, master key, bucket references, dynamic group and IAM policy. It never receives a secret value and does not manage `oci_vault_secret.secret_content`.

Secret metadata consists only of secret OCIDs and target credential names under `/etc/liqi/secrets`. An approved OCI CLI command creates or rotates a secret version from a protected input file outside Git. The host retrieves secret bundles with its instance principal and materializes service-scoped credentials under `/run/liqi`; plaintext does not enter OpenTofu variables, outputs, plans or state.

The `v1-live` environment uses a partial S3 backend configuration against a separately approved, versioned OCI Object Storage state bucket. Credentials are supplied through an operator-owned credentials file, never `-backend-config` values or tracked source. `use_lockfile=true` is mandatory, but apply remains blocked until a two-writer capability test proves OCI's compatibility with OpenTofu conditional-write locking. State and plan encryption are enforced by environment-supplied OpenTofu encryption configuration; loss of that key is treated as a recovery incident.

The state bucket is not created by the workload stack because a backend cannot depend on resources in its own state. Bootstrap of the state bucket is a separate approved mutation with its own exact plan and evidence.

## Consequences

There is no secret value in source or OpenTofu state by design. There is a small operational cost: secret lifecycle and state-backend bootstrap are protected workflows rather than ordinary module resources. This is preferred to silently duplicating durable secret authority.

Compatibility status remains `pending-lock-capability-evidence` until the state backend test passes. Owner: Senior 4. Removal condition: attach machine-readable lock test evidence and set the infrastructure output to `validated` before first apply.
