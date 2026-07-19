# ADR 5002: Temporary E5 Adoption and A1 Migration

## Status

Accepted for source preparation on 2026-07-19. Live apply remains evidence- and approval-gated.

## Context

The required V1 runtime envelope remains 4 OCPU and 24 GiB. `VM.Standard.A1.Flex` capacity is temporarily unavailable in `ap-singapore-2`, while a reviewed Oracle Linux 9 `VM.Standard.E5.Flex` host with the same CPU and memory envelope is being prepared by the infrastructure lead.

The E5 host is x86_64 and paid. It must not be represented as A1/aarch64, and existing manually-created OCI resources must not be duplicated by a later OpenTofu plan.

## Decision

- Preserve `a1-target` as the default profile and final migration target.
- Add `e5-temporary` as an explicit, paid, x86_64 bridge with a 200 GiB boot volume and preserved 130 GiB data volume.
- Require an approval reference, cost/capacity acknowledgement, independent management-plane evidence, PostgreSQL state-backend TLS/lock/backup/restore evidence, reviewed host signing trust, and an RFC3339 expiry no more than 90 days from plan time.
- Keep approved apply blocked for `a1-target` in this source revision. The future A1 move requires a separately reviewed plan/source revision and architecture-matched artifacts.
- Parameterize OCI display names so existing `liqi-live` resources can be imported without replacement-by-rename.
- Adoption plans may create missing resources and perform bounded in-place updates, but must reject delete or replacement actions.
- Import mutates only encrypted OpenTofu state. It never substitutes for a reviewed post-import saved plan.
- Runtime, NIF, host package and release artifacts must match `x86_64-unknown-linux-gnu` on E5. ARM64 artifacts remain required for the later A1 migration.

## Consequences

The temporary host incurs paid compute/storage charges and has an enforced expiry. A1 migration is a host/artifact replacement with preserved data volume and independent recovery authority; no database down migration or durable dual write is introduced.
