# Supply-chain policy V0

Required release evidence:

1. Locked dependency graph from the provider-owned Cargo workspace.
2. RustSec advisory result under `dependency-policy-v0.json`.
3. License/source result under `dependency-policy-v0.json`.
4. SPDX JSON SBOM for all three runnable artifacts.
5. in-toto/SLSA provenance tied to Git SHA and artifact digest.
6. Pinned CI actions by immutable commit SHA.
7. Secret-scan result covering tracked files and generated logs after redaction.

No finding is suppressed inline. Temporary exceptions live only in the policy file, include an expiry and point to a Senior 4 ADR. Unknown source, license or attestation state blocks promotion.
