# OCI V0 Host Destroy and Recreate Runbook

## Scope

This runbook covers the provider-owned OCI host lifecycle. It does not initialize PostgreSQL, restore database data, deploy Rust services, update DNS, or promote a release; the corresponding consumers must complete their checks at the stated gates.

No command in this runbook is approval to mutate OCI. `tofu apply`, `tofu destroy`, replacement, firewall changes, and state migration require explicit project-owner approval.

## Preconditions for any plan

1. Worktree is clean and based on the approved module commit.
2. `python infrastructure/validation/validate_infrastructure.py --with-tofu` succeeds.
3. OCI CLI authentication is valid.
4. Inputs contain only tenancy/resource identifiers and an SSH **public** key.
5. The pinned Oracle Linux image supports AArch64 and `VM.Standard.A1.Flex`.
6. Cost classification and acknowledgement are reviewed.
7. No second operator is running OpenTofu for the same environment.
8. Durable state handling has been approved; local validation state is not sufficient for apply.

## Read-only plan

From `infrastructure/opentofu/environments/development`:

```bash
tofu init -backend=false -input=false
tofu fmt -check -recursive ../..
tofu validate

tofu plan \
  -refresh=false \
  -input=false \
  -lock=false \
  -var='acknowledge_non_always_free_profile=true' \
  -out=/secure-temporary-location/development.tfplan

tofu show -json /secure-temporary-location/development.tfplan \
  > /secure-temporary-location/development.plan.json

python ../../../../infrastructure/validation/validate_oci_plan.py \
  /secure-temporary-location/development.plan.json
```

Expected default clean-tenancy result:

- 27 create actions, 0 change, 0 destroy.
- One `VM.Standard.A1.Flex` instance at 4 OCPUs/24 GB.
- One 50 GB boot volume and one separate 100 GB data volume.
- Public TCP ingress only 80 and 443.
- No SSH rule when `enable_admin_ssh=false`.
- One private backup bucket and one exact-instance dynamic group.

Delete temporary plan files after review. A saved plan may contain infrastructure identifiers and public-key material even though it must not contain secrets.

## First creation gate

Before the first apply, record explicit owner approval for:

- Potential `free-trial-only` compute cost.
- State backend/single-writer procedure.
- Public edge IP and 80/443 exposure.
- Optional SSH source CIDRs, if enabled.
- Pinned image OCID.
- Stateful volume and backup-bucket retention.

The owner, not Senior 1, executes the approved apply command.

## Verify a created host

After an approved apply:

1. Wait for cloud-init completion and the volume attachment.
2. Read `/run/liqi/host-ready.json` through the approved administrative path.
3. Verify schema `liqi.platform.host-readiness/v0`, status `ready`, and output/bootstrap versions.
4. Verify `systemctl status liqi-data-volume.service liqi-host-readiness.service`.
5. Verify `/var/lib/liqi` is mounted from the expected UUID and not from the boot volume.
6. Verify no listener exists on database, PgBouncer, Rust, OTLP, metrics, or admin ports until its owning consumer deploys it.
7. Verify instance-principal bucket access with bounded retries; verify object deletion is denied.
8. Hand `tofu output -json oci_host_v0` directly to Senior 2, 3, and 4 tooling.

Do not repair a failed bootstrap manually. Capture cloud-init/systemd logs, fix source, validate, and replace the host.

## Planned host replacement

Consumer gates before replacement:

- Senior 2 confirms a restorable backup and clean PostgreSQL shutdown/fencing plan.
- Senior 3 confirms application drain and compatible configuration.
- Senior 4 confirms release artifact availability, edge/DNS update, and rollback version.
- Owner approves the replacement and any cost impact.

Read-only replacement plan:

```bash
tofu plan \
  -refresh=true \
  -input=false \
  -replace=module.secure_host.oci_core_instance.host \
  -out=/secure-temporary-location/host-replacement.tfplan
```

Review that:

- The data volume and backup bucket are not destroyed.
- The volume attachment, exact-instance dynamic group rule, host ID, and public IP change as expected.
- No new public port or paid/unknown resource appears.
- The image and bootstrap versions are intentional.

After owner-executed apply, repeat the created-host verification and wait for IAM propagation before declaring backup/secret consumers ready.

## Rollback

Infrastructure rollback is source rollback:

1. Select the last reviewed module/bootstrap version compatible with the preserved data volume.
2. Produce a new replacement plan.
3. Confirm no stateful resource destruction.
4. Obtain owner approval.
5. Owner executes apply; consumers validate database restore/startup and release compatibility.

Never repair drift in OCI Console and then treat it as authoritative. If emergency console action is unavoidable, record it as an incident, import/reconcile it into OpenTofu immediately, and restore source authority.

## Full environment destruction

Default source intentionally blocks deletion of the data volume and backup bucket with `prevent_destroy`. A normal `tofu destroy` must fail rather than erase stateful data.

Full destruction requires all of the following:

1. Written owner acknowledgement naming the environment and stateful resource IDs.
2. Senior 2 evidence that required backups restore successfully elsewhere.
3. Retention/export decision for Object Storage data.
4. Revocation of runtime access and application write freeze.
5. A reviewed source commit that explicitly removes stateful `prevent_destroy` protection; do not use ad-hoc state surgery as a shortcut.
6. A destroy plan showing every resource and cost consequence.
7. Owner-executed destroy.
8. Verification that no volume, object, public IP, policy, or compartment residue remains.
9. Preservation of required audit evidence and final state snapshot according to the approved retention policy.

## Failure handling

| Failure | Action |
|---|---|
| A1 capacity unavailable | Do not switch shape automatically. Record capacity failure and obtain owner decision on retry, region, or PAYG profile. |
| Cloud-init/readiness fails | Capture logs through approved access, fix source, and replace. Do not hand-edit the host. |
| Data device missing | Keep services stopped; inspect OCI attachment plan/state. Never format an alternate device. |
| Existing filesystem is not XFS | Fail closed and escalate to recovery; do not reformat. |
| IAM instance principal not active | Retry with bounded backoff; do not copy user OCI credentials onto the host. |
| Backup object create fails | Database backup must fail visibly; local staging is not success. |
| Public IP changes | Senior 4 updates edge/DNS only after readiness and service checks. |
| State cannot be persisted | Stop all mutation, preserve emergency state, and follow state-recovery procedure before another plan/apply. |
