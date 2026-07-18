# ADR 4005: Direct runtime, database and native provider integration

- Status: Accepted; source integration complete, artifact/live evidence pending
- Date: 2026-07-18
- Owner: Senior 4
- Consumers: Senior 1, Senior 2, Senior 3, Senior 5
- Integrated provider/readiness graph: `276dc9de16f507b784c85cdf8421b11471e4ccf1`

## Context

The first Senior 4 deployment draft predated committed V1 provider seams. The final provider graph proves:

- Phoenix listens on loopback port 4100;
- the socket endpoint is `/platform/v1/socket/websocket`;
- runtime secrets use `systemd-credential://...`;
- database authority requires migration 8 and a three-role URL bundle;
- native artifacts use Senior 3 Sigstore, SBOM and provenance verification;
- Senior 1 already signs the exact Mix manifest and release archive.

Infrastructure must not rewrite those contracts or create a second artifact authority.

## Decision

Senior 4 consumes these provider contracts directly:

- `contracts/runtime/runtime-artifact-result-v1.schema.json`;
- `contracts/runtime/runtime-config-v1.schema.json`;
- `contracts/database/database-runtime-v1.schema.json`;
- `contracts/database/migration-readiness-v1.schema.json`;
- `contracts/native/native-artifact-v1.schema.json`.

Senior 1's signed Mix manifest remains byte-for-byte unchanged. Senior 4 creates a separate signed `mix-deployment-v1` wrapper binding its checksum to:

- passed runtime artifact evidence;
- exact runtime config;
- accepted database contract and write-ready migration result;
- exact Senior 3 Sigstore provider manifests, Ed25519 deployment manifests and immutable install paths;
- exact retained rollback descriptor.

The wrapper and all referenced files form a self-contained staging package. The host verifies Senior 1 release signatures, the Senior 4 wrapper signature and the Senior 3 complete native handoff. The handoff preserves the original Sigstore/SBOM/provenance identity and separately verifies the offline Ed25519 install authorization over the same artifact bytes. There is no permissive signature fallback and no NIF load outside the Senior 1 runtime lifecycle.

The BEAM unit selects `/etc/liqi/runtime/current.json`, exports the standard `CREDENTIALS_DIRECTORY`, and requires exactly:

- `phoenix-secret-key-base`;
- `database-role-urls`;
- `drain-token`;
- `platform-probe-token`.

Caddy is rendered from validated runtime config rather than hardcoding port or socket path. It forwards protected headers without logging values. Cutover proves HTTPS redirect, TLS liveness and an authenticated WebSocket upgrade; full durable/resume/native walking-skeleton evidence remains the Senior 1 live platform probe composed by Senior 5.

## Native deployment adapter

`native/scripts/prepare_deployment_manifest.py` is the provider-owned bridge between the Senior 3 Sigstore manifest and Senior 4's offline host authorization. Its `liqi.deployment.native-artifact/v1` output is a production input, not a duplicate artifact authority: both manifests must bind the same source revision, target, ABI, artifact checksum and size.

The signed Mix manifest references the deployment manifest checksum. Senior 4's wrapper additionally binds the original provider manifest checksum. Preparation and host staging both call `native/scripts/verify_deployment_manifest.py`; the host installs only the deployment-manifest path and leaves the load probe pending until BEAM activation.

`infrastructure/deployment/authorize_native_artifact.py` is the protected build-time provider for the offline authorization. It first verifies the Senior 3 Sigstore artifact, signs the unchanged `.so` bytes with the Senior 4 Ed25519 deployment key, invokes the Senior 3 adapter and full verifier, and emits `native-authorization-result-v1`. The command performs no OCI mutation and never installs or loads the NIF.

The adapter can be removed only through a versioned contract that preserves both Sigstore provenance and offline host authorization. Senior 3 owns provider identity and safety fields; Senior 4 owns the protected deployment signing key and immutable host installation.

## Trade-offs

The deployment package is more verbose and uses multiple signatures, but each signature has one owner and one purpose. The extra wrapper prevents build, database, native and rollback evidence from drifting without modifying provider-owned artifacts. Artifact and live capability remain evidence-pending until approved ARM64 build, OCI staging, activation, endpoint and rollback drills execute.
