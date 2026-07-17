# ADR 0413: Bind SPDX and SLSA evidence to release subjects

## Status

Accepted for V0.

## Decision

A release uses one SPDX 2.3 document and one in-toto Statement v1 with SLSA provenance v1 predicate covering all three runtime binaries. Senior 4 validates evidence content against `release-manifest-v0`:

- artifact names and SHA-256 digests match exactly;
- SPDX identity, data license, creation metadata and file checksums exist;
- provenance has one statement, a builder ID, build type and the three subjects;
- resolved source `gitCommit` equals the manifest full Git SHA;
- manifest file digests equal the supplied SBOM and provenance files.

## Trust boundary

Content validation proves internal consistency, not builder authenticity. A staging or production release still requires a signed artifact attestation or equivalent approved provenance verification. The release workflow must pin the attestation implementation and retain verification evidence.

Senior 3/build owner produces binaries and native evidence. Senior 4 does not infer Cargo package names, cross-linker flags or build commands before the runtime provider publishes them.

## Consequences

- Empty-subject or generic SBOM/provenance documents are rejected.
- A correct digest of semantically unrelated evidence is rejected.
- A source commit mismatch is rejected even when artifact hashes match.
- The gate is additive and does not run a build.
