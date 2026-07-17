# Incident severity model V0

| Severity | Definition | Initial response |
|---|---|---|
| SEV0 | Confirmed security/authorization correctness violation, active secret compromise, irreversible broad data loss | Page immediately, appoint incident commander, freeze changes, preserve evidence |
| SEV1 | Critical user path unavailable, durable writes broadly failing, RPO/RTO at risk, rollback/recovery failure | Page, appoint incident commander, activate degraded mode or rollback |
| SEV2 | Partial degradation, tail latency or backlog affecting users, capacity forecast under safety margin | Assign owner, mitigate within bounded window, escalate on worsening impact |
| SEV3 | Non-urgent operational debt, warning before user impact, dashboard/runbook defect | Track and schedule |

Severity is based on impact, correctness and recovery risk, not component prestige. A short CPU spike without user impact or imminent capacity exhaustion is not page-worthy.
