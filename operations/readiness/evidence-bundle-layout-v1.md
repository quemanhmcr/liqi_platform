# V1 exact-release evidence bundle

The post-cutover workflow consumes one artifact for exactly one Git SHA and release ID. Artifact assembly is an operator/evidence transfer action, not proof by itself.

```text
v1-readiness-bundle/
├── evidence/
│   ├── capacity.json
│   ├── platform-probe.json
│   ├── load.json
│   ├── reconnect.json
│   ├── recovery.json
│   ├── resilience.json
│   ├── security.json
│   ├── cutover.json
│   └── rollback.json
├── checkpoints/
│   ├── source.json
│   ├── integration.json
│   ├── artifact.json
│   ├── live-staging.json
│   ├── promotion.json
│   ├── cutover.json
│   └── post-cutover.json
├── compatibility.json
└── oci-mutations.json
```

Every JSON document is validated against `operations/readiness/evidence-policy-v1.json`. The composer recalculates SHA-256, checks freshness and rejects a mismatched Git SHA/release ID, a synthetic pass claim, missing checkpoint, unapproved mutation, stale recovery/rollback or non-zero correctness event.

The artifact must not contain credentials, raw session tokens, PEM files, database dumps, backup repository contents or unredacted crash dumps. References point to protected evidence; they do not embed secret material.
