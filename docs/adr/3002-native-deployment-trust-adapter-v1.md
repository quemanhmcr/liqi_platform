# ADR 3002: Native artifact deployment trust adapter V1

## Status

Accepted for Senior 3 provider publication.

## Context

Senior 3's `native-artifact-v1` binds the ARM64 NIF to its exact source revision, SPDX SBOM, SLSA-compatible provenance and a keyless Sigstore bundle. Senior 4's host staging contract independently requires an Ed25519 signature trusted from the protected host key directory. The two contracts originally could not be consumed directly even though both referred to the same shared object.

Replacing either mechanism would weaken an existing boundary. Sigstore proves CI/OIDC provenance; the offline Ed25519 key is the host install authorization. Creating a second artifact or rewriting its checksum would create duplicate artifact identity.

## Decision

`native/scripts/prepare_deployment_manifest.py` is the provider-owned adapter.

It:

1. validates and executes the Senior 3 Sigstore/SBOM/provenance verifier;
2. resolves the exact artifact bytes and checks their SHA-256 and size against the provider manifest;
3. verifies a Senior 4 supplied Ed25519 signature over those same bytes using `<trust-dir>/<key-id>.pem`;
4. emits `liqi.deployment.native-artifact/v1` without copying, signing or modifying the artifact;
5. installs only at `lib/liqi_native-1.0.0/priv/native/libliqi_sequence_diff_nif.so`;
6. uses `bin/liqi_platform eval`, not distributed Erlang RPC, for the load probe;
7. preserves the pure Elixir fallback and `LIQI_NATIVE_MODE` feature control.

The Ed25519 private key remains outside Senior 3 ownership. Senior 4 or an approved signing job supplies the signature and public-key identity. The adapter logs no key material or payload.

## Compatibility

The change is additive. Both manifests share one source revision and artifact SHA-256. Existing Sigstore verification remains mandatory, and Senior 4's staging verifier remains unchanged.

## Migration and rollback

Senior 4 references the generated deployment manifest from the Mix release manifest. Rollback selects the retained prior manifest/artifact pair and does not require a database migration. The adapter may be removed only if a future versioned deployment contract natively accepts the provider Sigstore manifest while preserving offline host authorization.
