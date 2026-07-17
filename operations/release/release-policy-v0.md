# Release policy V0

## Artifact identity

A release is the tuple of full Git SHA, source timestamp, three artifact digests, contract versions, database migration range, infrastructure plan digest and runtime configuration digest. Re-running the generator with the same tuple must produce byte-identical JSON.

The release ID is derived as `<prefix>-<first 12 chars of full Git SHA>`. The full SHA remains authoritative.

## Supply chain

Every runnable artifact requires:

- SHA-256 digest in the release manifest.
- SPDX JSON SBOM reference.
- SLSA/in-toto provenance reference.
- Verification before staging promotion.
- Retention with the previous rollback-compatible release.

Attestation establishes source/build provenance; it does not assert that an artifact is vulnerability-free.

## Cost and mutation

- `paid` or `unknown` infrastructure cannot activate without explicit owner approval.
- CI source checks and plans are read-only by default.
- OCI apply and production activation are manual, protected actions.
- No production workflow stores a long-lived OCI private key by design; workload identity/scoped short-lived credentials are the target integration.

## Database compatibility

Deployment preflight accepts forward-compatible expand or expand-migrate states. Application rollback never performs database down migration. Contract-stage cleanup requires a separately approved release after old consumers are outside the compatibility window.
