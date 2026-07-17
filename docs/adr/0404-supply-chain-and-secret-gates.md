# ADR 0404: Supply-chain and secret gates

## Status

Accepted for V0.

## Decision

Tracked source is scanned locally and by a pinned gitleaks action. Runnable Rust artifacts must also pass provider-owned locked dependency resolution plus RustSec/cargo-deny gates once the Cargo workspace exists.

Dependency policy is fail closed for unknown registry, unknown Git source, unlicensed package and unknown license. High/critical advisories, yanked packages and unsound advisories block release. Every exception requires owner, expiry and an ADR; V0 begins with no exceptions.

The release manifest references SPDX SBOM and in-toto/SLSA provenance by digest. Provenance proves origin and build linkage, not vulnerability absence.

## Consequences

- Test fixtures use explicit `TEST_ONLY_` sentinels rather than realistic credentials.
- Secret-bearing filenames and private key material cannot be tracked.
- GPL/AGPL/SSPL/BUSL packages require an explicit policy change and legal/architecture review rather than accidental introduction.
- Senior 4 defines policy and CI invocation; Senior 3 owns dependency choices and provider build correctness.
