from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from vidxp.mcp_cli import render_stdio_client_config


def mcp_config(
    registry: Annotated[
        Path | None,
        typer.Option(
            "--registry",
            dir_okay=False,
            help="Path to the named-repository configuration file.",
        ),
    ] = None,
    repository: Annotated[
        str,
        typer.Option(
            "--repository",
            "-r",
            help="Repository the local MCP server should use.",
        ),
    ] = "default",
    index_directory: Annotated[
        Path | None,
        typer.Option(
            "--index-directory",
            file_okay=False,
            help="Override the selected repository's index directory.",
        ),
    ] = None,
    data_directory: Annotated[
        Path | None,
        typer.Option(
            "--data-dir",
            file_okay=False,
            help="Store VidXP models and the default repository here.",
        ),
    ] = None,
    device: Annotated[
        str | None,
        typer.Option(
            "--device",
            help="Override the selected repository runtime device.",
        ),
    ] = None,
) -> None:
    """Print copy/paste JSON for a local stdio MCP client."""

    typer.echo(
        render_stdio_client_config(
            registry=None if registry is None else str(registry),
            repository=repository,
            index_directory=(
                None if index_directory is None else str(index_directory)
            ),
            data_directory=data_directory,
            device=device,
        )
    )
