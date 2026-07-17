# ADR 0406: Promotion evidence and deterministic deployment specification

## Status

Accepted for V0.

## Decision

Promotion composes four independent evidence sources into one `integration-result-v0`:

1. direct provider command results,
2. aggregate capacity budget,
3. backup/WAL/restore freshness,
4. Senior 3 platform probe.

Mock mode, blocked providers, mismatched release IDs, stale recovery evidence, failed probe steps or over-capacity declarations fail the composed result. Senior 4 does not translate missing provider semantics.

A deployment specification is generated only from a passed provider-mode integration result, matching release manifest, matching host output and matching health target. It is deterministic and non-executing. The spec explicitly records that V0 is not HA, zero downtime or canary and that activation is never automatic.

Free-trial-only, paid and unknown cost classifications require an approval reference. Application rollback never runs a database down migration. A first release without a previous artifact has an explicit stop-and-incident failure path rather than a fictitious rollback target.

## Provider action required

- Senior 1: publish host output and read-only plan/cost evidence matching the release environment.
- Senior 2: publish `recovery-status-v0` and provider-owned restore verification; do not place the restore engine under `operations/**`.
- Senior 3: implement `platform-probe-result-v0`, including durable command, terminal outbox effect and observed release ID.
