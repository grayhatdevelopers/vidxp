from __future__ import annotations

import importlib.util
import os
import platform
import sys
from pathlib import Path
from typing import Any

from vidxp import __version__
from vidxp.app_paths import (
    default_data_directory,
    default_repository_directory,
)
from vidxp.media_runtime import media_runtime_is_initialized


DESKTOP_PROBE_SCHEMA_VERSION = 1
DESKTOP_PROBE_PROTOCOL_VERSION = 1
DESKTOP_LAUNCH_PROTOCOL_VERSION = 1
PRODUCT_ID = "dev.grayhat.vidxp"


def _resolved_path(value: str | Path) -> str:
    return str(Path(value).expanduser().resolve(strict=False))


def _windows_executable_extensions() -> tuple[str, ...]:
    configured = os.environ.get("PATHEXT", ".COM;.EXE;.BAT;.CMD")
    extensions: list[str] = []
    for value in configured.split(";"):
        extension = value.strip()
        if not extension:
            continue
        if not extension.startswith("."):
            extension = f".{extension}"
        normalized = extension.lower()
        if normalized not in extensions:
            extensions.append(normalized)
    return tuple(extensions)


def _resolved_launcher_path(
    value: str | Path,
    *,
    windows: bool | None = None,
) -> str:
    launcher = Path(value).expanduser()
    windows = os.name == "nt" if windows is None else windows
    if windows and not launcher.exists() and launcher.suffix == "":
        for extension in _windows_executable_extensions():
            candidate = launcher.with_name(f"{launcher.name}{extension}")
            if candidate.is_file():
                launcher = candidate
                break
    return str(launcher.resolve(strict=False))


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
            "The VidXP command-line installation is usable, but its optional "
            "browser interface is not installed."
        )
        remediation = (
            "Use the environment or package manager that owns this executable "
            "to install VidXP with the 'frontend' extra, initialize its media "
            "runtime if needed, then return to VidXP Desktop and revalidate."
        )
    elif not media_ready:
        code = "media_runtime_uninitialized"
        message = (
            "The VidXP command-line installation and browser interface are "
            "installed, but FFmpeg and ffprobe are not initialized for local "
            "media work."
        )
        remediation = (
            "Use this installation's own VidXP initialization workflow to set "
            "up its media runtime, then return to VidXP Desktop and revalidate."
        )
    else:
        code = "frontend_available"
        message = "The browser interface can be launched."
        remediation = ""
    return {
        "available": installed,
        "launchable": launchable,
        "optional": True,
        "code": code,
        "message": message,
        "remediation": remediation,
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
        "launch_contract": {
            "protocol_version": DESKTOP_LAUNCH_PROTOCOL_VERSION,
            "surface": "browser",
            "command": "ui",
        },
        "request_id": request_id,
        "launcher": _resolved_launcher_path(resolved_launcher),
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
            "compatible": True,
            "code": "contract_compatible",
            "message": (
                "This installation implements the supported desktop probe and "
                "browser launch contracts."
            ),
            "desktop_version": desktop_version,
        },
        "capabilities": {"frontend": _frontend_capability()},
    }
