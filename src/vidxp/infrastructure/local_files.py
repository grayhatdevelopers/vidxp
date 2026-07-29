from __future__ import annotations

from pathlib import Path, PurePosixPath

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
    root.mkdir(parents=True, exist_ok=True)
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
            current.mkdir()
        if not current.resolve(strict=True).is_relative_to(resolved_root):
            raise PermissionError("Managed storage escaped its configured root.")

    destination = current / relative.name
    if destination.exists():
        is_junction = getattr(destination, "is_junction", lambda: False)
        if destination.is_symlink() or is_junction() or not destination.is_file():
            raise PermissionError("Managed storage links are not permitted.")
    return destination
