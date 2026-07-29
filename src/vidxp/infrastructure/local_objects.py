from __future__ import annotations

import os
from pathlib import Path

from vidxp.core.manifest import sha256_file
from vidxp.infrastructure.local_files import (
    durable_replace,
    durable_unlink,
    prepare_managed_destination,
    resolve_managed_file,
)


class LocalObjectStore:
    """Shared confined-file lifecycle for local media and artifacts."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def publish(
        self,
        source: Path,
        storage_key: str,
        *,
        expected_sha256: str | None,
        replace_corrupt: bool,
    ) -> tuple[Path, str, int]:
        if not source.is_file() or source.stat().st_size <= 0:
            raise FileNotFoundError("The staged object was not created.")
        with source.open("r+b") as handle:
            os.fsync(handle.fileno())
        checksum = sha256_file(source)
        if expected_sha256 is not None and checksum != expected_sha256:
            raise RuntimeError("The staged object checksum changed.")

        destination = prepare_managed_destination(self.root, storage_key)
        created = not destination.exists()
        published = False
        try:
            if destination.exists():
                if sha256_file(destination) == checksum:
                    durable_unlink(source)
                elif replace_corrupt:
                    durable_replace(source, destination)
                    published = True
                else:
                    raise FileExistsError(
                        "The managed object destination already exists."
                    )
            else:
                durable_replace(source, destination)
                published = True
            resolved = destination.resolve(strict=True)
            byte_size = resolved.stat().st_size
            return resolved, checksum, byte_size
        except BaseException:
            if created and published:
                durable_unlink(destination, missing_ok=True)
            raise

    def verify(
        self,
        storage_key: str,
        *,
        sha256: str,
        byte_size: int,
    ) -> Path:
        path = self.resolve(storage_key)
        if path.stat().st_size != byte_size:
            raise RuntimeError("Managed object has an unexpected byte size.")
        if sha256_file(path) != sha256:
            raise RuntimeError("Managed object has an unexpected checksum.")
        return path

    def delete(self, storage_key: str) -> None:
        durable_unlink(self.resolve(storage_key))

    def resolve(self, storage_key: str) -> Path:
        return resolve_managed_file(self.root, storage_key)
