from __future__ import annotations

import importlib.util
import platform
import sys
from pathlib import Path
from typing import Any

from packaging.version import InvalidVersion, Version

from vidxp import __version__
from vidxp.app_paths import (
    default_data_directory,
    default_repository_directory,
)
from vidxp.application_models import ApplicationError, ErrorCategory
from vidxp.media_runtime import media_runtime_is_initialized


DESKTOP_PROBE_SCHEMA_VERSION = 1
DESKTOP_PROBE_PROTOCOL_VERSION = 1
PRODUCT_ID = "dev.grayhat.vidxp"


def _resolved_path(value: str | Path) -> str:
    return str(Path(value).expanduser().resolve(strict=False))


def _normalized_version(value: str, *, code: str, label: str) -> Version:
    try:
        return Version(value)
    except InvalidVersion as exc:
        raise ApplicationError(
            code,
            ErrorCategory.validation,
            f"The {label} version is not a valid Python package version.",
            details={"version": value},
        ) from exc


def _module_available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, AttributeError, ValueError):
        return False


def _frontend_capability() -> dict[str, Any]:
    installed = _module_available("vidxp.frontend") and _module_available("streamlit")
    media_ready = media_runtime_is_initialized()
    launchable = installed and media_ready
    if not installed:
        code = "frontend_unavailable"
        message = (
            "The optional browser interface is not installed. Install the "
            "VidXP frontend extra to enable desktop launch."
        )
    elif not media_ready:
        code = "media_runtime_uninitialized"
        message = (
            "The browser interface is installed, but FFmpeg and ffprobe are "
            "not initialized for local media work."
        )
    else:
        code = "frontend_available"
        message = "The browser interface can be launched."
    return {
        "available": installed,
        "launchable": launchable,
        "optional": True,
        "code": code,
        "message": message,
    }


def build_desktop_probe(
    *,
    desktop_version: str,
    request_id: str,
    launcher: str | Path | None = None,
    data_root: str | Path | None = None,
    repository_root: str | Path | None = None,
) -> dict[str, Any]:
    """Describe this local VidXP installation without mutating it."""

    product_version = _normalized_version(
        __version__,
        code="product_version_invalid",
        label="installed VidXP",
    )
    requested_desktop_version = _normalized_version(
        desktop_version,
        code="desktop_version_invalid",
        label="VidXP desktop",
    )
    compatible = product_version == requested_desktop_version
    compatibility_code = "compatible" if compatible else "desktop_version_incompatible"
    compatibility_message = (
        "This VidXP installation is compatible with the desktop."
        if compatible
        else (
            "This VidXP installation has a different product version from "
            "the desktop. Select a compatible installation."
        )
    )

    resolved_data_root = (
        Path(data_root if data_root is not None else default_data_directory())
        .expanduser()
        .resolve(strict=False)
    )
    resolved_repository_root = (
        Path(
            repository_root
            if repository_root is not None
            else default_repository_directory(resolved_data_root)
        )
        .expanduser()
        .resolve(strict=False)
    )
    resolved_launcher = launcher if launcher is not None else sys.argv[0]

    return {
        "product": PRODUCT_ID,
        "product_version": __version__,
        "schema_version": DESKTOP_PROBE_SCHEMA_VERSION,
        "protocol_version": DESKTOP_PROBE_PROTOCOL_VERSION,
        "request_id": request_id,
        "launcher": _resolved_path(resolved_launcher),
        "runtime": {
            "python_executable": _resolved_path(sys.executable),
            "python_version": platform.python_version(),
            "implementation": platform.python_implementation(),
            "prefix": _resolved_path(sys.prefix),
            "base_prefix": _resolved_path(sys.base_prefix),
        },
        "data_root": str(resolved_data_root),
        "repository_root": str(resolved_repository_root),
        "compatibility": {
            "compatible": compatible,
            "code": compatibility_code,
            "message": compatibility_message,
            "desktop_version": desktop_version,
            "desktop_version_normalized": str(requested_desktop_version),
            "product_version_normalized": str(product_version),
        },
        "capabilities": {"frontend": _frontend_capability()},
    }
