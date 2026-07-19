# Temporary E5 adoption and exact apply

This runbook adopts the infrastructure lead's reviewed OCI resources into the independent encrypted PostgreSQL OpenTofu state. It does not recreate those resources and it does not represent the x86_64 E5 host as A1/aarch64.

## Ownership boundary

The infrastructure lead owns creation and completion of the temporary E5 host and supporting OCI resources. The deployment operator only performs read-only discovery, imports compatible identities into OpenTofu state after approval, reviews a no-delete/no-replacement saved plan, and applies that exact plan after a separate approval.

The existing transition SSH NSG and stopped fallback instance remain outside the source-managed production graph unless a later reviewed change explicitly adopts or removes them.

## Required protected inputs

- Exact clean Git SHA.
- Independent PostgreSQL state-backend evidence for TLS verify-full, advisory locking, encrypted backup and isolated restore.
- Protected live tfvars with E5 expiry, cost acknowledgement, management-plane evidence and reviewed host-bundle public key.
- OCI authentication scoped to `ap-singapore-2`.
- Explicit approval references for state adoption and OCI apply.

Never commit live OCIDs, private keys, database URLs, token values or approval secrets.

## Release artifact preparation

Build native and Mix artifacts on an independent clean Linux x86_64 builder, never on the OCI application host. The canonical order is:

1. `native/scripts/build-x86_64-artifact.sh` on exact SHA.
2. Sigstore sign/package/verify the NIF with `--target-triple x86_64-unknown-linux-gnu`.
3. Produce and verify the offline Ed25519 native deployment handoff.
4. Run `beam/scripts/build_linux_release.py` with distinct protected archive and manifest signing key identities.
5. Retain the complete output directory, including native SBOM/provenance/signatures and `runtime-artifact-result-v1`.

The builder uses a temporary `git archive`, injects only the verified NIF, creates a deterministic no-symlink release archive, self-verifies the signatures and ERTS architecture, and atomically publishes output only after every check passes.

## Sequence

1. Run `discover-e5-adoption`. It performs OCI list/get calls only and writes an `adoption-manifest-v1` file with compatible imports, missing source-managed resources, unmanaged transition resources and blockers.
2. Review the manifest. A blocked manifest cannot be consumed.
3. Run `validate-e5-state-adoption` without `--execute`. This validates exact SHA and inputs without changing state.
4. Run `execute-e5-state-adoption` with an explicit approval. This mutates encrypted OpenTofu state only; it does not create, update or delete OCI resources.
5. Run `read-only-live-plan` in `e5-temporary` and `adopt-existing` mode. The plan validator allows create/no-op/in-place update but rejects delete, replacement, unknown resource counts, public SSH, architecture mismatch and secret material.
6. Review the saved plan, JSON plan, validation result, adoption evidence digest, expected cost and E5 expiry.
7. Run `approved-oci-apply` only with the matching approval. The wrapper applies the exact saved plan and refuses re-planning.

## A1 migration

A1 remains the target profile. Migration requires an A1 capacity event, a separately reviewed source/plan, an `aarch64-unknown-linux-gnu` release and NIF, preserved data/recovery authority and a health-gated host switch. No database down migration or durable dual write is permitted.
