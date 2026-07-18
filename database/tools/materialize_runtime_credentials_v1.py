#!/usr/bin/env python3
from __future__ import annotations

import argparse, base64, datetime as dt, hashlib, hmac, json, os, re, secrets, stat, subprocess, tempfile
from pathlib import Path

SCHEMA_VERSION = "database-host-bootstrap-v1"
ITERATIONS = 4096
SALT_BYTES = 16
POSTGRES_ROLES = ("liqi_migrator", "liqi_api", "liqi_realtime", "liqi_worker", "liqi_readonly", "liqi_monitor", "liqi_backup")
PGBOUNCER_ROLES = ("liqi_api", "liqi_realtime", "liqi_worker", "liqi_readonly", "liqi_monitor")
CREDENTIAL_NAMES = {role: f"database-{role.replace('_', '-')}-password" for role in POSTGRES_ROLES}
SCRAM_RE = re.compile(r"^SCRAM-SHA-256\$(?P<i>[0-9]+):(?P<s>[A-Za-z0-9+/]+={0,2})\$(?P<k>[A-Za-z0-9+/]+={0,2}):(?P<v>[A-Za-z0-9+/]+={0,2})$")
REVISION_RE = re.compile(r"^[0-9a-f]{40}$")

class BootstrapError(RuntimeError):
    pass

def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--result", required=True, type=Path)
    return parser.parse_args()

def read_credential(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise BootstrapError(f"credential is not a regular file: {path.name}")
    if stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise BootstrapError(f"credential permissions are too broad: {path.name}")
    raw = path.read_bytes()
    raw = raw[:-2] if raw.endswith(b"\r\n") else raw[:-1] if raw.endswith(b"\n") else raw
    if b"\x00" in raw or b"\r" in raw or b"\n" in raw:
        raise BootstrapError(f"credential must be a single UTF-8 line: {path.name}")
    if not 32 <= len(raw) <= 256:
        raise BootstrapError(f"credential length must be 32..256 bytes: {path.name}")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise BootstrapError(f"credential must be UTF-8: {path.name}") from exc

def scram_components(value: str, salt: bytes, iterations: int):
    salted = hashlib.pbkdf2_hmac("sha256", value.encode(), salt, iterations)
    client = hmac.new(salted, b"Client Key", hashlib.sha256).digest()
    return hashlib.sha256(client).digest(), hmac.new(salted, b"Server Key", hashlib.sha256).digest()

def parse_scram(value: str):
    match = SCRAM_RE.fullmatch(value)
    if not match:
        return None
    try:
        return int(match["i"]), base64.b64decode(match["s"], validate=True), base64.b64decode(match["k"], validate=True), base64.b64decode(match["v"], validate=True)
    except (ValueError, base64.binascii.Error):
        return None

def verifier_matches(verifier: str, credential: str) -> bool:
    parsed = parse_scram(verifier)
    if parsed is None:
        return False
    iterations, salt, stored, server = parsed
    actual_stored, actual_server = scram_components(credential, salt, iterations)
    return hmac.compare_digest(stored, actual_stored) and hmac.compare_digest(server, actual_server)

def make_verifier(credential: str) -> str:
    salt = secrets.token_bytes(SALT_BYTES)
    stored, server = scram_components(credential, salt, ITERATIONS)
    return f"SCRAM-SHA-256${ITERATIONS}:{base64.b64encode(salt).decode()}${base64.b64encode(stored).decode()}:{base64.b64encode(server).decode()}"
