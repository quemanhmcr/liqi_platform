# V1 provider integration contract

Senior 5 consumes provider commands from `operations/readiness/provider-gates-v1.json`. The registry is descriptive and fail-closed; it does not implement provider behavior.

## Provider states

- `pending-provider-publication`: the owned consumer-ready command or output does not exist. `provider_commit` must be null.
- `pending-integration`: the command exists at the recorded provider branch and exact 40-character commit, but is not yet present on the Senior 5/integrated branch.
- `available`: the exact command and required paths are present on the integrated SHA and have passed provider/consumer validation. The original provider commit remains recorded for review provenance.
- `pending-live-evidence`: the integrated collector exists, but the exact-release live result is still absent.

None of the pending states can produce a passed checkpoint.

## Publication and integration rule

A provider moves a seam through these states without skipping evidence:

```text
pending-provider-publication
→ pending-integration
→ available
→ pending-live-evidence (only for a live collector awaiting an owner-run result)
→ passed checkpoint evidence
```

A provider commit is recorded as `pending-integration` only after Senior 5 has read its communication note, reviewed the exact diff and confirmed the advertised command exists at that SHA. The entry becomes `available` only after that commit is integrated, the required paths are present on the resulting repository SHA and the command passes there.

Provider output must be bound to the exact Git SHA and release ID whenever the result schema carries those fields. Live evidence must identify `evidence_mode: live`; examples, fixtures and synthetic evidence are test-only.

## Current exact provider commits

The current checkpoint and unresolved lifecycle gaps are listed in `operations/readiness/blocked-provider-seams-v1.md`. Recording an exact commit is not an instruction to cherry-pick blindly; the integration owner still reviews shared-file conflicts, compatibility, migrations, capacity and the commit communication footer.

## Owner actions

- Senior 1: retain exact-SHA source/disposable integration evidence; verify the signed ARM64 Mix release when Senior 4 supplies it; run `live-platform-probe-v1` only after an approved deployment.
- Senior 2: retain the integrated source/database runner and publish an approved isolated `recovery-result-v1` collector and result.
- Senior 3: retain the integrated source/artifact verifier and publish one full safety result with Linux ARM64 Rustler, property/fuzz/panic/fallback and direct A1 scheduler/latency evidence.
- Senior 4: publish the signed ARM64 release inputs plus plan, host-readiness and `rollback-result-v1` collectors. OCI and traffic mutation remain Senior 4-only and require explicit approval.
- Senior 5: consume only `available` commands, preserve `pending-live-evidence`, and never promote examples or local packaging into production evidence.

## Blocked seam format

```text
Blocked seam:
Provider: Senior <n>
Consumer: Senior 5
Missing contract: <exact command/output/path>
Why current work cannot safely continue: <semantic or evidence gap>
Minimal provider output required: <directly consumable result>
Temporary work that remains independent: readiness schemas, validators, load/resilience harness and runbooks
```

No readiness-owned provider wrapper, permissive fallback or fixture is accepted as production evidence.
