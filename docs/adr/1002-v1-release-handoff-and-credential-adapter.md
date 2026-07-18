# ADR 1002: V1 release handoff and credential-directory adapter

- Status: Accepted for Senior 1 implementation; provider evidence pending
- Date: 2026-07-18
- Decision owner: Senior 1
- Consumers: Senior 4, Senior 5

## Context

Senior 4 published a breaking-with-adapter Mix release handoff requiring an exact-manifest Ed25519 signature and a predeclared V0 rollback target. The same provider materializes Vault values into `/run/liqi/secrets/beam` and exports `LIQI_CREDENTIALS_DIRECTORY`, while the runtime contract uses `systemd-credential://` and the standard systemd variable is `CREDENTIALS_DIRECTORY`.

The infrastructure example also contains placeholder credential names and therefore does not yet prove that the runtime-required database, endpoint, drain and platform-probe credentials are materialized.

## Decision

1. Senior 1 emits the new `manifest_signature`, artifact `signed_payload` and `rollback_target_release_id` fields directly; no legacy unsigned manifest is accepted.
2. Artifact verification validates exact schema, SHA-256, archive bounds/layout, actual AArch64 ERTS, exact runtime versions, V0 rollback identity and both Ed25519 signatures. Missing trust material is `blocked`; invalid signatures are `failed`.
3. `systemd-credential://` first resolves through standard `CREDENTIALS_DIRECTORY`. For one V1 release window it also accepts `LIQI_CREDENTIALS_DIRECTORY` as a compatibility adapter for Senior 4's custom materializer.
4. The alias does not accept a plaintext secret value or OCI reference directly. It only locates an already-materialized bounded file.
5. Production readiness remains blocked until the Senior 4 beam secret mapping names every runtime-required credential and live evidence proves the files are owner-readable only.

## Required Senior 4 output

The beam mapping must provide exact provider-owned names referenced by runtime configuration, including at least:

- endpoint secret;
- command/realtime/worker database credentials according to the Senior 2 role contract;
- drain token;
- platform probe token.

Caddy must preserve `x-liqi-probe-token` end-to-end and must not log its value. The token must never be accepted in query parameters.

## Compatibility and removal

The `LIQI_CREDENTIALS_DIRECTORY` alias is additive and temporary. Remove it after Senior 4 either uses systemd `LoadCredential=`/`CREDENTIALS_DIRECTORY` or publishes a versioned runtime config using explicit `file://` references. Removal owner: Senior 1, after one successful live release window and updated Senior 4 contract evidence.

## OCI impact

None. No apply, secret mutation, deployment or traffic change was performed.
