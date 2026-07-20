#!/usr/bin/env python3
"""Create or reuse local-only container secrets without printing their values."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import stat
from datetime import datetime, timezone
from pathlib import Path

NAMES = {
    "endpoint_secret": 128,
    "probe_token": 64,
    "drain_token": 64,
}
SECRET_MODE = 0o640
RUNTIME_GID = 10001


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def valid(path: Path, length: int) -> bool:
    if not path.is_file() or path.is_symlink():
        return False
    value = path.read_text(encoding="ascii").strip()
    return len(value) == length and all(character in "0123456789abcdef" for character in value)


def reject_symlink_components(path: Path) -> None:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        if current.is_symlink():
            raise SystemExit(f"state directory path contains a symbolic link: {current}")


def secure_directory(path: Path, *, create: bool) -> None:
    if path.is_symlink():
        raise SystemExit(f"state directory must not be a symbolic link: {path}")
    if create:
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not path.is_dir():
        raise SystemExit(f"state directory is not a directory: {path}")
    mode = stat.S_IMODE(path.stat().st_mode)
    if os.name == "posix" and mode & 0o077:
        raise SystemExit(f"state directory must not grant group or other access: {path}")
    os.chmod(path, 0o700)


def require_runtime_group_assignment() -> None:
    if os.name != "posix" or os.geteuid() == 0:
        return
    if RUNTIME_GID not in {os.getegid(), *os.getgroups()}:
        raise SystemExit(
            f"local secret materialization requires permission to assign runtime GID {RUNTIME_GID}"
        )


def secure_secret(path: Path) -> None:
    if os.name == "posix":
        try:
            os.chown(path, -1, RUNTIME_GID)
        except PermissionError as error:
            raise SystemExit(
                f"local secret materialization requires permission to assign runtime GID {RUNTIME_GID}"
            ) from error
    os.chmod(path, SECRET_MODE)


def write_secret(path: Path, length: int) -> None:
    temporary = path.with_name(f".{path.name}.new.{os.getpid()}")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(temporary, flags, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="ascii", newline="\n") as stream:
            descriptor = -1
            stream.write(secrets.token_hex(length // 2) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        secure_secret(temporary)
        os.replace(temporary, path)
    except BaseException:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)
        raise


def write_manifest(path: Path, document: dict[str, object]) -> None:
    temporary = path.with_name(f".{path.name}.new.{os.getpid()}")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(temporary, flags, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            descriptor = -1
            stream.write(json.dumps(document, indent=2, sort_keys=True) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    except BaseException:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)
        raise


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-dir", required=True, type=Path)
    parser.add_argument("--rotate", action="store_true")
    args = parser.parse_args()

    state_dir = Path(os.path.abspath(args.state_dir))
    reject_symlink_components(state_dir)
    secure_directory(state_dir, create=True)
    secrets_dir = state_dir / "secrets"
    secure_directory(secrets_dir, create=True)
    require_runtime_group_assignment()

    created: list[str] = []
    reused: list[str] = []
    for name, length in NAMES.items():
        path = secrets_dir / name
        if valid(path, length) and not args.rotate:
            secure_secret(path)
            reused.append(name)
            continue
        if path.exists() and not args.rotate:
            raise SystemExit(f"existing local secret has an invalid shape: {path}")
        write_secret(path, length)
        created.append(name)

    document = {
        "schema_version": "liqi.local-container-secrets/v1",
        "observed_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "rotation_requested": args.rotate,
        "files": {
            name: {
                "sha256": digest(secrets_dir / name),
                "bytes": (secrets_dir / name).stat().st_size,
                "gid": (secrets_dir / name).stat().st_gid,
                "mode": f"{(secrets_dir / name).stat().st_mode & 0o777:04o}",
            }
            for name in sorted(NAMES)
        },
    }
    manifest = state_dir / "secrets-manifest.json"
    write_manifest(manifest, document)
    print(json.dumps({"status": "passed", "created": created, "reused": reused, "manifest": str(manifest)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
