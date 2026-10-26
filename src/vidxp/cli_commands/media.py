from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from vidxp.application_models import ImportMediaCommand, ListMediaCommand
from vidxp.cli_support import (
    OutputFormat,
    effective_output_format,
    emit_json,
    emit_progress,
    require_media_runtime,
    state_from_context,
)
from vidxp.core.media import MediaState


app = typer.Typer(no_args_is_help=True, help="Import and inspect local media.")


@app.command("import")
def import_media(
    ctx: typer.Context,
    path: Annotated[
        Path,
        typer.Argument(
            exists=True,
            dir_okay=False,
            readable=True,
            resolve_path=True,
            help="Local video file to copy into managed storage.",
        ),
    ],
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit machine-readable JSON."),
    ] = False,
) -> None:
    """Validate and register a local video, returning its stable media ID."""

    require_media_runtime()
    state = state_from_context(ctx)
    output_format = effective_output_format(state, json_output)
    if not state.quiet and output_format == OutputFormat.rich:
        emit_progress(f"Importing {path.name} into managed storage...")
    result = state.service.import_media(ImportMediaCommand(path=path))
    payload = result.model_dump(mode="json")
    if output_format == OutputFormat.json:
        emit_json(payload)
    else:
        typer.secho(
            f"Imported {result.original_filename} as {result.media_id}",
            fg=typer.colors.GREEN,
        )


@app.command("list")
def list_media(
    ctx: typer.Context,
    limit: Annotated[
        int,
        typer.Option("--limit", min=1, max=100),
    ] = 100,
    cursor: Annotated[
        str | None,
        typer.Option("--cursor", help="Cursor returned by the previous page."),
    ] = None,
    filename: Annotated[
        str | None,
        typer.Option("--filename", help="Filter media by filename."),
    ] = None,
    media_state: Annotated[
        MediaState | None,
        typer.Option("--state", help="Filter media by readiness/state."),
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit machine-readable JSON."),
    ] = False,
) -> None:
    """List filenames, metadata, and stable IDs used by other commands."""

    state = state_from_context(ctx)
    page = state.service.list_media(
        ListMediaCommand(
            page_size=limit,
            cursor=cursor,
            filename=filename,
            state=media_state,
        )
    )
    assets = page.items
    payload = page.model_dump(mode="json")
    if effective_output_format(state, json_output) == OutputFormat.json:
        emit_json(payload)
        return
    table = Table(title="Media")
    table.add_column("ID")
    table.add_column("Filename")
    table.add_column("Duration", justify="right")
    table.add_column("Size", justify="right")
    table.add_column("State")
    for asset in assets:
        table.add_row(
            asset.media_id,
            asset.original_filename,
            (
                "-"
                if asset.duration_seconds is None
                else f"{asset.duration_seconds:.3f}s"
            ),
            f"{asset.byte_size:,}",
            asset.state.value
        )
    Console().print(table)


@app.command("show")
def show_media(
    ctx: typer.Context,
    media_id: Annotated[
        str,
        typer.Argument(
            help="Stable media identifier returned by import or list."
        ),
    ],
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit machine-readable JSON."),
    ] = False,
) -> None:
    """Show all registered metadata for one media ID."""

    state = state_from_context(ctx)
    payload = state.service.get_media(media_id).model_dump(mode="json")
    if effective_output_format(state, json_output) == OutputFormat.json:
        emit_json(payload)
    else:
        Console().print_json(data=payload)
