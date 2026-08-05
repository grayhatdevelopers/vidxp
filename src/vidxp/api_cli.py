from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from vidxp.network_share import (
    load_or_create_api_share_token,
    primary_lan_address,
)


def _port(value: str) -> int:
    try:
        port = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("port must be an integer") from exc
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("port must be between 1 and 65535")
    return port


def _arguments(values: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run the VidXP HTTP API and remote MCP server. "
            "Configure the service with VIDXP_* environment variables."
        )
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        help="Store VidXP models and the default repository here.",
    )
    parser.add_argument(
        "--port",
        type=_port,
        metavar="PORT",
        help="Listen on this port instead of the VidXP default.",
    )
    parser.add_argument(
        "--share",
        action="store_true",
        help=(
            "Share the API and MCP on this machine's LAN address using an "
            "app-managed bearer token."
        ),
    )
    parser.add_argument(
        "--print-share-details",
        action="store_true",
        help="Print the resolved LAN connection details as JSON and exit.",
    )
    return parser.parse_args(values)


def _shared_settings(settings, *, host: str, token: str):
    from vidxp.settings import HttpAuthMode, VidXPSettings

    if settings.http_auth_mode not in {HttpAuthMode.none, HttpAuthMode.static}:
        raise ValueError(
            "--share supports the app-managed static bearer mode, not OIDC."
        )
    active_token = (
        settings.http_static_bearer_token.get_secret_value()
        if settings.http_auth_mode == HttpAuthMode.static
        and settings.http_static_bearer_token is not None
        else token
    )
    payload = settings.model_dump(mode="python")
    payload.update(
        {
            "http_bind_host": host,
            "http_auth_mode": HttpAuthMode.static,
            "http_static_bearer_token": active_token,
            "http_trusted_hosts": tuple(
                dict.fromkeys((*settings.http_trusted_hosts, host))
            ),
            "mcp_allowed_hosts": tuple(
                dict.fromkeys((*settings.mcp_allowed_hosts, f"{host}:*"))
            ),
        }
    )
    return VidXPSettings.model_validate(payload), active_token


def _share_details(settings, token: str) -> dict[str, str | int]:
    origin = f"http://{settings.http_bind_host}:{settings.http_port}"
    return {
        "origin": origin,
        "host": settings.http_bind_host,
        "port": settings.http_port,
        "health_url": f"{origin}/health",
        "mcp_url": f"{origin}/mcp",
        "bearer_token": token,
    }


def _print_share_details(settings, token: str) -> None:
    details = _share_details(settings, token)
    print("VidXP network sharing is enabled.", flush=True)
    print(f"Health: {details['health_url']}", flush=True)
    print(f"MCP: {details['mcp_url']}", flush=True)
    print(f"Bearer token: {token}", flush=True)
    print(
        "Keep this token private. LAN traffic uses HTTP and is not encrypted.",
        flush=True,
    )
    if settings.upload_handoff_public_url is None:
        print(
            "Browser upload tools are omitted on this HTTP LAN listener. "
            "Configure an advertised HTTPS handoff origin to enable them.",
            flush=True,
        )


def main(arguments: Sequence[str] | None = None) -> None:
    options = _arguments(arguments)

    if options.print_share_details and not options.share:
        raise ValueError("--print-share-details requires --share.")

    import uvicorn

    from vidxp.api import create_app
    from vidxp.settings import VidXPSettings

    settings = (
        VidXPSettings(data_dir=options.data_dir)
        if options.data_dir is not None
        else VidXPSettings()
    )
    if options.port is not None:
        payload = settings.model_dump(mode="python")
        payload["http_port"] = options.port
        settings = VidXPSettings.model_validate(payload)
    share_token = None
    if options.share:
        if settings.http_auth_mode.value not in {"none", "static"}:
            raise ValueError(
                "--share supports the app-managed static bearer mode, not OIDC."
            )
        configured_token = (
            settings.http_static_bearer_token.get_secret_value()
            if settings.http_auth_mode.value == "static"
            and settings.http_static_bearer_token is not None
            else None
        )
        settings, share_token = _shared_settings(
            settings,
            host=primary_lan_address(),
            token=configured_token or load_or_create_api_share_token(),
        )
    settings.validate_http_server()
    if options.print_share_details:
        assert share_token is not None
        print(json.dumps(_share_details(settings, share_token), sort_keys=True))
        return
    if share_token is not None:
        _print_share_details(settings, share_token)
    uvicorn.run(
        create_app(settings),
        host=settings.http_bind_host,
        port=settings.http_port,
    )
