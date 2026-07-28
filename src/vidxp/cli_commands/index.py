from __future__ import annotations

from typing import Annotated, Iterable

import typer

from vidxp.application_models import CreateIndexCommand, RemoveIndexCommand
from vidxp.cli_support import (
    CLIState,
    IndexProgress,
    OutputFormat,
    effective_output_format,
    emit_json,
    emit_status,
    parse_capability_options,
    selected_modalities,
    state_from_context,
)


app = typer.Typer(no_args_is_help=True, help="Manage a local video index.")


def create_index(
    state: CLIState,
    media_id: str,
    *,
    modalities: Iterable[str],
    frame_stride: int,
    capability_options: dict[str, dict],
    detach: bool = False,
) -> dict:
    show_progress = (
        not state.quiet and state.output_format == OutputFormat.rich
    )
    with IndexProgress(show_progress) as progress:
        job = state.jobs.submit_index(
            CreateIndexCommand(
                media_id=media_id,
                modalities=tuple(modalities),
                frame_stride=frame_stride,
                capability_options=capability_options,
            ),
        )
        if not detach:
            job = state.jobs.wait(
                job.job_id,
                progress=lambda current: (
                    progress.update(
                        current.progress.model_dump(mode="python")
                    )
                    if current.progress is not None
                    else None
                ),
            )
        summary = job.model_dump(mode="json")
    if state.output_format == OutputFormat.json:
        emit_json(summary)
    else:
        typer.secho(
            (
                f"Indexing job queued: {job.job_id}"
                if detach
                else f"Video indexing completed: {job.job_id}"
            ),
            fg=typer.colors.GREEN,
            bold=True,
        )
    return summary


@app.command("create")
def index_create(
    ctx: typer.Context,
    media_id: Annotated[
        str,
        typer.Argument(help="Registered media identifier to index."),
    ],
    modalities: Annotated[
        list[str] | None,
        typer.Option(
            "--modality",
            "-m",
            help="Modality to index; repeat to select more than one.",
        ),
    ] = None,
    frame_stride: Annotated[
        int,
        typer.Option(
            "--frame-stride",
            min=1,
            help="Materialize every Nth frame for visual modalities.",
        ),
    ] = 1,
    capability_options: Annotated[
        list[str] | None,
        typer.Option(
            "--option",
            help=(
                "Capability setting as CAPABILITY.KEY=VALUE; "
                "repeat for multiple settings."
            ),
        ),
    ] = None,
    detach: Annotated[
        bool,
        typer.Option(
            "--detach",
            help="Return after the durable job is queued.",
        ),
    ] = False,
) -> None:
    """Add media or replace its immutable generation in the active index."""

    state = state_from_context(ctx)
    create_index(
        state,
        media_id=media_id,
        modalities=selected_modalities(
            modalities,
            state.service.registry,
        ),
        frame_stride=frame_stride,
        capability_options=parse_capability_options(capability_options),
        detach=detach,
    )


@app.command("remove")
def index_remove(
    ctx: typer.Context,
    media_id: Annotated[
        str,
        typer.Argument(help="Media identifier to remove from the active index."),
    ],
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit machine-readable JSON."),
    ] = False,
) -> None:
    """Remove one media item from the active snapshot."""

    state = state_from_context(ctx)
    removed = state.service.remove_from_index(
        RemoveIndexCommand(media_id=media_id)
    )
    payload = {"removed": removed, "media_id": media_id}
    if effective_output_format(state, json_output) == OutputFormat.json:
        emit_json(payload)
    else:
        typer.echo(
            "Media removed."
            if removed
            else "The media identifier was not in the active index."
        )


@app.command("status")
def index_status(
    ctx: typer.Context,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit machine-readable JSON."),
    ] = False,
) -> None:
    """Show the state and source of the selected index."""

    state = state_from_context(ctx)
    emit_status(
        state.service.index_status().model_dump(mode="json"),
        output_format=effective_output_format(state, json_output),
    )


@app.command("clear")
def index_clear(
    ctx: typer.Context,
    yes: Annotated[
        bool,
        typer.Option("--yes", "-y", help="Skip the confirmation prompt."),
    ] = False,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit machine-readable JSON."),
    ] = False,
) -> None:
    """Publish an empty active snapshot without deleting retained generations."""

    state = state_from_context(ctx)
    if not yes:
        typer.confirm(
            f"Clear the active index at {state.service.index_directory}?",
            abort=True,
        )
    cleared = state.service.clear_index()
    payload = {
        "cleared": cleared,
    }
    if effective_output_format(state, json_output) == OutputFormat.json:
        emit_json(payload)
    else:
        typer.echo("Index cleared." if cleared else "No index was found.")
