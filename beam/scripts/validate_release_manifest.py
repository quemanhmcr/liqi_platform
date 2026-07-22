#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in os.sys.path:
    os.sys.path.insert(0, str(ROOT))

from beam.elf_abi import scan_elf_abi
SCHEMA = ROOT / "contracts/deployment/mix-release-v1.schema.json"
RESULT_SCHEMA = ROOT / "contracts/runtime/runtime-artifact-result-v1.schema.json"
REQUIRED = {"bin/liqi_platform", "bin/liqi-health", "bin/liqi-drain", "releases/start_erl.data"}
TARGETS = {
    "aarch64-unknown-linux-gnu": {"elf_machine": 183, "elf_name": "AArch64"},
    "x86_64-unknown-linux-gnu": {"elf_machine": 62, "elf_name": "x86-64"},
}


def elf_matches_target(header: bytes, target_triple: str) -> bool:
    expected = TARGETS[target_triple]
    return (
        len(header) >= 20
        and header[:4] == b"\x7fELF"
        and header[4] == 2
        and header[5] == 1
        and int.from_bytes(header[18:20], "little") == expected["elf_machine"]
    )


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def add_check(
    checks: list[dict[str, str]],
    blockers: list[str],
    name: str,
    status: str,
    detail: str | None = None,
) -> None:
    checks.append({"name": name, "status": status})
    if status != "passed" and detail:
        blockers.append(detail)


def trusted_key(trust_dir: Path, key_id: str) -> Path:
    key = trust_dir / f"{key_id}.pem"
    if not key.is_file():
        raise FileNotFoundError(f"trusted key is missing: {key}")
    return key


