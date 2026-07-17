# Contributing to LIQI Platform

## Branch and ownership

All V0 workstreams branch from baseline `2d72ce4` and stay within their DRI boundaries. Senior 4 is single writer for `.github/**`, `operations/**`, `scripts/**`, `README.md`, `CONTRIBUTING.md`, `.gitignore`, `contracts/operations/**` and ADR range `0400–0499`.

Do not modify provider internals to make an integration gate pass. Return failures with owner, seam, action required and machine-readable evidence.

## Change discipline

- Keep commits bounded to one purpose.
- Use forward-compatible contract changes or a versioned migration path.
- Never commit credentials, PEM files, tokens, database passwords or signing keys.
- Do not run or enable OCI mutations, host deployment or destructive database migration without explicit project-owner approval.
- Build/prebuild commands are prepared for the project owner and are not run by Senior 4 without permission.
- Every resource, queue, retry, timeout, pool and retention policy is bounded.

## Required validation for Senior 4 changes

```bash
python scripts/operations/validate_contracts.py
python scripts/operations/validate_provider_registry.py --allow-pending
python scripts/operations/validate_dependency_policy.py
python scripts/operations/validate_operability_catalog.py
python scripts/operations/validate_ci_workflows.py
python scripts/operations/scan_repository_secrets.py
python -m unittest discover -s tests -p 'test_*.py' -v
```

Source CI may report a provider gate as `blocked` while its branch is unmerged. The manual provider integration/promotion workflow is strict: blocked, missing or mock seams fail.

## Integration commit footer

```text
Communication:
Consumers: Senior <n>, ...
Seam: <file, endpoint, output or lifecycle>
Change: <what changed>
Action required: <consumer action or none>
Compatibility: <additive, versioned, breaking-with-adapter>
Validation: <exact command>
Decision note: <ADR path if applicable>
```

Breaking changes additionally require old/new provider tests during the migration window, consumer updates, removal condition and owner.
