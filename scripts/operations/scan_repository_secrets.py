#!/usr/bin/env python3
"""Fail when Git-tracked source contains credential material or secret file types.

This source gate complements, rather than replaces, the pinned gitleaks CI gate.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[2]
SECRET_SUFFIXES = {".pem", ".p12", ".pfx", ".jks", ".keystore"}
SECRET_NAMES = {".env", "id_rsa", "id_ed25519", "credentials", "credentials.json"}
PRIVATE_KEY_MARKERS = (
    "-----BEGIN " + "PRIVATE KEY-----",
    "-----BEGIN RSA " + "PRIVATE KEY-----",
    "-----BEGIN EC " + "PRIVATE KEY-----",
    "-----BEGIN OPENSSH " + "PRIVATE KEY-----",
)
POSTGRES_PASSWORD_DSN = re.compile(r"postgres(?:ql)?://[^\s:/]+:[^\s@/]+@", re.IGNORECASE)
BEARER_TOKEN = re.compile(r"(?i)authorization\s*[:=]\s*bearer\s+[A-Za-z0-9._~+/-]{20,}=*")
GENERIC_SECRET = re.compile(
    r"(?i)\b(password|passwd|client_secret|access_token|refresh_token|private_key)\b\s*[:=]\s*[\"']?([^\s\"',;}{]{8,})"
)
SAFE_VALUE_PREFIXES = ("REDACTED", "[REDACTED]", "PLACEHOLDER", "TEST_ONLY_", "EXAMPLE_", "${", "<")


def tracked_paths() -> list[Path]:
    output = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT)
    return [ROOT / item.decode("utf-8") for item in output.split(b"\0") if item]


def scan_paths(paths: Iterable[Path]) -> list[str]:
    failures: list[str] = []
    for path in paths:
        relative = path.relative_to(ROOT).as_posix() if path.is_relative_to(ROOT) else path.as_posix()
        lowered_name = path.name.lower()
        if path.suffix.lower() in SECRET_SUFFIXES or lowered_name in SECRET_NAMES:
            failures.append(f"{relative}: tracked secret-bearing filename")
            continue
        try:
            raw = path.read_bytes()
        except OSError as exc:
            failures.append(f"{relative}: cannot read tracked file: {exc}")
            continue
        if b"\0" in raw:
            continue
        text = raw.decode("utf-8", errors="replace")
        for marker in PRIVATE_KEY_MARKERS:
            if marker in text:
                failures.append(f"{relative}: private key material detected")
                break
        for line_number, line in enumerate(text.splitlines(), start=1):
            if POSTGRES_PASSWORD_DSN.search(line):
                failures.append(f"{relative}:{line_number}: credential-bearing PostgreSQL DSN")
            if BEARER_TOKEN.search(line):
                failures.append(f"{relative}:{line_number}: hard-coded bearer token")
            match = GENERIC_SECRET.search(line)
            if match and not match.group(2).upper().startswith(SAFE_VALUE_PREFIXES):
                failures.append(f"{relative}:{line_number}: hard-coded {match.group(1).lower()} value")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="*", type=Path)
    args = parser.parse_args()
    paths = [path.resolve() for path in args.paths] if args.paths else tracked_paths()
    failures = scan_paths(paths)
    if failures:
        for failure in failures:
            print(f"ERROR secret-scan: {failure}", file=sys.stderr)
        return 1
    print(f"secret scan passed for {len(paths)} tracked/source files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
