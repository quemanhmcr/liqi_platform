# ADR 5001: V1 provider integration checkpoint

- Status: Accepted; production evidence pending
- Date: 2026-07-18
- Owner: Senior 5
- Consumers: Senior 1, Senior 2, Senior 3, Senior 4

## Context

The four V1 provider branches have published a committed composite graph. Readiness previously blocked all source checkpoints because the registry referenced initial contract/skeleton commits and unpublished runtime commands.

## Decision

`v1/production-readiness` integrates the committed graph through:

- Senior 1 runtime `15e2dd5a263decb91308a0d1783c4610bd7dc62d`;
- Senior 2 database `168f6b3be66ff36eac4b4944f8d6940b6d2026ce`;
- Senior 3 native `ca71a1be6914a33db22544802f704084f3346af5`, with non-Linux blocked-evidence adapter `e9201d742765f4b1c544e60648e0a719eab91c8e`;
- Senior 4 infrastructure `19b06788e0a5d7695fc2f89102af8e75129d39af`.

The readiness registry marks only commands that are integrated and directly consumable as `available`; provider JSON status remains authoritative and cannot be promoted by process exit code. Integrated artifact/live collectors without exact-release evidence are `pending-live-evidence`. Missing owner commands remain `pending-provider-publication` even when adjacent composite evidence exists.

## Evidence classification

Runtime source and disposable PostgreSQL integration evidence generated on provider/composite SHAs may be used to diagnose and reproduce the gates. Final checkpoint evidence must be regenerated on the exact final readiness SHA. Local Windows release packaging is non-promotable and cannot satisfy Linux ARM64 artifact gates.

The source integration state is:

```text
engineering-complete-evidence-pending
```

The production verdict remains:

```text
V1 NOT READY
```

until artifact, live load/reconnect/resilience, restore, security, cutover, rollback and post-cutover evidence all pass for one exact release.

## Compatibility and rollback

The integration is additive/versioned. PostgreSQL remains the only durable authority. V0 Rust remains the predeclared route-scoped rollback target, and application rollback does not run database down migrations. No dual write or competing transport envelope is introduced by this checkpoint.

## OCI impact

None. This checkpoint performs source integration, local validation and disposable-database evidence only. It does not apply OpenTofu, mutate OCI/IAM/secrets, deploy a release, switch traffic, restore production data or execute live rollback.
