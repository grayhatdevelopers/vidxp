from __future__ import annotations

import argparse
from collections.abc import Sequence


def _arguments(values: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Run the VidXP HTTP API and remote MCP server. "
            "Configure the service with VIDXP_* environment variables."
        )
    )
    parser.parse_args(values)


def main(arguments: Sequence[str] | None = None) -> None:
    _arguments(arguments)

    import uvicorn

    from vidxp.api import create_app
    from vidxp.settings import VidXPSettings

    settings = VidXPSettings()
    settings.validate_http_server()
    uvicorn.run(
        create_app(settings),
        host=settings.http_bind_host,
        port=settings.http_port,
    )
