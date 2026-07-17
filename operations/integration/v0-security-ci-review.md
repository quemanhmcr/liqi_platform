# V0 security and CI closeout review

Status: source-verified; runtime/cloud evidence remains owner/environment-gated.

## Security source findings

| Control | Source evidence | Result |
|---|---|---|
| Public ingress | OCI network contract and validator allow TCP 80/443 only; SSH is disabled by default and world-open SSH is rejected | passed |
| Database exposure | PostgreSQL/PgBouncer contracts bind Unix socket or loopback/private paths; no public security-list rule | passed |
| Runtime/telemetry exposure | API/realtime/worker and OTLP/metrics bind loopback; edge is the only public consumer | passed |
| Host edge | default NGINX closes HTTP and rejects TLS; approved-site template has fixed redirect target, bounded body/timeouts, WebSocket headers, and JSON 503 fallback | passed |
| Runtime identities | dedicated `liqi-api`, `liqi-realtime`, `liqi-worker` users; base units reject missing config/secret and do not run as root | passed |
| Secret handling | config contains secret references, not resolved values; per-service runtime secret directories are 0700; source secret scan passes | passed |
| Cloud-init | no plaintext password/token/private key; exact public key only; rendered source is parsed and size checked | passed |
| systemd | CPU/memory/tasks/swap controls, restart bounds, readiness ordering, capability/filesystem/kernel restrictions | passed |
| IAM/Object Storage | instance-principal policy is contract-bounded; backup object deletion is not granted to the instance | passed |
| Recovery | provider-owned isolated target guards, approval/freshness checks, checksummed result, cleanup marker | passed |
| Durable event processing | committed handoff function, idempotent terminal effect, fail-closed access/readiness | passed |

Source validation commands:

```bash
python infrastructure/validation/validate_infrastructure.py --with-tofu
bash database/tests/run-source-validation.sh
python scripts/operations/scan_repository_secrets.py
python scripts/operations/validate_telemetry_runtime.py
python scripts/operations/validate_provider_compatibility.py --output .artifacts/provider-compatibility-result.json
python -m unittest discover -s tests -p "test_*.py" -v
```

## CI findings

- third-party actions are pinned by full commit SHA;
- workflow permissions are least privilege and checkout credentials are not persisted;
- source workflows contain no OCI apply/deploy or automatic runtime build;
- disposable database/recovery/promotion work requires explicit inputs/evidence;
- artifacts from another run require repository/ref/digest binding;
- evidence is uploaded before a failing final authority step where diagnostic preservation is required;
- `continue-on-error` is not used as a readiness bypass;
- the readiness assembler is the final source/promotion authority;
- fixture evidence is rejected in promotion paths.

Validation:

```bash
python scripts/operations/validate_ci_workflows.py
python scripts/operations/validate_dependency_policy.py
python scripts/operations/validate_provider_registry.py
```

## Evidence still required

Source review does not prove host runtime state. Before promotion, owner/environment evidence must prove package installation, systemd start behavior, database migrations, backup/recovery, saved OCI plan, release activation, and end-to-end platform probe against the exact Git SHA.
