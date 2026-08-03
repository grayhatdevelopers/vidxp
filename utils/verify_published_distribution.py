#!/usr/bin/env python3
"""Verify whether a Python distribution version is absent or byte-identical."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import urlopen


_PUBLISH_ATTESTATION_SUFFIX = ".publish.attestation"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def is_distribution_file(filename: str) -> bool:
    return not filename.endswith(_PUBLISH_ATTESTATION_SUFFIX)


def local_distribution_files(directory: Path) -> dict[str, str]:
    return {
        path.name: sha256(path)
        for path in directory.iterdir()
        if path.is_file() and is_distribution_file(path.name)
    }


def distribution_files(payload: dict[str, object]) -> dict[str, str]:
    return {
        file["filename"]: file["digests"]["sha256"]
        for file in payload.get("urls", [])
        if is_distribution_file(file["filename"])
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", required=True)
    parser.add_argument("--package", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--dist", type=Path, default=Path("dist"))
    args = parser.parse_args()

    local = local_distribution_files(args.dist)
    if not local:
        raise SystemExit(f"No distribution files found in {args.dist}")

    url = (
        f"{args.repository.rstrip('/')}/pypi/"
        f"{quote(args.package)}/{quote(args.version)}/json"
    )
    try:
        with urlopen(url, timeout=30) as response:  # noqa: S310
            payload = json.load(response)
    except HTTPError as error:
        if error.code == 404:
            print("absent")
            return 0
        raise

    remote = distribution_files(payload)
    if local == remote:
        print("identical")
        return 0

    missing = sorted(local.keys() - remote.keys())
    extra = sorted(remote.keys() - local.keys())
    changed = sorted(
        filename
        for filename in local.keys() & remote.keys()
        if local[filename] != remote[filename]
    )
    print(
        "Published distribution conflicts with this tag: "
        f"missing={missing}, extra={extra}, changed={changed}",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
