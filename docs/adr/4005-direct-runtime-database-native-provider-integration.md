# ADR 4005: Direct runtime, database and native provider integration

- Status: Accepted; source integration complete, artifact/live evidence pending
- Date: 2026-07-18
- Owner: Senior 4
- Consumers: Senior 1, Senior 2, Senior 3, Senior 5
- Integrated provider graph: `15e2dd5a263decb91308a0d1783c4610bd7dc62d`

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
- exact Senior 3 native manifests and immutable install paths;
- exact retained rollback descriptor.

The wrapper and all referenced files form a self-contained staging package. The host verifies both Senior 1 and Senior 4 Ed25519 trust roots and delegates native verification to Senior 3's verifier. There is no permissive signature fallback and no NIF load outside the Senior 1 runtime lifecycle.

The BEAM unit selects `/etc/liqi/runtime/current.json`, exports the standard `CREDENTIALS_DIRECTORY`, and requires exactly:

- `phoenix-secret-key-base`;
- `database-role-urls`;
- `drain-token`;
- `platform-probe-token`.

Caddy is rendered from validated runtime config rather than hardcoding port or socket path. It forwards protected headers without logging values. Cutover proves HTTPS redirect, TLS liveness and an authenticated WebSocket upgrade; full durable/resume/native walking-skeleton evidence remains the Senior 1 live platform probe composed by Senior 5.

## Compatibility adapter

The final signed Mix manifest already emits the immutable Senior 3 artifact install mapping. The production preparation/staging path therefore consumes that mapping directly; it does not accept an operator-supplied competing path.

`contracts/deployment/native-artifact-v1.schema.json` is retained as a deprecated compatibility record for the already-published seam, but it is not a production input. Senior 3 remains the authority for ABI, scheduler, signature, provenance and artifact identity.

Removal owner: Senior 4. Remove the deprecated schema after Senior 5 and any external consumer stop registering it for one release window.

## Trade-offs

The deployment package is more verbose and uses two signatures, but each signature has one owner and one purpose. The extra wrapper prevents build, database, native and rollback evidence from drifting without modifying provider-owned artifacts. Artifact and live capability remain evidence-pending until approved ARM64 build, OCI staging, activation, endpoint and rollback drills execute.
