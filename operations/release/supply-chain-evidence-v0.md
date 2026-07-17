# Supply-chain evidence V0

The release bundle uses one SPDX 2.3 document and one SLSA provenance v1 statement covering all three binaries.

`validate_supply_chain_evidence.py` requires:

- the exact three artifact names and SHA-256 digests from `release-manifest-v0`;
- SPDX document identity, CC0 data license, creation metadata and file checksums;
- one in-toto Statement v1 with SLSA provenance v1 predicate;
- builder ID, build type and resolved Git commit equal to the manifest full SHA;
- manifest-level SBOM and provenance file digests equal to supplied evidence.

This content validation does not authenticate the builder. Staging/production additionally require a signed GitHub artifact attestation or equivalent approved provenance verification. Artifact production remains Senior 3/build-owner work; Senior 4 does not guess the Cargo package or linker command.
