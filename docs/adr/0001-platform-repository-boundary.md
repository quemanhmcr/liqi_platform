# ADR 0001: Keep LIQI Platform separate from LIQI Match

## Status

Accepted.

## Decision

The self-owned backend platform is maintained in the sibling repository `liqi_platform`. Mobile application code remains in `liqi_match`.

Secrets, OCI CLI credentials, private keys, Terraform/OpenTofu state, and runtime data are not stored in either repository.
