# V1 first-release retained-compute rollout

This runbook is the production boundary for completing the first V1 release
without deleting working compute to make quota available. The tenancy rejects
additional Core Instance creation, so the reviewed existing primary and stopped
fallback remain the compute authority. This is not a V0 application rollback.

## Invariants

- Retain the existing primary and stopped fallback. OpenTofu protects the
  primary with `prevent_destroy`; the fallback stays outside OpenTofu mutation
  scope and is identified only through protected input.
- Neither VNIC has a public IP. The historical host subnet permits public-IP
  assignment at subnet level, so the stronger compensating controls are
  mandatory: `assign_public_ip = false`, no Security List ingress, exact
  workload NSG membership, and SSH from only the two OCI Bastion `/32` sources.
- Keep the adopted `10.42.20.0/24` private subnet intact for later capacity. Do
  not move or replace the existing primary VNIC during this first release.
- Attach the V1 data volume and NLB backends only to the retained primary. Bind
  the instance-principal dynamic group to the retained primary and stopped
  fallback only.
- Keep both NLB backends offline until private runtime, monitoring, backup and
  exact-SHA recovery evidence pass.
- Use a new encrypted state backup, readiness result and saved plan for every
  mutation stage. Apply exactly that reviewed saved plan; never re-plan during
  apply.
- Any source change invalidates all exact-SHA artifacts and evidence.

## Mutation stages

| Stage | Retained fallback | `public_backend_enabled` | Expected OCI mutation |
| --- | --- | --- | --- |
| Foundation | `STOPPED` | `false` | Reconcile partial infrastructure, attach the data volume, create IAM and NLB listeners/backends offline; create no compute. |
| Public cutover | `STOPPED` | `true` | With `acknowledge_public_cutover = true`, update only both NLB backends from offline to online. |

For each stage, reject the plan if it contains delete or replacement actions,
manages or terminates the retained fallback, replaces Vault/secrets, adds a host
public IP, changes the backend target away from the retained primary, or uses a
listener idle timeout outside OCI's supported range. The production value is
exactly 1800 seconds.

## Foundation verification

Before the foundation saved apply, require exact-SHA source validation, signed
Linux release and host bundle, executed recovery evidence, idempotent adoption
and all seven readiness checks. Recovery evidence must prove the retained
fallback ends `STOPPED`, both instances have no public IP, the full backup is
fresh and public traffic remains off.

After foundation apply, deploy the signed release and verify loopback and
private-IP application health, database migration readiness, service state, Run
Command access, Vault retrieval without disclosure, metrics, alert delivery and
backup timers. Do not enable public traffic.

## Public cutover

Regenerate readiness using fresh recovery and runtime evidence plus cutover
tfvars. The cutover guard requires explicit acknowledgement and the retained
fallback marker `STOPPED`. The saved plan must contain no delete/replacement and
only the reviewed backend online updates, plus explicitly explained harmless
drift; otherwise reject it.

After apply, verify NLB backend health, TCP 80/443, external HTTP behavior, TLS
chain and hostname, application health, WebSocket/realtime, monitoring, alert
delivery and backup freshness. Confirm the fallback remains stopped/private,
the primary remains without a public IP, and direct SSH remains limited to the
two exact Bastion `/32` sources. Only then may the release be declared LIVE.

Stopping or terminating either retained instance is a separate owner-approved
change and is intentionally outside this rollout.
