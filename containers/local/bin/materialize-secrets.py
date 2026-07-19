#!/usr/bin/env python3
"""Create or reuse local-only container secrets without printing their values."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
from datetime import datetime, timezone
from pathlib import Path

NAMES = {
    "endpoint_secret": 128,
    "probe_token": 64,
    "drain_token": 64,
}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def valid(path: Path, length: int) -> bool:
    if not path.is_file() or path.is_symlink():
        return False
    value = path.read_text(encoding="ascii").strip()
    return len(value) == length and all(character in "0123456789abcdef" for character in value)


def write_secret(path: Path, length: int) -> None:
    temporary = path.with_name(f".{path.name}.new.{os.getpid()}")
    with temporary.open("x", encoding="ascii", newline="\n") as stream:
        stream.write(secrets.token_hex(length // 2) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-dir", required=True, type=Path)
    parser.add_argument("--rotate", action="store_true")
    args = parser.parse_args()

    state_dir = args.state_dir.resolve()
    secrets_dir = state_dir / "secrets"
    if state_dir.is_symlink() or secrets_dir.is_symlink():
        raise SystemExit("state directories must not be symbolic links")
    secrets_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(secrets_dir, 0o700)

    created: list[str] = []
    reused: list[str] = []
    for name, length in NAMES.items():
        path = secrets_dir / name
        if valid(path, length) and not args.rotate:
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
            name: {"sha256": digest(secrets_dir / name), "bytes": (secrets_dir / name).stat().st_size}
            for name in sorted(NAMES)
        },
    }
    manifest = state_dir / "secrets-manifest.json"
    temporary = manifest.with_name(f".{manifest.name}.new.{os.getpid()}")
    temporary.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    os.chmod(temporary, 0o600)
    os.replace(temporary, manifest)
    print(json.dumps({"status": "passed", "created": created, "reused": reused, "manifest": str(manifest)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