def verify_ed25519(payload: Path, signature: Path, key: Path) -> tuple[bool, str | None]:
    openssl = shutil.which("openssl")
    if not openssl:
        return False, "openssl is unavailable for Ed25519 verification"
    try:
        result = subprocess.run(
            [
                openssl,
                "pkeyutl",
                "-verify",
                "-rawin",
                "-pubin",
                "-inkey",
                str(key),
                "-in",
                str(payload),
                "-sigfile",
                str(signature),
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        return False, "Ed25519 verification exceeded 30 seconds"
    if result.returncode:
        return False, (result.stderr or result.stdout or "Ed25519 verification failed").strip()[:512]
    return True, None


def verify_signature(
    checks: list[dict[str, str]],
    blockers: list[str],
    name: str,
    payload: Path,
    signature: Path,
    key_id: str,
    trust_dir: Path | None,
) -> None:
    if not signature.is_file():
        add_check(checks, blockers, f"{name}-file-present", "failed", f"signature file missing: {signature.name}")
        return
    add_check(checks, blockers, f"{name}-file-present", "passed")
    if trust_dir is None:
        add_check(
            checks,
            blockers,
            f"{name}-verified",
            "blocked",
            "LIQI_RELEASE_TRUST_DIR or --trust-dir is required for signature verification",
        )
        return
    try:
        key = trusted_key(trust_dir, key_id)
    except FileNotFoundError as error:
        add_check(checks, blockers, f"{name}-verified", "blocked", str(error))
        return
    ok, detail = verify_ed25519(payload, signature, key)
    add_check(checks, blockers, f"{name}-verified", "passed" if ok else "failed", detail)


def archive_checks(
    artifact: Path,
    manifest: dict[str, Any],
    checks: list[dict[str, str]],
    blockers: list[str],
) -> str | None:
    if not artifact.is_file():
        add_check(checks, blockers, "artifact-present", "failed", f"artifact missing: {artifact.name}")
        return None
    add_check(checks, blockers, "artifact-present", "passed")
    size_matches = artifact.stat().st_size == manifest["artifact"]["size_bytes"]
    add_check(checks, blockers, "artifact-size", "passed" if size_matches else "failed", "artifact size mismatch")
    artifact_sha = digest(artifact)
    sha_matches = artifact_sha == manifest["artifact"]["sha256"]
    add_check(checks, blockers, "artifact-sha256", "passed" if sha_matches else "failed", "artifact sha256 mismatch")

    try:
        with tarfile.open(artifact, "r:gz") as archive:
            members = archive.getmembers()
            safe_paths = all(
                not Path(member.name).is_absolute()
                and ".." not in Path(member.name.replace("\\", "/")).parts
                for member in members
            )
            add_check(
                checks,
                blockers,
                "archive-path-safety",
                "passed" if safe_paths else "failed",
                "release archive contains an absolute or parent-traversal path",
            )
            bounded_members = len(members) <= 20_000
            add_check(
                checks,
                blockers,
                "archive-member-bound",
                "passed" if bounded_members else "failed",
                "release archive contains more than 20000 members",
            )
            names = {member.name.lstrip("./") for member in members}
            layout_ok = all(any(name == required or name.endswith("/" + required) for name in names) for required in REQUIRED)
            add_check(
                checks,
                blockers,
                "release-layout",
                "passed" if layout_ok else "failed",
                "release missing provider command or start_erl.data",
            )
            has_erts = any("/erts-" in "/" + name or name.startswith("erts-") for name in names)
            add_check(checks, blockers, "erts-included", "passed" if has_erts else "failed", "release does not include ERTS")
            beam_member = next(
                (
                    member
                    for member in members
                    if member.name.replace("\\", "/").endswith("/bin/beam.smp")
                    or member.name.replace("\\", "/") == "bin/beam.smp"
                ),
                None,
            )
            target_triple = manifest["target_triple"]
            target = TARGETS[target_triple]
            target_elf = False
            beam_shape_ok = False
            if beam_member is not None and beam_member.isfile():
                beam_shape_ok = beam_member.size >= 1_048_576 and bool(beam_member.mode & 0o111)
                stream = archive.extractfile(beam_member)
                header = stream.read(20) if stream else b""
                target_elf = elf_matches_target(header, target_triple)
            add_check(
                checks,
                blockers,
                "erts-executable-shape",
                "passed" if beam_shape_ok else "failed",
                "ERTS beam.smp must be a regular executable file of at least 1 MiB",
            )
            add_check(
                checks,
                blockers,
                "target-elf",
                "passed" if target_elf else "failed",
                f"ERTS beam.smp is not an ELF64 little-endian {target['elf_name']} binary for {target_triple}",
            )
            try:
                with tempfile.TemporaryDirectory(prefix="liqi-archive-abi-") as directory:
                    abi_root = Path(directory)
                    abi_files: list[tuple[str, Path]] = []
                    for index, member in enumerate(members):
                        if not member.isfile():
                            continue
                        stream = archive.extractfile(member)
                        if stream is None:
                            continue
                        header = stream.read(4)
                        if header != b"\x7fELF":
                            continue
                        extracted = abi_root / f"{index}.elf"
                        with extracted.open("wb") as output:
                            output.write(header)
                            shutil.copyfileobj(stream, output)
                        abi_files.append((member.name.lstrip("./"), extracted))
                    abi_report = scan_elf_abi(abi_files)
                abi_ok = not abi_report["violations"]
                abi_detail = "; ".join(abi_report["violations"][:16]) if not abi_ok else None
            except (OSError, RuntimeError, subprocess.SubprocessError) as error:
                abi_ok = False
                abi_detail = f"EL9 ABI inspection failed: {error}"
            add_check(
                checks,
                blockers,
                "el9-abi-compatibility",
                "passed" if abi_ok else "failed",
                abi_detail,
            )
            elixir_ok = any("/lib/elixir-1.20.2/" in "/" + name for name in names)
            add_check(checks, blockers, "elixir-runtime", "passed" if elixir_ok else "failed", "release does not contain Elixir 1.20.2")
            app_ok = any("/lib/liqi_platform-1.0.0-dev/" in "/" + name for name in names)
            add_check(checks, blockers, "application-version", "passed" if app_ok else "failed", "release does not contain liqi_platform 1.0.0-dev")
    except (tarfile.TarError, OSError) as error:
        add_check(checks, blockers, "release-archive-readable", "failed", f"invalid release archive: {error}")
    return artifact_sha


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--trust-dir",
        type=Path,
        default=Path(os.environ["LIQI_RELEASE_TRUST_DIR"]) if os.environ.get("LIQI_RELEASE_TRUST_DIR") else None,
    )
    args = parser.parse_args()

    checks: list[dict[str, str]] = []
    blockers: list[str] = []
    manifest: dict[str, Any] = {}
    artifact_sha: str | None = None
    try:
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
        schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
        errors = sorted(
            Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(manifest),
            key=lambda error: list(error.path),
        )
        add_check(
            checks,
            blockers,
            "manifest-schema",
            "passed" if not errors else "failed",
            "; ".join(error.message for error in errors[:8]) or None,
        )

        current_sha = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True, timeout=10).strip()
        exact_sha = manifest.get("git_sha") == current_sha
        add_check(checks, blockers, "exact-git-sha", "passed" if exact_sha else "failed", "manifest git_sha does not match current checkout")

        artifact = args.manifest.parent / manifest.get("artifact", {}).get("filename", "")
        artifact_sha = archive_checks(artifact, manifest, checks, blockers)

        artifact_signature_spec = manifest.get("artifact", {}).get("signature", {})
        artifact_signature = args.manifest.parent / artifact_signature_spec.get("signature_filename", "")
        if artifact_signature.is_file():
            signature_sha_ok = digest(artifact_signature) == artifact_signature_spec.get("signature_sha256")
            add_check(
                checks,
                blockers,
                "artifact-signature-sha256",
                "passed" if signature_sha_ok else "failed",
                "artifact signature checksum mismatch",
            )
        verify_signature(
            checks,
            blockers,
            "artifact-signature",
            artifact,
            artifact_signature,
            artifact_signature_spec.get("key_id", ""),
            args.trust_dir,
        )

        manifest_signature_spec = manifest.get("manifest_signature", {})
        manifest_signature = args.manifest.parent / manifest_signature_spec.get("signature_filename", "")
        verify_signature(
            checks,
            blockers,
            "manifest-signature",
            args.manifest,
            manifest_signature,
            manifest_signature_spec.get("key_id", ""),
            args.trust_dir,
        )

        runtime = manifest.get("runtime", {})
        runtime_ok = runtime.get("otp_release", "").startswith("28") and runtime.get("elixir_version") == "1.20.2"
        add_check(checks, blockers, "runtime-version", "passed" if runtime_ok else "failed", "runtime version differs from Senior 1 toolchain")
        commands = runtime.get("commands", {})
        loopback_ok = commands.get("health") == ["bin/liqi-health"] and commands.get("drain") == ["bin/liqi-drain"]
        add_check(checks, blockers, "loopback-control-commands", "passed" if loopback_ok else "failed", "health/drain must use release overlay commands")

        database = manifest.get("database_compatibility", {})
        database_ok = (
            database.get("minimum_migration") == 8
            and database.get("maximum_migration") == 8
            and database.get("rollback_safe_through") == 8
        )
        add_check(checks, blockers, "database-compatibility", "passed" if database_ok else "failed", "database compatibility must remain forward-only at migration 8")

        rollback = manifest.get("rollback_target_release_id")
        first_release = rollback is None
        upgrade = isinstance(rollback, str) and rollback.startswith("liqi-v1-") and rollback != manifest.get("release_id")
        add_check(
            checks, blockers, "release-transition",
            "passed" if first_release or upgrade else "failed",
            "release transition must be an explicit first release or a distinct retained V1 rollback target",
        )
    except Exception as error:  # evidence must still be emitted for unexpected verification failures
        add_check(checks, blockers, "verification-exception", "failed", f"{type(error).__name__}: {error}")

    statuses = {check["status"] for check in checks}
    status = "failed" if "failed" in statuses else "blocked" if "blocked" in statuses else "passed"
    result = {
        "schema_version": "runtime-artifact-result-v1",
        "git_sha": manifest.get("git_sha") or subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "release_id": manifest.get("release_id", "unknown"),
        "observed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "status": status,
        "artifact_sha256": artifact_sha,
        "checks": checks,
        "blockers": blockers,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8", newline="\n")

    result_schema = json.loads(RESULT_SCHEMA.read_text(encoding="utf-8"))
    result_errors = list(
        Draft202012Validator(result_schema, format_checker=FormatChecker()).iter_errors(result)
    )
    if result_errors:
        print("; ".join(error.message for error in result_errors), file=os.sys.stderr)
        return 65
    if status == "passed":
        return 0
    if status == "blocked":
        return 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
