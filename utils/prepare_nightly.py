#!/usr/bin/env python3
"""Give an ephemeral main-branch build a unique PEP 440 development version."""

from __future__ import annotations

import argparse
import re
from pathlib import Path


PROJECT_VERSION = re.compile(
    r'(?ms)^(\[project\].*?^version = ")([^"]+)("\s*$)'
)
RELEASE_CORE = re.compile(r"^(\d+\.\d+\.\d+)")


def prepare(path: Path, run_number: int) -> str:
    contents = path.read_text(encoding="utf-8")
    match = PROJECT_VERSION.search(contents)
    if match is None:
        raise ValueError(f"project version not found in {path}")
    core = RELEASE_CORE.match(match.group(2))
    if core is None:
        raise ValueError(f"release core not found in {match.group(2)!r}")
    version = f"{core.group(1)}.dev{run_number}"
    path.write_text(
        PROJECT_VERSION.sub(rf"\g<1>{version}\g<3>", contents, count=1),
        encoding="utf-8",
    )
    return version


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-number", required=True, type=int)
    parser.add_argument("--path", type=Path, default=Path("pyproject.toml"))
    args = parser.parse_args()
    print(prepare(args.path, args.run_number))


if __name__ == "__main__":
    main()
