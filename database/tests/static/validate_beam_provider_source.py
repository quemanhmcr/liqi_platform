#!/usr/bin/env python3
from __future__ import annotations
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
PERSISTENCE = ROOT / "beam/apps/liqi_persistence"
JOBS = ROOT / "beam/apps/liqi_jobs"
errors: list[str] = []
required = [
    PERSISTENCE / "mix.exs",
    JOBS / "mix.exs",
    PERSISTENCE / "lib/liqi_persistence/runtime_adapter.ex",
    PERSISTENCE / "lib/liqi_persistence/transaction.ex",
    PERSISTENCE / "lib/liqi_persistence/outbox.ex",
    PERSISTENCE / "lib/liqi_persistence/realtime_handoff.ex",
    JOBS / "lib/liqi_jobs/maintenance_worker.ex",
]
for path in required:
    if not path.is_file():
        errors.append(f"missing provider source: {path.relative_to(ROOT)}")

owned_sources = []
for base in (PERSISTENCE, JOBS):
    owned_sources.append(base / "mix.exs")
    for owned_root in (base / "config", base / "lib", base / "test"):
        if owned_root.is_dir():
            owned_sources.extend(owned_root.rglob("*.ex"))
            owned_sources.extend(owned_root.rglob("*.exs"))
text = "\n".join(
    path.read_text(encoding="utf-8")
    for path in sorted(set(owned_sources))
    if path.is_file()
)
for token in (
    "prepare: :unnamed",
    "pool_size: 12",
    "pool_size: 4",
    "pool_size: 6",
    "platform.request_probe_v1",
    "platform.claim_outbox_v1",
    "platform.read_realtime_handoff_v1",
    "platform.database_readiness_v1",
    "defmodule LiqiPersistence.RuntimeAdapter",
    "consumer command module's `event_id/1`",
    "Application.get_env(:liqi_persistence, :start_repos, false)",
    "Application.get_env(:repos, @defaults)",
    "repo: LiqiPersistence.Repos.worker()",
    "notifier: {Oban.Notifiers.PG",
    "shutdown_grace_period: 60_000",
    "stage_interval: 1_000",
    "recovery: [limit: 1, paused: true]",
    "def timeout(_job)",
):
    if token not in text:
        errors.append(f"BEAM provider source missing policy token: {token}")

for forbidden in (
    r"\bFROM\s+platform\.outbox_events\b",
    r"\bFROM\s+platform\.command_idempotency_v1\b",
    r"\bFROM\s+platform\.probe_state_v0\b",
    r"\bINSERT\s+INTO\s+oban\.oban_jobs\b",
    r"Repo\.query!",
    r"prepare:\s*:named",
):
    if re.search(forbidden, text, re.IGNORECASE):
        errors.append(f"BEAM provider bypasses approved function seam: {forbidden}")

adapter_text = (PERSISTENCE / "lib/liqi_persistence/runtime_adapter.ex").read_text(encoding="utf-8")
repos_text = (PERSISTENCE / "lib/liqi_persistence/repos.ex").read_text(encoding="utf-8")
if not re.search(r"function_exported\?\(module,\s*:event_id,\s*1\)", adapter_text):
    errors.append("runtime adapter must delegate command event identity to the consumer module")
if not re.search(r"apply\(module,\s*:event_id,\s*\[command\]\)", adapter_text):
    errors.append("runtime adapter must invoke the consumer event_id/1 implementation")
if not re.search(r"\|>\s*Application\.get_env\(\s*:repos,\s*@defaults\s*\)", repos_text, re.DOTALL):
    errors.append("runtime owner configurable Repo seam is missing")
if "DateTime.compare(deadline, DateTime.utc_now())" not in adapter_text or ":deadline_exceeded" not in adapter_text:
    errors.append("runtime adapter must reject expired commands before opening a transaction")
for callback in ("readiness", "request_probe", "observe_probe", "claim_probe_events", "apply_probe_effect", "fail_event", "read_handoff"):
    if not re.search(rf"\bdef {callback}\b", adapter_text):
        errors.append(f"runtime adapter missing callback: {callback}")

if text.count("use Ecto.Repo") != 3:
    errors.append("persistence provider must publish exactly three bounded Ecto repositories")
if "configured_concurrency() == 7" not in text or "active_concurrency() == 6" not in text:
    errors.append("Oban concurrency tests do not bind the declared 7/6 limits")
if "Oban" in (PERSISTENCE / "lib/liqi_persistence/outbox.ex").read_text(encoding="utf-8"):
    errors.append("domain outbox API must not depend on Oban")

secret_patterns = [r"postgres(?:ql)?://[^\s\"]+:[^\s\"]+@", r"password:\s*\"[^\"]+\""]
for pattern in secret_patterns:
    if re.search(pattern, text, re.IGNORECASE):
        errors.append(f"secret-shaped BEAM source: {pattern}")

if errors:
    for error in errors:
        print(f"ERROR: {error}")
    raise SystemExit(1)
print(json.dumps({"validation":"beam-persistence-jobs-source-v1","ectoRepos":3,"ectoPoolDemand":22,"obanConfiguredConcurrency":7,"obanActiveConcurrency":6,"directAuthorityQueries":False,"passed":True}, separators=(",", ":")))
