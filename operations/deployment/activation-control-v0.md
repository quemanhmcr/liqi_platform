# Activation control V0

`activate_release.py` is the host-side implementation of health-gated replacement.

Default invocation is dry-run and performs no mutation. Execution requires all of:

- exact SHA-256 of the reviewed deployment specification;
- Senior 1 host-readiness evidence for output `0.3.0` and bootstrap `0.3.0`, including runtime-unit, capacity-control, and fail-closed-edge checks;
- Senior 2 database-readiness evidence for the required migration version;
- staged artifacts whose size and digest match the manifest;
- owner approval reference;
- root execution on the target POSIX host;
- all Senior 3 provider-owned systemd units loaded from `/etc/systemd/system`, with config and per-service secret files readable by the dedicated non-root identities;
- health target digest matching the deployment specification.

The script never runs `tofu apply`, changes a firewall, runs a migration, rolls a database backward, or invents missing provider output.

On activation health failure it stops the new services. A predeclared retained application release is selected and health-checked with the previous release ID. If no rollback exists or rollback health fails, the result is `incident` and services remain under operator control.

## Owner-run commands

Dry-run:

```bash
python scripts/release/activate_release.py \
  --spec /secure/release/deployment-spec.json \
  --expected-spec-sha256 <reviewed-sha256> \
  --host-readiness /run/liqi/host-ready.json \
  --database-readiness /secure/evidence/database-readiness.json \
  --staged-root /var/tmp/liqi/releases/<release-id> \
  --health-target /secure/release/health-target.json \
  --state-dir /var/lib/liqi/operations/releases \
  --output /secure/evidence/activation-dry-run.json
```

Execution adds:

```text
--execute --approval-ref <owner-approved-reference>
```

Expected successful status is `active`. Exit `3` means the new release failed but application rollback passed. Exit `4` is an incident requiring the activation-failure runbook.

The bootstrap starts only the fail-closed NGINX default. Public application routing follows `infrastructure/runbooks/edge-activation-v0.md` and is not part of release activation unless DNS/TLS approval and a reviewed edge file already exist.
