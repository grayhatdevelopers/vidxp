from __future__ import annotations

import ipaddress
import os
import socket
from pathlib import Path
from secrets import token_urlsafe

from vidxp.app_paths import default_config_directory


API_SHARE_TOKEN_FILE = "api-share-token"


def _usable_ipv4(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    return (
        address.version == 4
        and not address.is_loopback
        and not address.is_unspecified
        and not address.is_link_local
    )


def primary_lan_address() -> str:
    """Resolve the IPv4 address selected by the host's default route."""

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            # UDP connect selects a route without sending application data.
            probe.connect(("192.0.2.1", 9))
            candidate = str(probe.getsockname()[0])
            if _usable_ipv4(candidate):
                return candidate
    except OSError:
        pass

    try:
        addresses = socket.getaddrinfo(
            socket.gethostname(),
            None,
            family=socket.AF_INET,
            type=socket.SOCK_STREAM,
        )
    except OSError as exc:
        raise RuntimeError(
            "VidXP could not determine a LAN address for sharing."
        ) from exc
    for item in addresses:
        candidate = str(item[4][0])
        if _usable_ipv4(candidate):
            return candidate
    raise RuntimeError("VidXP could not determine a LAN address for sharing.")


def api_share_token_path() -> Path:
    return default_config_directory() / API_SHARE_TOKEN_FILE


def load_or_create_api_share_token(path: Path | None = None) -> str:
    """Return the stable app-owned bearer token used by API share mode."""

    target = path or api_share_token_path()
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        existing = target.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        existing = ""
    if existing:
        if len(existing) < 32:
            raise RuntimeError(f"The managed VidXP API token is invalid: {target}")
        return existing

    token = token_urlsafe(32)
    try:
        descriptor = os.open(
            target,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
    except FileExistsError:
        existing = target.read_text(encoding="utf-8").strip()
        if len(existing) < 32:
            raise RuntimeError(f"The managed VidXP API token is invalid: {target}")
        return existing
    with os.fdopen(descriptor, "w", encoding="utf-8") as destination:
        destination.write(token + "\n")
        destination.flush()
        os.fsync(destination.fileno())
    return token


def is_loopback_host(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False
