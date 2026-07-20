#!/usr/bin/env python3
"""Build, sign, and self-verify an exact-SHA Linux Mix release."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[2]
MIX_SCHEMA = ROOT / "contracts/deployment/mix-release-v1.schema.json"
BUILD_RESULT_SCHEMA = ROOT / "contracts/runtime/linux-release-build-result-v1.schema.json"
NATIVE_SCHEMA = ROOT / "contracts/native/native-artifact-v1.schema.json"
DEPLOYMENT_NATIVE_SCHEMA = ROOT / "contracts/deployment/native-artifact-v1.schema.json"
NATIVE_HANDOFF_VERIFIER = ROOT / "native/scripts/verify_deployment_manifest.py"
RELEASE_VERIFIER = ROOT / "beam/scripts/validate_release_manifest.py"
TARGETS = {
    "aarch64-unknown-linux-gnu": {"architecture": "aarch64", "elf_machine": 183},
    "x86_64-unknown-linux-gnu": {"architecture": "x86_64", "elf_machine": 62},
}
IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9._-]{2,95}$")
KEY_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{2,63}$")
GIT_SHA = re.compile(r"^[0-9a-f]{40}$")
NIF_INSTALL = Path("lib/liqi_native-1.0.0/priv/native/libliqi_sequence_diff_nif.so")


def load(path: Path) -> dict[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return document


def validate(schema_path: Path, document: dict[str, Any], label: str) -> None:
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    errors = list(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(document))
    if errors:
        raise ValueError(f"invalid {label}: {errors[0].message}")


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def safe_child(base: Path, relative: str) -> Path:
    candidate = (base / relative).resolve()
    try:
        candidate.relative_to(base.resolve())
    except ValueError as exc:
        raise ValueError(f"path escapes artifact directory: {relative}") from exc
    return candidate


def run(argv: list[str], *, cwd: Path = ROOT, env: dict[str, str] | None = None, timeout: int = 900, capture: bool = False) -> str:
    completed = subprocess.run(
        argv, cwd=cwd, env=env, text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
        check=False, timeout=timeout,
    )
    if completed.returncode:
        detail = (completed.stderr or completed.stdout or "").strip() if capture else ""
        raise RuntimeError(detail[-4096:] or f"command failed: {argv[0]}")
    return (completed.stdout or "").strip()


def exact_source() -> str:
    sha = run(["git", "rev-parse", "HEAD"], capture=True)
    if not GIT_SHA.fullmatch(sha):
        raise RuntimeError("current Git revision is invalid")
    status = run(["git", "status", "--porcelain", "--untracked-files=all"], capture=True)
    if status:
        raise RuntimeError("release build requires a clean exact-SHA worktree")
    return sha


def verify_toolchain(target_triple: str) -> None:
    expected = TARGETS[target_triple]
    if platform.system() != "Linux" or platform.machine() != expected["architecture"]:
        raise RuntimeError(f"release target {target_triple} requires Linux {expected['architecture']} builder")
    otp = run(["erl", "-noshell", "-eval", 'io:format("~s", [erlang:system_info(otp_release)]), halt().'], capture=True)
    elixir = run(["elixir", "--short-version"], capture=True)
    if otp != "28" or elixir != "1.20.2":
        raise RuntimeError(f"release builder requires OTP 28 and Elixir 1.20.2, got otp={otp!r} elixir={elixir!r}")
    run(["mix", "--version"], capture=True)
    run(["openssl", "version"], capture=True)


def copy_exact(source: Path, destination: Path, seen: dict[str, str]) -> Path:
    source = source.resolve()
    if not source.is_file() or source.is_symlink():
        raise FileNotFoundError(source)
    name = source.name
    checksum = digest(source)
    if name in seen and seen[name] != checksum:
        raise RuntimeError(f"artifact filename collision: {name}")
    target = destination / name
    if not target.exists():
        shutil.copyfile(source, target)
        os.chmod(target, 0o640)
    seen[name] = checksum
    return target


def sign(payload: Path, key: Path, signature: Path) -> None:
    run(["openssl", "pkeyutl", "-sign", "-rawin", "-inkey", str(key.resolve()), "-in", str(payload.resolve()), "-out", str(signature.resolve())], timeout=60)
    os.chmod(signature, 0o640)


def public_key_fingerprint(private_key: Path) -> str:
    completed = subprocess.run(
        ["openssl", "pkey", "-in", str(private_key.resolve()), "-pubout"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, timeout=60,
    )
    if completed.returncode:
        raise RuntimeError(completed.stderr.decode("utf-8", errors="replace").strip() or "cannot derive release public key")
    return hashlib.sha256(completed.stdout).hexdigest()


def derive_public(private_key: Path, key_id: str, trust_dir: Path) -> None:
    public = trust_dir / f"{key_id}.pem"
    run(["openssl", "pkey", "-in", str(private_key.resolve()), "-pubout", "-out", str(public.resolve())], timeout=60)
    os.chmod(public, 0o600)


def deterministic_archive(source: Path, output: Path) -> None:
    for path in source.rglob("*"):
        if path.is_symlink():
            resolved = path.resolve()
            try:
                resolved.relative_to(source.resolve())
            except ValueError as exc:
                raise RuntimeError(f"release symlink escapes release root: {path}") from exc
    with tempfile.TemporaryDirectory(prefix="liqi-release-flat-") as directory:
        flat = Path(directory) / "release"
        shutil.copytree(source, flat, symlinks=False)
        if any(path.is_symlink() for path in flat.rglob("*")):
            raise RuntimeError("release flattening left a symbolic link")
        raw = io.BytesIO()
        with tarfile.open(fileobj=raw, mode="w", format=tarfile.PAX_FORMAT) as archive:
            for path in sorted(flat.rglob("*"), key=lambda item: item.relative_to(flat).as_posix()):
                relative = path.relative_to(flat).as_posix()
                info = tarfile.TarInfo(relative)
                info.uid = info.gid = 0
                info.uname = info.gname = "root"
                info.mtime = 0
                info.pax_headers = {}
                if path.is_dir():
                    info.type = tarfile.DIRTYPE
                    info.mode = 0o755
                    archive.addfile(info)
                elif path.is_file():
                    data = path.read_bytes()
                    info.size = len(data)
                    info.mode = 0o755 if path.stat().st_mode & stat.S_IXUSR else 0o644
                    archive.addfile(info, io.BytesIO(data))
                else:
                    raise RuntimeError(f"unsupported release member: {relative}")
        with output.open("wb") as stream:
            with gzip.GzipFile(filename="", mode="wb", fileobj=stream, compresslevel=9, mtime=0) as compressed:
                compressed.write(raw.getvalue())
    os.chmod(output, 0o640)


def prepare_native(
    provider_path: Path,
    deployment_path: Path,
    trust_dir: Path,
    git_sha: str,
    target_triple: str,
    output_dir: Path,
    seen: dict[str, str],
) -> tuple[dict[str, Any], Path]:
    provider_path = provider_path.resolve()
    deployment_path = deployment_path.resolve()
    provider = load(provider_path)
    deployment = load(deployment_path)
    validate(NATIVE_SCHEMA, provider, "native provider manifest")
    validate(DEPLOYMENT_NATIVE_SCHEMA, deployment, "native deployment manifest")
    if provider["source_revision"] != git_sha or deployment["git_sha"] != git_sha:
        raise RuntimeError("native handoff does not match exact release source revision")
    if provider["target_triple"] != target_triple or deployment["target_triple"] != target_triple:
        raise RuntimeError("native handoff target does not match Mix release target")
    run(
        [
            sys.executable, str(NATIVE_HANDOFF_VERIFIER),
            "--native-manifest", str(provider_path),
            "--deployment-manifest", str(deployment_path),
            "--trust-dir", str(trust_dir.resolve()),
        ],
        timeout=600,
    )
    provider_copy = copy_exact(provider_path, output_dir, seen)
    deployment_copy = copy_exact(deployment_path, output_dir, seen)
    provider_base = provider_path.parent
    artifact = safe_child(provider_base, provider["artifact_path"])
    referenced = [
        artifact,
        safe_child(provider_base, provider["sbom"]["path"]),
        safe_child(provider_base, provider["provenance"]["path"]),
        safe_child(provider_base, provider["signature"]["bundle_path"]),
        safe_child(deployment_path.parent, deployment["artifact"]["signature"]["signature_filename"]),
    ]
    for path in referenced:
        copy_exact(path, output_dir, seen)
    if digest(artifact) != provider["artifact_sha256"] or artifact.stat().st_size != provider["artifact_size_bytes"]:
        raise RuntimeError("native artifact bytes differ from provider manifest")
    return (
        {
            "artifact_id": deployment["artifact_id"],
            "manifest_filename": deployment_copy.name,
            "manifest_sha256": digest(deployment_copy),
            "required": True,
        },
        artifact,
    )


def export_source(git_sha: str, destination: Path) -> None:
    completed = subprocess.run(
        ["git", "archive", "--format=tar", git_sha],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=120,
    )
    if completed.returncode:
        raise RuntimeError(completed.stderr.decode("utf-8", errors="replace")[-4096:])
    with tarfile.open(fileobj=io.BytesIO(completed.stdout), mode="r:") as archive:
        members = archive.getmembers()
        for member in members:
            path = PurePosixPath(member.name)
            if path.is_absolute() or ".." in path.parts or member.issym() or member.islnk() or member.isdev() or member.isfifo():
                raise RuntimeError(f"unsafe source archive member: {member.name}")
        for member in members:
            target = destination.joinpath(*PurePosixPath(member.name).parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            if not member.isfile():
                raise RuntimeError(f"unsupported source archive member: {member.name}")
            target.parent.mkdir(parents=True, exist_ok=True)
            stream = archive.extractfile(member)
            if stream is None:
                raise RuntimeError(f"cannot read source archive member: {member.name}")
            target.write_bytes(stream.read())
            os.chmod(target, member.mode & 0o777)


def build_release(git_sha: str, target_triple: str, native_artifact: Path, workspace: Path, build_jobs: int) -> Path:
    source = workspace / "source"
    source.mkdir()
    export_source(git_sha, source)
    native_target = source / "native/elixir/priv/native/libliqi_sequence_diff_nif.so"
    native_target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(native_artifact, native_target)
    os.chmod(native_target, 0o755)

    epoch = run(["git", "show", "-s", "--format=%ct", git_sha], capture=True)
    env = os.environ.copy()
    env.update(
        {
            "MIX_ENV": "prod",
            "MIX_HOME": str(workspace / "mix-home"),
            "HEX_HOME": str(workspace / "hex-home"),
            "HOME": str(workspace / "home"),
            "SOURCE_DATE_EPOCH": epoch,
            "ERL_FLAGS": "+S 2:2 +SDcpu 1 +SDio 1 +A 1",
            "MAKEFLAGS": f"-j{build_jobs}",
        }
    )
    for path in (Path(env["MIX_HOME"]), Path(env["HEX_HOME"]), Path(env["HOME"])):
        path.mkdir(parents=True, exist_ok=True)
    run(["mix", "local.hex", "--force"], cwd=source, env=env, timeout=300)
    run(["mix", "local.rebar", "--force"], cwd=source, env=env, timeout=300)
    run(["mix", "deps.get", "--only", "prod", "--locked"], cwd=source, env=env, timeout=600)
    run(["mix", "deps.compile"], cwd=source, env=env, timeout=900)
    run(["mix", "compile", "--warnings-as-errors"], cwd=source, env=env, timeout=900)
    run(["mix", "release", "liqi_platform", "--overwrite"], cwd=source, env=env, timeout=900)
    release = source / "_build/prod/rel/liqi_platform"
    binary = release / "bin/liqi_platform"
    release_nif = release / NIF_INSTALL
    if not binary.is_file() or not os.access(binary, os.X_OK):
        raise RuntimeError("Mix release executable is missing")
    if not release_nif.is_file() or digest(release_nif) != digest(native_artifact):
        raise RuntimeError("Mix release does not contain the exact verified native artifact")
    expected_machine = TARGETS[target_triple]["elf_machine"]
    header = next(release.rglob("erts-*/bin/beam.smp")).read_bytes()[:20]
    if len(header) < 20 or header[:4] != b"\x7fELF" or int.from_bytes(header[18:20], "little") != expected_machine:
        raise RuntimeError("Mix release ERTS architecture differs from target triple")
    return release


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--release-id", required=True)
    parser.add_argument("--target-triple", required=True, choices=tuple(TARGETS))
    parser.add_argument("--native-provider-manifest", required=True, type=Path)
    parser.add_argument("--native-deployment-manifest", required=True, type=Path)
    parser.add_argument("--native-trust-dir", required=True, type=Path)
    parser.add_argument("--artifact-key-id", required=True)
    parser.add_argument("--artifact-signing-key", required=True, type=Path)
    parser.add_argument("--manifest-key-id", required=True)
    parser.add_argument("--manifest-signing-key", required=True, type=Path)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--first-release", action="store_true")
    mode.add_argument("--rollback-target-release-id")
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--build-jobs", type=int, default=2)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not IDENTIFIER.fullmatch(args.release_id):
        raise SystemExit("release ID must be a stable lowercase identifier")
    if args.rollback_target_release_id is not None:
        if not IDENTIFIER.fullmatch(args.rollback_target_release_id) or not args.rollback_target_release_id.startswith("liqi-v1-"):
            raise SystemExit("upgrade rollback target must be a stable liqi-v1 release ID")
        if args.rollback_target_release_id == args.release_id:
            raise SystemExit("upgrade rollback target must differ from the new release")
    if not KEY_ID.fullmatch(args.artifact_key_id) or not KEY_ID.fullmatch(args.manifest_key_id):
        raise SystemExit("invalid release signing key ID")
    if args.artifact_key_id == args.manifest_key_id:
        raise SystemExit("artifact and manifest signing key IDs must be distinct")
    if public_key_fingerprint(args.artifact_signing_key) == public_key_fingerprint(args.manifest_signing_key):
        raise SystemExit("artifact and manifest signing public keys must be distinct")
    if args.build_jobs not in (1, 2):
        raise SystemExit("--build-jobs must be 1 or 2")
    for path, label in (
        (args.native_provider_manifest, "native provider manifest"),
        (args.native_deployment_manifest, "native deployment manifest"),
        (args.native_trust_dir, "native trust directory"),
        (args.artifact_signing_key, "artifact signing key"),
        (args.manifest_signing_key, "manifest signing key"),
    ):
        if not path.exists() or path.is_symlink():
            raise SystemExit(f"{label} is missing or is a symlink")

    git_sha = exact_source()
    verify_toolchain(args.target_triple)
    final_output = args.output_dir.resolve()
    try:
        final_output.relative_to(ROOT.resolve())
    except ValueError:
        pass
    else:
        raise SystemExit("--output-dir must be outside the source repository")
    if final_output.exists():
        raise SystemExit("--output-dir must not already exist")
    final_output.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="liqi-linux-release-", dir=final_output.parent) as temporary:
        root = Path(temporary)
        staged_output = root / "output"
        staged_output.mkdir(mode=0o750)
        seen: dict[str, str] = {}
        native_reference, native_artifact = prepare_native(
            args.native_provider_manifest,
            args.native_deployment_manifest,
            args.native_trust_dir,
            git_sha,
            args.target_triple,
            staged_output,
            seen,
        )
        build_workspace = root / "build"
        build_workspace.mkdir()
        release = build_release(git_sha, args.target_triple, native_artifact, build_workspace, args.build_jobs)

        archive_name = f"{args.release_id}.tar.gz"
        archive = staged_output / archive_name
        deterministic_archive(release, archive)
        artifact_signature = staged_output / f"{archive_name}.sig"
        sign(archive, args.artifact_signing_key, artifact_signature)

        manifest_name = f"{args.release_id}.json"
        manifest_signature_name = f"{manifest_name}.sig"
        created_at = run(["git", "show", "-s", "--format=%cI", git_sha], capture=True)
        manifest = {
            "schema_version": "liqi.deployment.mix-release/v1",
            "release_id": args.release_id,
            "release_name": "liqi_platform",
            "version": "1.0.0-dev",
            "git_sha": git_sha,
            "target_triple": args.target_triple,
            "artifact": {
                "filename": archive.name,
                "size_bytes": archive.stat().st_size,
                "sha256": digest(archive),
                "signature": {
                    "algorithm": "ed25519",
                    "key_id": args.artifact_key_id,
                    "signature_filename": artifact_signature.name,
                    "signature_sha256": digest(artifact_signature),
                    "signed_payload": "artifact-bytes",
                },
            },
            "runtime": {
                "otp_release": "28",
                "elixir_version": "1.20.2",
                "erts_included": True,
                "commands": {
                    "start": ["bin/liqi_platform", "start"],
                    "stop": ["bin/liqi_platform", "stop"],
                    "drain": ["bin/liqi-drain"],
                    "health": ["bin/liqi-health"],
                },
                "health_timeout_seconds": 60,
                "drain_timeout_seconds": 60,
            },
            "configuration": {
                "runtime_directory": "/etc/liqi/runtime",
                "secret_reference_directory": "/etc/liqi/secrets",
                "plaintext_secrets_forbidden": True,
            },
            "database_compatibility": {
                "minimum_migration": 8,
                "maximum_migration": 8,
                "rollback_safe_through": 8,
            },
            "native_artifacts": [native_reference],
            "installation": {
                "release_directory": f"/opt/liqi/releases/{args.release_id}",
                "current_symlink": "/opt/liqi/current",
                "immutable_after_install": True,
                "build_on_host": False,
            },
            "created_at": created_at,
            "manifest_signature": {
                "algorithm": "ed25519",
                "key_id": args.manifest_key_id,
                "signature_filename": manifest_signature_name,
                "signed_payload": "exact-manifest-bytes",
            },
            "rollback_target_release_id": args.rollback_target_release_id,
        }
        validate(MIX_SCHEMA, manifest, "Mix release manifest")
        manifest_path = staged_output / manifest_name
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
        os.chmod(manifest_path, 0o640)
        manifest_signature = staged_output / manifest_signature_name
        sign(manifest_path, args.manifest_signing_key, manifest_signature)

        trust_dir = root / "release-trust"
        trust_dir.mkdir(mode=0o700)
        derive_public(args.artifact_signing_key, args.artifact_key_id, trust_dir)
        derive_public(args.manifest_signing_key, args.manifest_key_id, trust_dir)
        runtime_result = staged_output / f"{args.release_id}.runtime-artifact-result-v1.json"
        run(
            [
                sys.executable, str(RELEASE_VERIFIER),
                "--manifest", str(manifest_path),
                "--output", str(runtime_result),
                "--trust-dir", str(trust_dir),
            ],
            timeout=900,
        )
        result_document = load(runtime_result)
        if result_document.get("status") != "passed" or result_document.get("blockers"):
            raise RuntimeError("self-verification did not pass")
        os.chmod(runtime_result, 0o640)

        native_provider_copy = staged_output / args.native_provider_manifest.resolve().name
        native_deployment_copy = staged_output / args.native_deployment_manifest.resolve().name
        build_result_name = f"{args.release_id}.linux-release-build-result-v1.json"
        result = {
            "schema_version": "liqi.runtime.linux-release-build-result/v1",
            "release_id": args.release_id,
            "git_sha": git_sha,
            "target_triple": args.target_triple,
            "status": "passed",
            "build_on_host": False,
            "signing_mode": "sigstore-keyless-native-plus-ed25519-release",
            "manifest": {"filename": manifest_path.name, "sha256": digest(manifest_path)},
            "artifact": {"filename": archive.name, "sha256": digest(archive), "size_bytes": archive.stat().st_size},
            "artifact_signature": {"filename": artifact_signature.name, "sha256": digest(artifact_signature)},
            "manifest_signature": {"filename": manifest_signature.name, "sha256": digest(manifest_signature)},
            "runtime_artifact_result": {"filename": runtime_result.name, "sha256": digest(runtime_result), "status": result_document["status"]},
            "native_provider_manifest": {"filename": native_provider_copy.name, "sha256": digest(native_provider_copy)},
            "native_deployment_manifest": {"filename": native_deployment_copy.name, "sha256": digest(native_deployment_copy)},
            "created_at": created_at,
        }
        validate(BUILD_RESULT_SCHEMA, result, "Linux release build result")
        build_result = staged_output / build_result_name
        build_result.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
        os.chmod(build_result, 0o640)

        os.replace(staged_output, final_output)

    print(json.dumps(load(final_output / build_result_name), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
