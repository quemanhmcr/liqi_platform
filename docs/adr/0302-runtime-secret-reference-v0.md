# ADR 0302: Runtime secret references V0

- Status: Accepted
- Date: 2026-07-17
- Owner: Senior 3

## Context

Runtime configuration must be versioned and reproducible without placing database passwords, signing keys, OCI credentials, or tokens in Git or logs. Senior 1 owns the final OCI secret-reference and host-path contracts.

## Decision

- Configuration stores only a URI-shaped secret reference, never a secret value.
- V0 reserves `env://`, `file://`, and `oci-vault://` schemes.
- Local/test resolution may use environment or absolute file references.
- Production refuses unresolved or unsupported references. There is no permissive fallback.
- Resolved values use a redacting, zeroizing secret wrapper and are never serializable.
- Logs may record the scheme and a generic resolution outcome, not the secret value or full provider locator.
- `oci-vault://` is a reserved provider seam until Senior 1 publishes the OCI output/identity contract. Senior 3 will consume that seam rather than inventing an OCI credential flow.

## Compatibility

A future reference rename must support the old scheme for at least one release window. Removing a scheme requires a migration command, consumer list, and explicit removal condition.
