# V1 first-release private blue-green rollout

This runbook is the production migration boundary for correcting a retained
public-IP-capable host subnet without replacing or editing its immutable OCI
subnet property. It does not provide a V0 application rollback path.

## Invariants

- Retain the legacy subnet, primary and stopped fallback. Their OpenTofu
  resources use `prevent_destroy`; neither legacy instance is an NLB target.
- Create the V1 primary and recovery fallback in the existing subnet whose
  `prohibit_public_ip_on_vnic` value is `true`. Both primary VNICs are created
  with `assign_public_ip = false`.
- Attach the V1 data volume and the NLB backends only to the new private
  primary. Bind the instance-principal dynamic group only to the new private
  primary and fallback.
- Keep both NLB backends offline until private runtime, monitoring, backup and
  first-release recovery evidence pass.
- Use a new encrypted state backup, readiness result and saved plan for every
  mutation stage. Apply exactly that saved plan; never re-plan during apply.
- Any source change invalidates all exact-SHA artifacts and evidence.

## Mutation stages

| Stage | `fallback_desired_state` | `public_backend_enabled` | Expected OCI mutation |
| --- | --- | --- | --- |
| Foundation | `RUNNING` | `false` | Adopt the existing private route/subnet; create the private primary, fallback and remaining reviewed resources; create NLB backends offline. |
| Recovery-ready | `STOPPED` | `false` | Stop only the new fallback after bootstrap and private verification. |
| Public cutover | `STOPPED` | `true` | With `acknowledge_public_cutover = true`, update only both NLB backends from offline to online. |

For each stage, reject the plan if it contains delete or replacement actions,
touches the retained fallback, replaces Vault/secrets, adds a host public IP,
or changes any target away from the new private primary.

## Foundation verification

Before the first saved apply, require exact-SHA source validation, signed Linux
release and host bundle, executed recovery evidence, idempotent adoption and all
seven readiness checks. The recovery evidence may describe the retained
private-addressed fallback while public traffic is still off; it is sufficient
only for creating the new private foundation.

After the foundation apply, deploy the signed release and verify loopback and
private-IP application health, database migration readiness, service state,
Run Command access, secret retrieval without disclosure, metrics, alerts and
backup timers. Do not enable public traffic.

## Recovery-ready verification

Generate and apply a second saved plan that changes the new fallback to
`STOPPED` while both NLB backends remain offline. Run the first-release recovery
exercise against the new private primary/fallback. Evidence must prove:

- primary and fallback VNICs have no public IP and belong to subnets that
  prohibit public IP assignment;
- the fallback started, reached `RUNNING`, was stopped in `finally`, and ended
  `STOPPED`;
- the reusable or newly created boot-volume backup is `FULL`, `AVAILABLE`,
  purpose-tagged and younger than 24 hours;
- public traffic stayed off throughout the exercise.

If the exercise fails, independently verify the fallback is `STOPPED` and keep
the NLB backends offline.

## Public cutover

Regenerate readiness using the new recovery evidence and cutover tfvars. The
cutover guard requires both explicit acknowledgement and a stopped fallback.
The saved plan must contain no delete/replacement and only the reviewed backend
online updates (plus explicitly explained harmless drift, otherwise reject it).

After apply, verify NLB backend health, TCP 80/443, external HTTP behavior, TLS
chain and hostname, application health, WebSocket/realtime, monitoring, alert
delivery and backup freshness. Confirm both fallbacks remain stopped/private,
the new primary remains private, and direct SSH ingress remains limited to the
two exact Bastion `/32` sources. Only then may the release be declared LIVE.

The retained legacy primary may be stopped later under a separate reviewed
operation after cutover stability is established. Cleanup or termination is a
separate owner-approved change and is intentionally outside this rollout.
