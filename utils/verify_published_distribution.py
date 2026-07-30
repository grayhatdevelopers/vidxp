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


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", required=True)
    parser.add_argument("--package", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--dist", type=Path, default=Path("dist"))
    args = parser.parse_args()

    local = {
        path.name: sha256(path)
        for path in args.dist.iterdir()
        if path.is_file()
    }
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

    remote = {
        file["filename"]: file["digests"]["sha256"]
        for file in payload.get("urls", [])
    }
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
