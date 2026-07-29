from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path


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
    return parser.parse_args(values)


def main(arguments: Sequence[str] | None = None) -> None:
    options = _arguments(arguments)

    import uvicorn

    from vidxp.api import create_app
    from vidxp.settings import VidXPSettings

    settings = (
        VidXPSettings(data_dir=options.data_dir)
        if options.data_dir is not None
        else VidXPSettings()
    )
    settings.validate_http_server()
    uvicorn.run(
        create_app(settings),
        host=settings.http_bind_host,
        port=settings.http_port,
    )
