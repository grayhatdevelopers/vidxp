#!/usr/bin/env python3
"""Normalize an sdist archive without relying on platform-specific tar flags."""

from __future__ import annotations

import argparse
import copy
import gzip
import os
from pathlib import Path
import tarfile
import tempfile


def normalize_sdist(path: Path, source_date_epoch: int) -> None:
    """Rewrite *path* with stable member ordering and ownership metadata."""
    if source_date_epoch < 0:
        raise ValueError("SOURCE_DATE_EPOCH must be non-negative")

    path = path.resolve()
    temporary_path: Path | None = None
    try:
        with tarfile.open(path, "r:gz") as source:
            members = sorted(source.getmembers(), key=lambda member: member.name)
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=path.parent,
                prefix=f".{path.name}.",
                suffix=".tmp",
                delete=False,
            ) as temporary:
                temporary_path = Path(temporary.name)
                with gzip.GzipFile(
                    filename="",
                    mode="wb",
                    compresslevel=9,
                    fileobj=temporary,
                    mtime=0,
                ) as compressed:
                    with tarfile.open(
                        fileobj=compressed,
                        mode="w",
                        format=tarfile.GNU_FORMAT,
                    ) as destination:
                        for original in members:
                            normalized = copy.copy(original)
                            normalized.uid = 0
                            normalized.gid = 0
                            normalized.uname = ""
                            normalized.gname = ""
                            normalized.mtime = source_date_epoch
                            normalized.pax_headers = {}
                            content = (
                                source.extractfile(original)
                                if original.isfile()
                                else None
                            )
                            try:
                                destination.addfile(normalized, content)
                            finally:
                                if content is not None:
                                    content.close()

        os.replace(temporary_path, path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("source_date_epoch", type=int)
    args = parser.parse_args()
    normalize_sdist(args.archive, args.source_date_epoch)


if __name__ == "__main__":
    main()
