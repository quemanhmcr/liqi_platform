#!/usr/bin/env python3
"""Build and sign the deterministic LIQI V1 host runtime bundle."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import os
import re
import subprocess
import tarfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[2]
GIT_SHA = re.compile(r"^[0-9a-f]{40}$")
IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9._-]{2,95}$")
TARGET_PACKAGE_MANIFESTS = {
    "aarch64-unknown-linux-gnu": "infrastructure/packages/oracle-linux-9-aarch64-v1.json",
    "x86_64-unknown-linux-gnu": "infrastructure/packages/oracle-linux-9-x86_64-v1.json",
}

# Provider-owned source file -> immutable host target, mode, owner, group.
BASE_FILES: tuple[tuple[str, str, int, str, str], ...] = (
    ("infrastructure/bin/liqi-install-host-bundle", "/usr/local/libexec/liqi-install-host-bundle", 0o755, "root", "root"),
    ("infrastructure/bin/liqi-install-runtime-packages", "/usr/local/libexec/liqi-install-runtime-packages", 0o755, "root", "root"),
    ("infrastructure/deployment/host_package_manifest.py", "/usr/local/libexec/liqi-host-package-settings", 0o755, "root", "root"),
    ("infrastructure/bin/liqi-prepare-data-volume", "/usr/local/libexec/liqi-prepare-data-volume", 0o755, "root", "root"),
    ("infrastructure/bin/liqi-materialize-secrets", "/usr/local/libexec/liqi-materialize-secrets", 0o755, "root", "root"),
    ("infrastructure/bin/liqi-configure-database-credentials", "/usr/local/libexec/liqi-configure-database-credentials", 0o755, "root", "root"),
    ("infrastructure/bin/liqi-host-readiness", "/usr/local/libexec/liqi-host-readiness", 0o755, "root", "root"),
    ("infrastructure/bin/liqi-release-command", "/usr/local/libexec/liqi-release-command", 0o755, "root", "root"),
    ("infrastructure/deployment/stage_mix_release.py", "/usr/local/libexec/liqi-stage-mix-release", 0o755, "root", "root"),
    ("infrastructure/deployment/adapt_v0_rollback.py", "/usr/local/libexec/liqi-retain-v0-rollback", 0o755, "root", "root"),
    ("infrastructure/deployment/release_control.py", "/usr/local/libexec/liqi-release-control", 0o755, "root", "root"),
    ("infrastructure/deployment/configure_live_edge.py", "/usr/local/libexec/liqi-configure-live-edge", 0o755, "root", "root"),
    ("infrastructure/deployment/enable_backup_timers.py", "/usr/local/libexec/liqi-enable-backup-timers", 0o755, "root", "root"),
    ("scripts/release/health_gate.py", "/usr/local/lib/liqi-v0/scripts/release/health_gate.py", 0o755, "root", "root"),
    ("infrastructure/systemd/liqi-platform.slice", "/etc/systemd/system/liqi-platform.slice", 0o644, "root", "root"),
    ("infrastructure/systemd/liqi-beam.slice", "/etc/systemd/system/liqi-beam.slice", 0o644, "root", "root"),
    ("infrastructure/systemd/liqi-platform-runtime.slice", "/etc/systemd/system/liqi-platform-runtime.slice", 0o644, "root", "root"),
    ("infrastructure/systemd/liqi-database.slice", "/etc/systemd/system/liqi-database.slice", 0o644, "root", "root"),
    ("infrastructure/systemd/liqi-edge.slice", "/etc/systemd/system/liqi-edge.slice", 0o644, "root", "root"),
    ("infrastructure/systemd/liqi-telemetry.slice", "/etc/systemd/system/liqi-telemetry.slice", 0o644, "root", "root"),
    ("infrastructure/systemd/liqi-data-volume.service", "/etc/systemd/system/liqi-data-volume.service", 0o644, "root", "root"),
    ("infrastructure/systemd/liqi-host-readiness.service", "/etc/systemd/system/liqi-host-readiness.service", 0o644, "root", "root"),
    ("infrastructure/systemd/liqi-secrets.service", "/etc/systemd/system/liqi-secrets.service", 0o644, "root", "root"),
    ("infrastructure/systemd/liqi-database-credentials.service", "/etc/systemd/system/liqi-database-credentials.service", 0o644, "root", "root"),
    ("infrastructure/systemd/liqi-beam.service", "/etc/systemd/system/liqi-beam.service", 0o644, "root", "root"),
    ("services/systemd/liqi-api.service", "/etc/systemd/system/liqi-api.service", 0o644, "root", "root"),
    ("services/systemd/liqi-realtime.service", "/etc/systemd/system/liqi-realtime.service", 0o644, "root", "root"),
    ("services/systemd/liqi-worker.service", "/etc/systemd/system/liqi-worker.service", 0o644, "root", "root"),
    ("infrastructure/systemd/v0/liqi-api-capacity.conf", "/etc/systemd/system/liqi-api.service.d/10-capacity.conf", 0o644, "root", "root"),
    ("infrastructure/systemd/v0/liqi-realtime-capacity.conf", "/etc/systemd/system/liqi-realtime.service.d/10-capacity.conf", 0o644, "root", "root"),
    ("infrastructure/systemd/v0/liqi-worker-capacity.conf", "/etc/systemd/system/liqi-worker.service.d/10-capacity.conf", 0o644, "root", "root"),
    ("infrastructure/systemd/caddy.service", "/etc/systemd/system/caddy.service", 0o644, "root", "root"),
    ("infrastructure/systemd/otelcol.service.d-liqi.conf", "/etc/systemd/system/otelcol.service.d/liqi.conf", 0o644, "root", "root"),
    ("infrastructure/systemd/postgresql-17.service.d-liqi.conf", "/etc/systemd/system/postgresql-17.service.d/liqi.conf", 0o644, "root", "root"),
    ("infrastructure/systemd/pgbouncer.service.d-liqi.conf", "/etc/systemd/system/pgbouncer.service.d/liqi.conf", 0o644, "root", "root"),
    ("database/systemd/liqi-database-backup@.service", "/etc/systemd/system/liqi-database-backup@.service", 0o644, "root", "root"),
    ("database/systemd/liqi-database-backup-full.timer", "/etc/systemd/system/liqi-database-backup-full.timer", 0o644, "root", "root"),
    ("database/systemd/liqi-database-backup-diff.timer", "/etc/systemd/system/liqi-database-backup-diff.timer", 0o644, "root", "root"),
    ("database/systemd/liqi-database-repository-check.service", "/etc/systemd/system/liqi-database-repository-check.service", 0o644, "root", "root"),
    ("database/systemd/liqi-database-repository-check.timer", "/etc/systemd/system/liqi-database-repository-check.timer", 0o644, "root", "root"),
    ("database/systemd/liqi-database-v0.conf.tmpfiles", "/usr/lib/tmpfiles.d/liqi-database-v0.conf", 0o644, "root", "root"),
    ("infrastructure/systemd/database/liqi-database-backup-credentials.conf", "/etc/systemd/system/liqi-database-backup@.service.d/10-credentials.conf", 0o644, "root", "root"),
    ("infrastructure/systemd/database/liqi-database-repository-check-credentials.conf", "/etc/systemd/system/liqi-database-repository-check.service.d/10-credentials.conf", 0o644, "root", "root"),
    ("infrastructure/caddy/Caddyfile.fail-closed", "/etc/caddy/Caddyfile", 0o640, "root", "caddy"),
    ("infrastructure/caddy/Caddyfile.v1-live.tftpl", "/usr/local/share/liqi/Caddyfile.v1-live.tftpl", 0o640, "root", "liqi"),
    ("infrastructure/otel/otelcol-v1.yaml", "/etc/otelcol/config.yaml", 0o644, "root", "root"),
    ("contracts/infrastructure/host-runtime-v1.schema.json", "/usr/local/share/liqi/contracts/infrastructure/host-runtime-v1.schema.json", 0o644, "root", "root"),
    ("contracts/infrastructure/secret-mapping-v1.schema.json", "/usr/local/share/liqi/contracts/infrastructure/secret-mapping-v1.schema.json", 0o644, "root", "root"),
    ("contracts/infrastructure/database-credentials-v1.schema.json", "/usr/local/share/liqi/contracts/infrastructure/database-credentials-v1.schema.json", 0o644, "root", "root"),
    ("contracts/deployment/mix-release-v1.schema.json", "/usr/local/share/liqi/contracts/deployment/mix-release-v1.schema.json", 0o644, "root", "root"),
    ("contracts/deployment/mix-deployment-v1.schema.json", "/usr/local/share/liqi/contracts/deployment/mix-deployment-v1.schema.json", 0o644, "root", "root"),
    ("contracts/deployment/v0-rollback-compatibility-v1.schema.json", "/usr/local/share/liqi/contracts/deployment/v0-rollback-compatibility-v1.schema.json", 0o644, "root", "root"),
    ("contracts/deployment/native-artifact-v1.schema.json", "/usr/local/share/liqi/contracts/deployment/native-artifact-v1.schema.json", 0o644, "root", "root"),
    ("contracts/deployment/release-target-v1.schema.json", "/usr/local/share/liqi/contracts/deployment/release-target-v1.schema.json", 0o644, "root", "root"),
    ("contracts/deployment/installed-release-v1.schema.json", "/usr/local/share/liqi/contracts/deployment/installed-release-v1.schema.json", 0o644, "root", "root"),
    ("contracts/deployment/activation-v1.schema.json", "/usr/local/share/liqi/contracts/deployment/activation-v1.schema.json", 0o644, "root", "root"),
    ("contracts/deployment/rollback-v1.schema.json", "/usr/local/share/liqi/contracts/deployment/rollback-v1.schema.json", 0o644, "root", "root"),
    ("contracts/deployment/live-endpoint-v1.schema.json", "/usr/local/share/liqi/contracts/deployment/live-endpoint-v1.schema.json", 0o644, "root", "root"),
    ("contracts/operations/release-manifest-v0.schema.json", "/usr/local/share/liqi/contracts/operations/release-manifest-v0.schema.json", 0o644, "root", "root"),
    ("contracts/operations/deployment-spec-v0.schema.json", "/usr/local/share/liqi/contracts/operations/deployment-spec-v0.schema.json", 0o644, "root", "root"),
    ("contracts/operations/health-gate-target-v0.schema.json", "/usr/local/share/liqi/contracts/operations/health-gate-target-v0.schema.json", 0o644, "root", "root"),
)

DATABASE_SOURCE_ROOTS = (
    "database/bin",
    "database/bootstrap",
    "database/config",
    "database/migrations",
    "database/recovery",
    "database/runbooks",
    "database/tools",
)

def database_files() -> tuple[tuple[str, str, int, str, str], ...]:
    records: list[tuple[str, str, int, str, str]] = []
    for relative_root in DATABASE_SOURCE_ROOTS:
        for source in sorted((ROOT / relative_root).rglob("*")):
            if not source.is_file() or source.name == ".gitkeep":
                continue
            repository_path = source.relative_to(ROOT).as_posix()
            target = "/usr/local/lib/liqi-database/" + repository_path
            executable = source.suffix in {".sh", ".py"} or source.read_bytes().startswith(b"#!")
            records.append((repository_path, target, 0o755 if executable else 0o644, "root", "root"))
    for source in sorted((ROOT / "contracts/platform").glob("database-*.json")):
        repository_path = source.relative_to(ROOT).as_posix()
        records.append((repository_path, "/usr/local/lib/liqi-database/" + repository_path, 0o644, "root", "root"))
    return tuple(records)


PROVIDER_CONTRACT_FILES = (
    "contracts/runtime/runtime-config-v1.schema.json",
    "contracts/runtime/runtime-artifact-result-v1.schema.json",
    "contracts/database/database-runtime-v1.schema.json",
    "contracts/database/migration-readiness-v1.schema.json",
    "contracts/native/native-artifact-v1.schema.json",
    "contracts/deployment/native-artifact-v1.schema.json",
)

NATIVE_SOURCE_ROOTS = (
    "native/scripts",
)


def provider_files() -> tuple[tuple[str, str, int, str, str], ...]:
    records: list[tuple[str, str, int, str, str]] = []
    for repository_path in PROVIDER_CONTRACT_FILES:
        source = ROOT / repository_path
        if not source.is_file():
            raise FileNotFoundError(repository_path)
        if repository_path.startswith("contracts/runtime/"):
            target = "/usr/local/share/liqi/contracts/runtime/" + source.name
        elif repository_path.startswith("contracts/database/"):
            target = "/usr/local/lib/liqi-database/contracts/database/" + source.name
        elif repository_path.startswith("contracts/native/"):
            target = "/usr/local/lib/liqi-native/contracts/native/" + source.name
        else:
            target = "/usr/local/lib/liqi-native/contracts/deployment/" + source.name
        records.append((repository_path, target, 0o644, "root", "root"))
    for relative_root in NATIVE_SOURCE_ROOTS:
        for source in sorted((ROOT / relative_root).rglob("*")):
            if not source.is_file() or "__pycache__" in source.parts:
                continue
            repository_path = source.relative_to(ROOT).as_posix()
            target = "/usr/local/lib/liqi-native/" + repository_path
            executable = source.suffix in {".sh", ".py"} or source.read_bytes().startswith(b"#!")
            records.append((repository_path, target, 0o755 if executable else 0o644, "root", "root"))
    return tuple(records)


def files_for_target(target_triple: str) -> tuple[tuple[str, str, int, str, str], ...]:
    package = (
        TARGET_PACKAGE_MANIFESTS[target_triple],
        "/usr/local/share/liqi/host-packages-v1.json",
        0o640,
        "root",
        "liqi",
    )
    return BASE_FILES + (package,) + database_files() + provider_files()


FILES = files_for_target("aarch64-unknown-linux-gnu")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--git-sha", required=True)
    parser.add_argument("--bundle-id", required=True)
    parser.add_argument("--key-id", required=True)
    parser.add_argument("--signing-key", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--allow-dirty-source-test-only", action="store_true")
    parser.add_argument("--target-triple", choices=tuple(TARGET_PACKAGE_MANIFESTS), default="aarch64-unknown-linux-gnu")
    return parser.parse_args()


def run(*argv: str, input_bytes: bytes | None = None) -> bytes:
    result = subprocess.run(
        argv,
        cwd=ROOT,
        input=input_bytes,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        raise SystemExit(result.stderr.decode("utf-8", errors="replace").strip() or f"command failed: {argv}")
    return result.stdout


def ensure_exact_source(git_sha: str, allow_dirty: bool, files: tuple[tuple[str, str, int, str, str], ...]) -> None:
    run("git", "cat-file", "-e", f"{git_sha}^{{commit}}")
    paths = [item[0] for item in files]
    tracked = set(run("git", "ls-tree", "-r", "--name-only", git_sha, "--", *paths).decode().splitlines())
    missing = sorted(set(paths) - tracked)
    if missing and not allow_dirty:
        raise SystemExit(f"bundle sources are not tracked at {git_sha}: {missing}")
    if not allow_dirty:
        result = subprocess.run(["git", "diff", "--quiet", git_sha, "--", *paths], cwd=ROOT, check=False)
        if result.returncode != 0:
            raise SystemExit("bundle source differs from the exact Git SHA")


def commit_created_at(git_sha: str) -> str:
    return run("git", "show", "-s", "--format=%cI", git_sha).decode().strip()


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def archive_payload(records: list[dict[str, object]]) -> bytes:
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w", format=tarfile.PAX_FORMAT) as tar:
        for record in records:
            source = ROOT / str(record["repository_path"])
            data = source.read_bytes()
            info = tarfile.TarInfo(str(record["source_path"]))
            info.size = len(data)
            info.mode = int(str(record["mode"]), 8)
            info.uid = 0
            info.gid = 0
            info.uname = "root"
            info.gname = "root"
            info.mtime = 0
            info.pax_headers = {}
            tar.addfile(info, io.BytesIO(data))
    compressed = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=compressed, compresslevel=9, mtime=0) as stream:
        stream.write(raw.getvalue())
    return compressed.getvalue()


def main() -> int:
    args = parse_args()
    if not GIT_SHA.fullmatch(args.git_sha):
        raise SystemExit("--git-sha must be a lowercase 40-character SHA")
    for value, label in ((args.bundle_id, "bundle ID"), (args.key_id, "key ID")):
        if not IDENTIFIER.fullmatch(value):
            raise SystemExit(f"invalid {label}")
    if not args.signing_key.is_file():
        raise SystemExit("signing key file does not exist")
    selected_files = files_for_target(args.target_triple)
    ensure_exact_source(args.git_sha, args.allow_dirty_source_test_only, selected_files)

    records: list[dict[str, object]] = []
    targets: set[str] = set()
    for repository_path, target, mode, owner, group in selected_files:
        source = ROOT / repository_path
        if not source.is_file():
            raise SystemExit(f"missing bundle source: {repository_path}")
        if target in targets:
            raise SystemExit(f"duplicate bundle target: {target}")
        targets.add(target)
        data = source.read_bytes()
        source_path = "payload" + target
        if PurePosixPath(source_path).is_absolute() or ".." in PurePosixPath(source_path).parts:
            raise SystemExit(f"unsafe archive path: {source_path}")
        records.append({
            "repository_path": repository_path,
            "source_path": source_path,
            "target_path": target,
            "mode": f"0{mode:o}",
            "owner": owner,
            "group": group,
            "size_bytes": len(data),
            "sha256": digest(data),
        })

    archive = archive_payload(records)
    archive_name = f"liqi-host-bundle-{args.bundle_id}.tar.gz"
    manifest_name = f"liqi-host-bundle-{args.bundle_id}.json"
    signature_name = f"{manifest_name}.sig"
    public_records = [{k: v for k, v in record.items() if k != "repository_path"} for record in records]
    manifest = {
        "schema_version": "liqi.infrastructure.host-bundle/v1",
        "bundle_id": args.bundle_id,
        "git_sha": args.git_sha,
        "target_triple": args.target_triple,
        "artifact": {"filename": archive_name, "size_bytes": len(archive), "sha256": digest(archive)},
        "signing": {"algorithm": "ed25519", "key_id": args.key_id, "signature_filename": signature_name, "signed_payload": "exact-manifest-bytes"},
        "files": public_records,
        "installation": {
            "installer": "/usr/local/libexec/liqi-install-host-bundle",
            "delivery": "operator-staged-local",
            "archive_relative_path": f"host/{args.bundle_id}/",
            "staging_root": "/var/lib/liqi/incoming/host",
            "approval_required": True,
            "build_on_host": False,
            "activates_release": False,
        },
        "created_at": commit_created_at(args.git_sha),
    }
    manifest_bytes = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    archive_path = args.output_dir / archive_name
    manifest_path = args.output_dir / manifest_name
    signature_path = args.output_dir / signature_name
    archive_path.write_bytes(archive)
    manifest_path.write_bytes(manifest_bytes)
    run(
        "openssl", "pkeyutl", "-sign", "-rawin",
        "-inkey", str(args.signing_key.resolve()),
        "-in", str(manifest_path.resolve()),
        "-out", str(signature_path.resolve()),
    )
    for path in (archive_path, manifest_path, signature_path):
        os.chmod(path, 0o640)

    result = {
        "schema_version": "liqi.infrastructure.host-bundle-build-result/v1",
        "bundle_id": args.bundle_id,
        "git_sha": args.git_sha,
        "target_triple": args.target_triple,
        "manifest": {"path": str(manifest_path), "sha256": digest(manifest_bytes)},
        "signature": {"path": str(signature_path), "sha256": digest(signature_path.read_bytes())},
        "artifact": {"path": str(archive_path), "sha256": digest(archive), "size_bytes": len(archive)},
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
