# V1 provider integration contract

Senior 5 consumes provider commands from `operations/readiness/provider-gates-v1.json`. The registry is descriptive and fail-closed; it does not implement Phoenix, PostgreSQL, native or OCI behavior.

## Provider states

- `pending-provider-publication`: the owner command/output does not exist; `provider_commit` is null.
- `pending-integration`: the command exists at a provider commit but is absent from the integrated graph.
- `available`: the exact command and required paths are integrated and invokable. Required inputs, approvals and platform constraints may still produce blocked evidence.
- `pending-live-evidence`: the collector/control exists, but the exact-release live result is absent.

A provider JSON result is authoritative after schema/SHA/release validation. `status: blocked` or `status: failed` is never promoted to passed, even when the process exits zero; a non-zero process with valid blocked evidence remains blocked.

## Integrated provenance

```text
Senior 1 runtime:        15e2dd5a263decb91308a0d1783c4610bd7dc62d
Senior 2 database:       168f6b3be66ff36eac4b4944f8d6940b6d2026ce
Senior 3 native:         ca71a1be6914a33db22544802f704084f3346af5
Senior 4 infrastructure: ca99b7d14816cd051fce15a54accdeb17276096d
Native Windows adapter:  e9201d742765f4b1c544e60648e0a719eab91c8e
```

The source, disposable runtime/database integration, native safety and release/native artifact verifier commands are available. Database recovery remains `pending-provider-publication`. Runtime live probe and infrastructure plan/host/rollback evidence remain exact-release live blockers.

## Evidence and mutation rule

Final evidence must bind the exact integrated Git SHA and one release ID. Live evidence must identify live execution; examples, fixtures, synthetic results and local non-promotable packages are test-only.

Source validation, local builds, disposable databases and read-only plan inspection do not authorize OCI mutation. OCI apply, IAM/secret changes, live migration, deployment, traffic switching, restore and rollback execution require explicit approval and the owning executor.

The unresolved seams and removal conditions are in `operations/readiness/blocked-provider-seams-v1.md`.
