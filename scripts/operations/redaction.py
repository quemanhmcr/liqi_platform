"""Shared bounded redaction for operational command evidence."""
from __future__ import annotations
import re
PRIVATE_KEY_BLOCK = re.compile(r"-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----", re.DOTALL)
SECRET_ASSIGNMENT = re.compile(r"(?i)(password|passwd|secret|token|authorization|private[_-]?key)\s*[:=]\s*([^\s,;]+)")
BEARER = re.compile(r"(?i)Bearer\s+[A-Za-z0-9._~+/-]+=*")

def redact(text: str) -> str:
    text = PRIVATE_KEY_BLOCK.sub("[REDACTED_PRIVATE_KEY]", text)
    text = BEARER.sub("Bearer [REDACTED]", text)
    return SECRET_ASSIGNMENT.sub(lambda match: f"{match.group(1)}=[REDACTED]", text)
