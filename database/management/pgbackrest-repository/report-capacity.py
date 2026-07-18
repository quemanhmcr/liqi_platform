#!/usr/bin/env python3
from __future__ import annotations
import argparse,hashlib,json,os,tempfile
from datetime import datetime,timezone
from pathlib import Path


def atomic_write(path: Path, content: bytes, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--git-sha", required=True)
    parser.add_argument("--repository-root", type=Path, default=Path("/independent-storage/pgbackrest/liqi"))
    parser.add_argument("--reserved-bytes", type=int, default=20 * 1024**3)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if len(args.git_sha) != 40 or any(character not in "0123456789abcdef" for character in args.git_sha):
        raise SystemExit("git SHA must be lowercase 40 hex")
    root = args.repository_root.resolve(strict=True)
    if str(root) != "/independent-storage/pgbackrest/liqi" or root.is_symlink() or not root.is_dir():
        raise SystemExit("repository root is not the approved independent path")
    values = os.statvfs(root)
    total = values.f_blocks * values.f_frsize
    available = values.f_bavail * values.f_frsize
    used = total - values.f_bfree * values.f_frsize
    if args.reserved_bytes < 1024**3 or args.reserved_bytes >= total:
        raise SystemExit("reserved bytes are invalid")
    document = {
        "schema_version": "liqi.database.backup-repository-capacity/v1",
        "git_sha": args.git_sha,
        "observed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "authority": "independent-management-storage",
        "repository_ref": "management://database-backup-repository",
        "filesystem": {
            "path": str(root),
            "total_bytes": total,
            "used_bytes": used,
            "available_bytes": available,
            "reserved_bytes": args.reserved_bytes,
        },
        "transport": {"kind": "mutual-tls-over-wireguard", "port": 8432, "publicly_exposed": False},
        "status": "passed",
    }
    encoded = (json.dumps(document, indent=2, sort_keys=True) + "\n").encode("utf-8")
    atomic_write(args.output, encoded, 0o440)
    checksum = args.output.with_suffix(args.output.suffix + ".sha256")
    checksum_content = f"{hashlib.sha256(encoded).hexdigest()}  {args.output.name}\n".encode("utf-8")
    atomic_write(checksum, checksum_content, 0o440)
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
