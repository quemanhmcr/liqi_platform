# LIQI Platform

Self-owned backend platform for LIQI, deployed on Oracle Cloud Infrastructure.

This repository owns LIQI domain services, API and realtime contracts, PostgreSQL schema, workers, infrastructure-as-code, security controls, observability, release operations, and disaster-recovery procedures.

## Repository boundary

- `liqi_match`: mobile client and client-side adapters.
- `liqi_platform`: backend platform and OCI operations.
- OCI API signing keys and CLI configuration must remain in the user's `~/.oci` directory and must never be committed.

## Initial runtime shape

- Rust HTTP API
- Rust realtime gateway
- Rust background worker
- PostgreSQL authority
- Transactional outbox
- OCI Object Storage and Vault

No build or deployment has been performed yet.
