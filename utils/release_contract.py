#!/usr/bin/env python3
"""Validate and report the repository's combined release version contract."""

from __future__ import annotations

import argparse
import json
import re
import sys
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STABLE_VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
BETA_VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+-b(?:\.[0-9]+)?$")


def _json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def version_sources(root: Path = ROOT) -> dict[str, str]:
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    desktop = root / "desktop"
    package = _json(desktop / "package.json")
    package_lock = _json(desktop / "package-lock.json")
    runtime = _json(desktop / "runtime-manifest.json")
    tauri = _json(desktop / "src-tauri" / "tauri.conf.json")
    cargo = tomllib.loads(
        (desktop / "src-tauri" / "Cargo.toml").read_text(encoding="utf-8")
    )
    return {
        "pyproject.toml": project["project"]["version"],
        "desktop/package.json": package["version"],
        "desktop/package-lock.json": package_lock["version"],
        "desktop/package-lock.json root": package_lock["packages"][""]["version"],
        "desktop/runtime-manifest.json desktop": runtime["desktop_version"],
        "desktop/runtime-manifest.json package": runtime["package_version"],
        "desktop/src-tauri/Cargo.toml": cargo["package"]["version"],
        "desktop/src-tauri/tauri.conf.json": tauri["version"],
    }


def validate(channel: str, expected_tag: str | None, root: Path = ROOT) -> str:
    sources = version_sources(root)
    versions = set(sources.values())
    if len(versions) != 1:
        detail = ", ".join(f"{source}={version}" for source, version in sources.items())
        raise ValueError(f"release version sources disagree: {detail}")
    version = versions.pop()
    pattern = STABLE_VERSION if channel == "stable" else BETA_VERSION
    if not pattern.fullmatch(version):
        raise ValueError(f"{version!r} is not a valid {channel} release version")
    tag = f"v{version}"
    if expected_tag is not None and tag != expected_tag:
        raise ValueError(f"tag {expected_tag!r} does not match repository version {version!r}")

    runtime = _json(root / "desktop" / "runtime-manifest.json")
    if runtime["dependency_index"] != "https://pypi.org/simple":
        raise ValueError("desktop releases must install VidXP from the public PyPI index")
    return version


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--channel", choices=("beta", "stable"), required=True)
    parser.add_argument("--expected-tag")
    parser.add_argument("--github-output", action="store_true")
    args = parser.parse_args()
    try:
        version = validate(args.channel, args.expected_tag)
    except (KeyError, OSError, TypeError, ValueError) as error:
        print(f"release contract invalid: {error}", file=sys.stderr)
        return 1

    values = {"channel": args.channel, "tag": f"v{version}", "version": version}
    if args.github_output:
        for key, value in values.items():
            print(f"{key}={value}")
    else:
        print(json.dumps(values, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
