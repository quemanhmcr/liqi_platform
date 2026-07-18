# Contributing to LIQI Platform

## V1 branch and ownership

All V1 branches start from the approved integrated V0 SHA and stay close to `main`:

```text
v1/beam-runtime-realtime   Senior 1
v1/durable-data-work       Senior 2
v1/native-rustler          Senior 3
v1/oci-live-runtime        Senior 4
v1/production-readiness    Senior 5
```

Single-writer ownership is authoritative. Do not modify another provider's internal implementation to make a readiness gate pass. Return the failure with owner, seam, exact action and machine-readable evidence.

## Required Git inspection

Before work:

```bash
git status --short
git branch --show-current
git log --oneline --decorate -20
git worktree list
```

Before consuming another senior's change, review the exact SHA, commit communication footer, public contract, migrations/capacity impact and provider/consumer tests. Do not integrate from a verbal description.

## Change discipline

- Keep each commit bounded to one purpose.
- Separate contract, behavior, refactor and unrelated formatting changes.
- Use additive/versioned contracts or a documented adapter/migration window.
- Never add an unbounded queue, mailbox, retry, pool, supervisor or telemetry buffer.
- Do not create another durable source of truth or a competing transport envelope.
- Fakes are test/development-only, have an owner/removal condition and never become the default production path.
- A fixture or mock cannot satisfy live evidence.
- `blocked` is not `passed`; missing evidence is not success.

## Senior 5 source validation

```bash
python operations/bin/validate_readiness_v1.py
python -m unittest discover -s tests/live -p 'test_*.py' -v
python -m unittest discover -s tests/recovery -p 'test_*.py' -v
python -m unittest discover -s tests/resilience -p 'test_*.py' -v
node --check tests/load/v1-floor.js
node --check tests/load/reconnect-storm-v1.js
python scripts/operations/validate_ci_workflows.py
python scripts/operations/scan_repository_secrets.py
```

Run the full repository suite before integration:

```bash
python -m unittest discover -s tests -p 'test_*.py' -v
```

Build/release/native commands are allowed when resource impact is controlled. OCI resource changes, IAM/real-secret changes, live deployment, live migration, backup/restore mutation, traffic switching and live rollback require explicit approval and the owning executor.

## Provider publication

`operations/readiness/provider-gates-v1.json` is the shared command registry. A provider changes only its owned entry from `pending-provider-publication` to `available` in the same commit that publishes:

1. The exact executable path and command.
2. Directly consumable output at the declared seam.
3. Provider validation.
4. Consumer contract compatibility.
5. Capacity/security/operational impact where relevant.

Do not add a Senior 5 wrapper around missing provider semantics. `run_provider_gates_v1.py` invokes published commands and records missing paths, inputs or approvals as owner-attributed blockers.

## Evidence rules

Production readiness evidence must be:

- Schema-valid and machine-readable.
- Bound to the exact 40-character Git SHA and V1 release ID.
- Fresh within `operations/readiness/evidence-policy-v1.json`.
- Immutable by SHA-256.
- Marked live where required.
- Free of credentials and durable data copies.
- Complete for all seven checkpoints and all required primary evidence kinds.

Security/correctness events—authorization bypass, secret exposure, duplicate durable identity, event before commit and durable event loss—have zero budget.

## Live and OCI approval boundary

Senior 4 is the OCI/traffic mutation executor. A required mutation request must state:

1. Why it is necessary.
2. Exact command.
3. Working directory and required environment variables.
4. What may change.
5. Expected result.
6. Required log/evidence to return.

Do not run a mutation merely to test whether it works. Source validation, read-only inspection and plan validation remain non-mutating.

## Integration communication footer

Use this footer only when a commit changes a shared seam, observable behavior, compatibility/migration, capacity/security requirement or consumer action:

```text
Communication:
Consumers: Senior <n>, Senior <n> | none
Seam: <contract, API, command, file or lifecycle>
Change: <observable change, 1–3 sentences>
Action required: <consumer action or none>
Compatibility: additive | unchanged | versioned | breaking-with-adapter
Validation: <exact command or machine-readable evidence>
Decision note: <ADR/path or none>
OCI impact: none | read-only | plan-only | approved-mutation
```

Internal-only implementation commits do not need the footer.

## Final reporting

Report branch/SHA, completed capabilities, published seams, commits consumers must read, validations, pending evidence, compatibility/rollback, OCI mutation count, risks and clean-worktree status. Use one unambiguous conclusion:

```text
READY FOR INTEGRATION
```

or:

```text
NOT READY FOR INTEGRATION
```

The final V1 platform verdict remains only `V1 PRODUCTION-SHAPED ON OCI` or `V1 NOT READY`.
