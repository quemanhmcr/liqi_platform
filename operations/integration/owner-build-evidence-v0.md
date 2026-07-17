# Project-owner runtime build evidence V0

These four commands compile or link Rust and therefore are **not** run by source CI or the V0 closeout DRI. The project owner runs them only after approving the exact clean Git SHA.

## Preconditions

```bash
git status --short --untracked-files=no
git rev-parse HEAD
```

Expected: no tracked changes and the SHA intended for V0 readiness. Set:

```bash
sha=$(git rev-parse HEAD)
out=.artifacts/owner-build/$sha
approval_ref=<review-or-ticket-reference>
mkdir -p "$out"
```

## Exact commands and expected results

Validation manifest:

```bash
cargo +1.97.1 run --locked -p liqi-platform-tool -- print-validation-manifest
```

Expected: exit `0`; stdout is valid JSON. Evidence result: `$out/runtime-validation-manifest.result.json`.

Runtime contract validation:

```bash
cargo +1.97.1 run --locked -p liqi-platform-tool -- \
  validate-contracts --root .
```

Expected: exit `0`; stdout is valid JSON reporting all runtime contracts passed. Evidence result: `$out/runtime-contract-validation.result.json`.

Clippy:

```bash
cargo +1.97.1 clippy \
  --workspace \
  --all-targets \
  --all-features \
  --locked \
  -- -D warnings
```

Expected: exit `0` with no warnings. Evidence log: `$out/runtime-clippy-source.log`.

Workspace tests:

```bash
cargo +1.97.1 test \
  --workspace \
  --all-targets \
  --all-features \
  --locked
```

Expected: exit `0`; all tests pass. Evidence log: `$out/runtime-contract-tests.log`.

## Non-manual evidence generation

The owner wrapper executes the exact `argv` above from `provider-gates-v0`, requires a clean tracked tree, records the current SHA, redacts logs, hashes exact bytes and writes `owner-build-evidence-v0`:

```bash
python scripts/operations/run_owner_build_gate.py --gate-id runtime-validation-manifest --approval-ref "$approval_ref" --output-dir "$out"
python scripts/operations/run_owner_build_gate.py --gate-id runtime-contract-validation --approval-ref "$approval_ref" --output-dir "$out"
python scripts/operations/run_owner_build_gate.py --gate-id runtime-clippy-source --approval-ref "$approval_ref" --output-dir "$out"
python scripts/operations/run_owner_build_gate.py --gate-id runtime-contract-tests --approval-ref "$approval_ref" --output-dir "$out"
```

Do not edit evidence JSON. A failed command remains `failed`; rerun only after a source fix and against the new exact SHA.

## Readiness ingestion

```bash
python scripts/operations/run_provider_gates.py \
  --stage source \
  --owner-evidence-dir "$out" \
  --output .artifacts/provider-source-result.json

python scripts/operations/assemble_source_readiness.py \
  --provider-result .artifacts/provider-source-result.json \
  --compatibility-result .artifacts/provider-compatibility-result.json \
  --capacity-result .artifacts/provider-capacity-result.json \
  --output .artifacts/source-integration-readiness-v0.json
```

Missing evidence is `blocked`. Wrong SHA, command, schema or digest is `failed`. Promotion does not accept either status.
