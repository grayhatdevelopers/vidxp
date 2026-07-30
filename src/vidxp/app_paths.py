from __future__ import annotations

import os
import shutil
from pathlib import Path

from platformdirs import user_config_path, user_data_path


APP_NAME = "VidXP"


def default_data_directory() -> Path:
    configured = os.environ.get("VIDXP_DATA_DIR")
    if configured:
        return Path(configured).expanduser()
    return user_data_path(APP_NAME, appauthor=False)


def default_repository_directory(data_directory: Path | None = None) -> Path:
    root = data_directory or default_data_directory()
    return root / "repositories" / "default"


def default_model_directory(data_directory: Path | None = None) -> Path:
    root = data_directory or default_data_directory()
    return root / "models"


def default_config_directory() -> Path:
    return user_config_path(APP_NAME, appauthor=False, roaming=True)


def available_storage_bytes(directory: Path) -> int | None:
    """Return free bytes on the volume that will contain ``directory``."""

    candidate = directory.expanduser().resolve(strict=False)
    while not candidate.exists() and candidate != candidate.parent:
        candidate = candidate.parent
    try:
        return shutil.disk_usage(candidate).free
    except OSError:
        return None
