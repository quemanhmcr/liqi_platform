# ADR 0405: CI and provider promotion policy

## Status

Accepted for V0.

## Decision

Pull requests run deterministic Senior 4 source controls and invoke every published provider source command directly. Missing provider branches are represented as owner-attributed `blocked` results during the checkpoint grace period; no mock is substituted.

Provider integration and promotion use a separate manual workflow. It is strict, carries no OCI long-lived credentials, performs no OCI/host deployment, and rejects blocked or missing seams. Disposable database mutation requires an explicit workflow input and remains owned by Senior 2.

GitHub Actions are pinned to executable 40-character commit SHAs, checkout credentials are not persisted and permissions are least privilege per job.

## Consequences

- A provider branch can be reviewed independently without Senior 4 becoming an integration sink.
- Promotion remains unavailable until Senior 1/2/3 publish every required signal.
- Build and artifact production remain owner-triggered and cannot be confused with source validation.
