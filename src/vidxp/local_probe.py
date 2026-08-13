from __future__ import annotations

import importlib.util
import platform
import sys
from pathlib import Path
from typing import Any

from vidxp import __version__
from vidxp.app_paths import (
    default_data_directory,
    default_model_directory,
    default_repository_directory,
)
from vidxp.media_runtime import media_runtime_is_initialized


DESKTOP_PROBE_SCHEMA_VERSION = 1
DESKTOP_PROBE_PROTOCOL_VERSION = 1
DESKTOP_LAUNCH_PROTOCOL_VERSION = 2
PRODUCT_ID = "dev.grayhat.vidxp"


def _resolved_path(value: str | Path) -> str:
    return str(Path(value).expanduser().resolve(strict=False))


def _resolved_launcher_path(
    value: str | Path,
    *,
    windows: bool | None = None,
) -> str:
    launcher = Path(value).expanduser()
    del windows  # Desktop compares the raw extensionless identity to its exact selection.
    return str(launcher.resolve(strict=False))


def desktop_model_cache_catalog() -> list[dict[str, str]]:
    """Derive Desktop cache identities from the canonical capability registry."""

    from vidxp.capabilities.registry import create_capability_registry
    from vidxp.model_contracts import model_artifact_path

    catalog = []
    for spec in create_capability_registry().model_specs():
        catalog.append(
            {
                "id": spec.model_id,
                "label": spec.model_id,
                "relative_artifact": model_artifact_path(Path(), spec).as_posix(),
            }
        )
    return sorted(catalog, key=lambda item: item["id"].casefold())


def _module_available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, AttributeError, ValueError):
        return False


def _surface_capability(
    *,
    installed: bool,
    media_ready: bool,
    unavailable_code: str,
    available_code: str,
    unavailable_message: str,
    unavailable_remediation: str,
    available_message: str,
) -> dict[str, Any]:
    launchable = installed and media_ready
    if not installed:
        code = unavailable_code
        message = unavailable_message
        remediation = unavailable_remediation
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
        code = available_code
        message = available_message
        remediation = ""
    return {
        "available": installed,
        "launchable": launchable,
        "optional": True,
        "code": code,
        "message": message,
        "remediation": remediation,
    }


def _surface_capabilities(
    search_capabilities: list[str],
) -> dict[str, dict[str, Any]]:
    media_ready = media_runtime_is_initialized()
    owner_instruction = (
        "Use the environment or package manager that owns this executable"
    )
    return {
        "worker": _surface_capability(
            installed=(
                {"dialogue", "scene", "actor", "videoprism"}.issubset(
                    search_capabilities
                )
                and _module_available("pydantic_ai")
            ),
            media_ready=media_ready,
            unavailable_code="local_worker_unavailable",
            available_code="local_worker_available",
            unavailable_message=(
                "Local background video processing is not installed in this "
                "VidXP environment."
            ),
            unavailable_remediation=(
                f"{owner_instruction} to install VidXP with the "
                "'local-worker' extra, then return to VidXP Desktop and "
                "revalidate."
            ),
            available_message="Local background video processing is available.",
        ),
        "browser": _surface_capability(
            installed=(
                _module_available("vidxp.frontend")
                and _module_available("streamlit")
            ),
            media_ready=media_ready,
            unavailable_code="frontend_unavailable",
            available_code="frontend_available",
            unavailable_message=(
                "The VidXP command-line installation is usable, but its "
                "optional browser interface is not installed."
            ),
            unavailable_remediation=(
                f"{owner_instruction} to install VidXP with the 'frontend' "
                "extra, initialize its media runtime if needed, then return "
                "to VidXP Desktop and revalidate."
            ),
            available_message="The browser interface can be launched.",
        ),
        "mcp": _surface_capability(
            installed=(
                _module_available("vidxp.mcp") and _module_available("mcp")
            ),
            media_ready=media_ready,
            unavailable_code="mcp_unavailable",
            available_code="mcp_available",
            unavailable_message=(
                "The local stdio MCP server is not installed in this VidXP "
                "environment."
            ),
            unavailable_remediation=(
                f"{owner_instruction} to install VidXP with the 'mcp' extra, "
                "then return to VidXP Desktop and revalidate."
            ),
            available_message="The local stdio MCP server is available.",
        ),
        "server": _surface_capability(
            installed=all(
                _module_available(name)
                for name in ("vidxp.api", "mcp", "fastapi", "uvicorn")
            ),
            media_ready=media_ready,
            unavailable_code="server_unavailable",
            available_code="server_available",
            unavailable_message=(
                "The local HTTP API and remote MCP server are not installed "
                "in this VidXP environment."
            ),
            unavailable_remediation=(
                f"{owner_instruction} to install VidXP with the 'server' "
                "extra, then return to VidXP Desktop and revalidate."
            ),
            available_message="The local API and remote MCP server are available.",
        ),
    }


def _installed_search_capabilities() -> list[str]:
    from vidxp.capabilities.registry import create_capability_registry
    from vidxp.dependencies import inspect_requirement

    registry = create_capability_registry()
    installed = []
    for name in registry.definitions:
        requirements = registry.requirements_for((name,))
        if requirements and all(inspect_requirement(item)["ok"] for item in requirements):
            installed.append(name)
    return sorted(installed)


def build_desktop_probe(
    *,
    desktop_version: str,
    request_id: str,
    launcher: str | Path | None = None,
    data_root: str | Path | None = None,
    repository_root: str | Path | None = None,
    model_root: str | Path | None = None,
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
    resolved_model_root = (
        Path(
            model_root
            if model_root is not None
            else default_model_directory(resolved_data_root)
        )
        .expanduser()
        .resolve(strict=False)
    )
    resolved_launcher = launcher if launcher is not None else sys.argv[0]

    search_capabilities = _installed_search_capabilities()
    surfaces = _surface_capabilities(search_capabilities)
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
        "model_root": str(resolved_model_root),
        "compatibility": {
            "compatible": True,
            "code": "contract_compatible",
            "message": (
                "This installation implements the supported desktop probe and "
                "browser launch contracts."
            ),
            "desktop_version": desktop_version,
        },
        # ``capabilities.frontend`` is retained for Desktop protocol v1 clients.
        "capabilities": {"frontend": surfaces["browser"]},
        "search_capabilities": search_capabilities,
        "surfaces": surfaces,
    }
