# Runbook: api slo burn V0

Owner and signal source are defined in `operations/slo/slo-catalog-v0.json`.

1. Confirm the alert signal, release ID, environment and missing-data state.
2. Identify the first failing provider seam; do not compensate with integration glue.
3. Apply the catalogued degraded mode with bounded concurrency, timeout and retry behavior.
4. Roll back only when the current release is causal and a retained compatible target passes preflight.
5. Preserve integration, health and recovery evidence; escalate severity on correctness or recovery risk.
6. Reconcile emergency manual action back into source after the incident.
