from __future__ import annotations

from pathlib import Path, PurePosixPath

from vidxp.core.manifest import sync_parent_directory
from vidxp.core.storage_keys import validate_storage_key


def resolve_managed_file(root: Path, storage_key: str) -> Path:
    """Resolve an internal storage key without permitting link escapes."""

    validate_storage_key(storage_key)
    resolved_root = root.resolve(strict=True)
    relative = PurePosixPath(storage_key)
    candidate = root.joinpath(*relative.parts)
    resolved = candidate.resolve(strict=True)
    if not resolved.is_relative_to(resolved_root) or not resolved.is_file():
        raise FileNotFoundError("The managed file is unavailable.")

    current = root
    for part in relative.parts:
        current = current / part
        is_junction = getattr(current, "is_junction", lambda: False)
        if current.is_symlink() or is_junction():
            raise PermissionError("Managed storage links are not permitted.")
    return resolved


def prepare_managed_destination(root: Path, storage_key: str) -> Path:
    """Create a confined parent tree and reject pre-existing links."""

    validate_storage_key(storage_key)
    _ensure_directory(root)
    resolved_root = root.resolve(strict=True)
    relative = PurePosixPath(storage_key)
    current = root
    for part in relative.parts[:-1]:
        current = current / part
        if current.exists():
            is_junction = getattr(current, "is_junction", lambda: False)
            if current.is_symlink() or is_junction() or not current.is_dir():
                raise PermissionError(
                    "Managed storage parent links are not permitted."
                )
        else:
            _ensure_directory(current)
        if not current.resolve(strict=True).is_relative_to(resolved_root):
            raise PermissionError("Managed storage escaped its configured root.")

    destination = current / relative.name
    if destination.exists():
        is_junction = getattr(destination, "is_junction", lambda: False)
        if destination.is_symlink() or is_junction() or not destination.is_file():
            raise PermissionError("Managed storage links are not permitted.")
    return destination


def _ensure_directory(path: Path) -> None:
    missing = []
    current = path
    while not current.exists():
        missing.append(current)
        current = current.parent
    for directory in reversed(missing):
        try:
            directory.mkdir()
        except FileExistsError:
            if not directory.is_dir():
                raise
        else:
            sync_parent_directory(directory.parent)


def durable_replace(source: Path, destination: Path) -> None:
    """Replace a file and synchronize both affected directory entries."""

    source_parent = source.parent
    destination_parent = destination.parent
    source.replace(destination)
    sync_parent_directory(destination_parent)
    if source_parent != destination_parent:
        sync_parent_directory(source_parent)


def durable_unlink(path: Path, *, missing_ok: bool = False) -> bool:
    """Remove a file and synchronize the removed directory entry."""

    try:
        path.unlink()
    except FileNotFoundError:
        if missing_ok:
            return False
        raise
    sync_parent_directory(path.parent)
    return True
