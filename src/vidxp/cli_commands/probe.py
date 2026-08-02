from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from vidxp.cli_support import emit_json
from vidxp.local_probe import build_desktop_probe


def desktop_probe(
    ctx: typer.Context,
    desktop_version: Annotated[
        str,
        typer.Option(
            "--desktop-version",
            help="VidXP desktop version requesting compatibility validation.",
        ),
    ],
    request_id: Annotated[
        str,
        typer.Option(
            "--request-id",
            min=1,
            max=256,
            help="Opaque challenge echoed by the probe response.",
        ),
    ],
    _json_output: Annotated[
        bool,
        typer.Option(
            "--json",
            help="Emit the stable machine-readable probe contract.",
        ),
    ] = False,
) -> None:
    """Probe local desktop compatibility without changing the installation."""

    root = ctx.find_root().params
    data_directory = root.get("data_directory")
    index_directory = root.get("index_directory")
    emit_json(
        build_desktop_probe(
            desktop_version=desktop_version,
            request_id=request_id,
            data_root=(Path(data_directory) if data_directory is not None else None),
            repository_root=(
                Path(index_directory) if index_directory is not None else None
            ),
        )
    )
