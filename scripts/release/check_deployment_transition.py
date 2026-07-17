#!/usr/bin/env python3
"""Validate one V0 deployment state transition."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MACHINE = ROOT / "operations" / "deployment" / "deployment-state-machine-v0.json"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--from-state", required=True)
    parser.add_argument("--to-state", required=True)
    parser.add_argument("--machine", type=Path, default=DEFAULT_MACHINE)
    args = parser.parse_args()
    machine = json.loads(args.machine.read_text(encoding="utf-8"))
    allowed = machine["transitions"].get(args.from_state)
    if allowed is None:
        print(f"invalid source state: {args.from_state}")
        return 2
    if args.to_state not in allowed:
        print(f"transition denied: {args.from_state} -> {args.to_state}; allowed={allowed}")
        return 1
    print(f"transition allowed: {args.from_state} -> {args.to_state}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
