from __future__ import annotations

import hashlib
from pathlib import Path


def repository_binding(repository_root: Path) -> str:
    """Return the stable repository binding used by bearer capabilities."""

    root = str(repository_root.resolve()).replace("\\", "/")
    # Preserve the deployed upload-session binding contract while sharing it
    # with other repository-scoped browser capabilities.
    return hashlib.sha256(
        f"vidxp-upload-repository-v1\0{root}".encode("utf-8")
    ).hexdigest()
