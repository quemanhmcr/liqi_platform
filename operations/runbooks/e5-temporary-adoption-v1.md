# Temporary E5 adoption and exact apply

This runbook adopts the infrastructure lead's reviewed OCI resources into the independent encrypted PostgreSQL OpenTofu state. It does not recreate those resources and it does not represent the x86_64 E5 host as A1/aarch64.

## Ownership boundary

The infrastructure lead owns creation and completion of the temporary E5 host and supporting OCI resources. The deployment operator only performs read-only discovery, imports compatible identities into OpenTofu state after approval, reviews a no-delete/no-replacement saved plan, and applies that exact plan after a separate approval.

The technically accepted workload NSG is adopted into the source graph with exactly two OCI Bastion SSH `/32` rules. The stopped fallback instance remains outside the single-primary graph as an explicitly retained rollback/cost resource.

## Accepted network topology

The temporary E5 primary has no public IP. Its existing `10.42.10.0/24` subnet is source-managed as a private host subnet with default egress through NAT Gateway and Oracle Services through Service Gateway. A separate `10.42.30.0/24` public edge subnet and public OCI Network Load Balancer terminate only Layer-4 reachability; TCP 80/443 pass through to Caddy on the private host. The NLB uses full NAT, fail-closed backend sets and a 3600-second TCP idle timeout for WebSocket longevity. SSH is never public: only the two technically accepted OCI Bastion private `/32` addresses may reach TCP/22, with OCI Run Command as the secondary management path.

Enabling the Internet Gateway is safe only together with the separated public edge route table; the private host route table must remain NAT/Service-Gateway based.

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

1. Run `discover-e5-adoption`. It performs OCI list/get calls only and verifies the private E5 primary, NAT default route, Oracle Services Service Gateway route, empty Security List, exact Bastion `/32` rules and separated public-NLB graph. It writes an `adoption-manifest-v1` file with compatible imports, missing source-managed resources, unmanaged transition resources and blockers.
2. Review the manifest. A blocked manifest cannot be consumed.
3. Run `validate-e5-state-adoption` without `--execute`. This validates exact SHA and inputs without changing state.
4. Run `execute-e5-state-adoption` with an explicit approval. This mutates encrypted OpenTofu state only; it does not create, update or delete OCI resources.
5. Run `pre-apply-readiness` with the handoff manifest, state evidence/result, protected E5 tfvars, cryptographically verified x86_64 publication, trust directories and exact first-release recovery evidence for the stopped private fallback and AVAILABLE full predeploy boot-volume backup. It can be run early to enumerate blockers, but status must be `passed` before planning. It performs no state or OCI mutation and never emits protected values.
6. Run `read-only-live-plan` in `e5-temporary` and `adopt-existing` mode. The plan validator allows create/no-op/in-place update but rejects delete, replacement, unknown resource counts, public SSH, architecture mismatch and secret material.
7. Review the schema-validated saved-plan result, JSON plan, validation result, adoption/readiness/release/rollback digests, expected cost and E5 expiry.
8. Run `approved-oci-apply` only with the matching approval and the same `pre-apply-readiness-v1` file used for planning. The wrapper revalidates its exact SHA and every state/adoption/tfvars/release/rollback digest, applies the exact saved plan, cross-binds the OCI output Git/profile/approval/plan identity, validates the apply-result schema and refuses re-planning.

## A1 migration

A1 remains the target profile. Migration requires an A1 capacity event, a separately reviewed source/plan, an `aarch64-unknown-linux-gnu` release and NIF, preserved data/recovery authority and a health-gated host switch. No database down migration or durable dual write is permitted.

- The signed release output includes a schema-validated portable `*.linux-release-build-result-v1.json` in the same atomic publication directory.
