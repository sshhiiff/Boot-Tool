"""Stop the build if anything installed has no exact version in build-constraints.txt.

Run by installer/pinned_env.sh right after installing, in the clean environment the tests
run in and the app is built from, so a package that sneaks in after an update can't change
a download unnoticed.
"""
from __future__ import annotations

import re
import sys
from importlib import metadata
from pathlib import Path

from packaging.version import InvalidVersion, Version

CONSTRAINTS = Path(__file__).resolve().parent / "build-constraints.txt"
OWN = {"boottool"}                          # Boot Tool itself, installed from this folder


def norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def read_pins(path: Path = CONSTRAINTS) -> dict[str, str]:
    pins = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#")[0].strip()
        if not line:
            continue
        m = re.fullmatch(r"([A-Za-z0-9][A-Za-z0-9._-]*)==([0-9][A-Za-z0-9.+!-]*)", line)
        if not m:
            raise ValueError(f"not an exact version: {line!r}")
        name = norm(m.group(1))
        if name in pins:
            raise ValueError(f"listed twice: {name}")
        pins[name] = m.group(2)
    return pins


def _same(a: str, b: str) -> bool:
    try:
        return Version(a) == Version(b)     # 2.5.5 and 2.5.5.0 are the same version
    except InvalidVersion:
        return a == b


def problems(installed: list[tuple[str, str]], pins: dict[str, str]) -> list[str]:
    """installed: (name, version) for every package found, duplicates included."""
    seen: dict[str, list[str]] = {}
    for name, version in installed:
        seen.setdefault(norm(name), []).append(version)
    out = []
    for name, versions in sorted(seen.items()):
        if name in OWN:
            continue
        if len(versions) > 1:
            out.append(f"{name} is installed more than once ({', '.join(sorted(versions))})")
        elif name not in pins:
            out.append(f"{name} {next(iter(versions))} has no fixed version in installer/build-constraints.txt")
        elif not _same(next(iter(versions)), pins[name]):
            out.append(f"{name} is {next(iter(versions))}, but build-constraints.txt says {pins[name]}")
    return out


def main() -> int:
    installed = []
    for d in metadata.distributions():
        name = d.metadata["Name"]
        if not name:
            print(f"A package without a name is installed at {getattr(d, '_path', '?')}")   # broken install
            return 1
        installed.append((name, d.version))
    found = problems(installed, read_pins())
    for p in found:
        print(p)
    if found:
        return 1
    others = len({norm(n) for n, _ in installed} - OWN)
    print(f"All {others} installed packages have the versions in build-constraints.txt.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
