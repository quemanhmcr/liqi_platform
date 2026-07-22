"""Fail-closed ELF symbol-version checks for the Oracle Linux 9 runtime baseline."""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Iterable

# Exact symbol ceilings observed on the production Oracle Linux Server 9.7 image.
# A release may require an older/equal symbol version, never a newer one.
EL9_MAXIMUMS: dict[str, tuple[int, ...]] = {
    "GLIBC": (2, 35),
    "GLIBCXX": (3, 4, 29),
    "CXXABI": (1, 3, 13),
    "GCC": (7, 0, 0),
}
VERSION_REQUIREMENT = re.compile(
    r"\b(GLIBCXX|GLIBC|CXXABI|GCC)_([0-9]+(?:\.[0-9]+)*)\b"
)
VERSION_SECTION = re.compile(r"^Version .+ section ")


def version_tuple(value: str) -> tuple[int, ...]:
    return tuple(int(part) for part in value.split("."))


def version_text(value: tuple[int, ...]) -> str:
    return ".".join(str(part) for part in value)


def version_exceeds(required: tuple[int, ...], maximum: tuple[int, ...]) -> bool:
    width = max(len(required), len(maximum))
    return required + (0,) * (width - len(required)) > maximum + (0,) * (width - len(maximum))


def parse_requirements(output: str) -> dict[str, tuple[int, ...]]:
    """Parse only .gnu.version_r requirements, never versions defined by the ELF itself."""
    requirements: dict[str, tuple[int, ...]] = {}
    in_needs_section = False
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if line.startswith("Version needs section "):
            in_needs_section = True
            continue
        if VERSION_SECTION.match(line):
            in_needs_section = False
            continue
        if not in_needs_section:
            continue
        for family, raw_version in VERSION_REQUIREMENT.findall(line):
            version = version_tuple(raw_version)
            previous = requirements.get(family)
            if previous is None or version_exceeds(version, previous):
                requirements[family] = version
    return requirements


def policy_violations(
    files: dict[str, dict[str, tuple[int, ...]]],
    maximums: dict[str, tuple[int, ...]] = EL9_MAXIMUMS,
) -> list[str]:
    violations: list[str] = []
    for label in sorted(files):
        for family, required in sorted(files[label].items()):
            maximum = maximums.get(family)
            if maximum is not None and version_exceeds(required, maximum):
                violations.append(
                    f"{label} requires {family}_{version_text(required)} "
                    f"but EL9 permits at most {family}_{version_text(maximum)}"
                )
    return violations


def scan_elf_abi(files: Iterable[tuple[str, Path]]) -> dict[str, object]:
    readelf = shutil.which("readelf")
    if readelf is None:
        raise RuntimeError("readelf is required for EL9 ABI verification")

    requirements_by_file: dict[str, dict[str, tuple[int, ...]]] = {}
    elf_count = 0
    maximum_requirements: dict[str, tuple[int, ...]] = {}
    for label, path in files:
        if not path.is_file() or path.is_symlink():
            continue
        with path.open("rb") as stream:
            if stream.read(4) != b"\x7fELF":
                continue
        elf_count += 1
        completed = subprocess.run(
            [readelf, "--version-info", "--wide", str(path)],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=30,
        )
        if completed.returncode:
            detail = (completed.stderr or completed.stdout).strip()[-1024:]
            raise RuntimeError(f"cannot inspect ELF ABI for {label}: {detail}")
        requirements = parse_requirements(completed.stdout)
        requirements_by_file[label] = requirements
        for family, required in requirements.items():
            previous = maximum_requirements.get(family)
            if previous is None or version_exceeds(required, previous):
                maximum_requirements[family] = required

    if elf_count == 0:
        raise RuntimeError("release contains no ELF files for ABI verification")
    violations = policy_violations(requirements_by_file)
    return {
        "elf_count": elf_count,
        "maximum_requirements": {
            family: version_text(version)
            for family, version in sorted(maximum_requirements.items())
        },
        "violations": violations,
    }
