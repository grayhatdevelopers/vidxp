from __future__ import annotations

from typing import Annotated, Any, Iterable

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from vidxp.application_models import (
    CreateIndexCommand,
    RemoveIndexCommand,
)
from vidxp.bulk_indexing import run_bulk_index
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
    scene_sample_fps: float | None,
    capability_options: dict[str, dict],
    detach: bool = False,
) -> dict:
    show_progress = (
        not state.quiet and state.output_format == OutputFormat.rich
    )
    selected = tuple(modalities)
    with IndexProgress(show_progress) as progress:
        job = state.jobs.submit_index(
            CreateIndexCommand(
                media_id=media_id,
                modalities=selected,
                frame_stride=frame_stride,
                scene_sample_fps=scene_sample_fps,
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
            help=(
                "Materialize every Nth frame for actor and legacy visual "
                "indexing."
            ),
        ),
    ] = 1,
    scene_sample_fps: Annotated[
        float | None,
        typer.Option(
            "--scene-sample-fps",
            min=0.01,
            help=(
                "Target scene samples per second; lower-FPS media uses every "
                "available frame."
            ),
        ),
    ] = None,
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
    indexable = tuple(
        capability.name
        for capability in state.service.list_capabilities()
        if capability.supports_indexing
    )
    create_index(
        state,
        media_id=media_id,
        modalities=selected_modalities(
            modalities,
            indexable,
        ),
        frame_stride=frame_stride,
        scene_sample_fps=scene_sample_fps,
        capability_options=parse_capability_options(capability_options),
        detach=detach,
    )


@app.command("bulk")
def index_bulk(
    ctx: typer.Context,
    media_ids: Annotated[
        list[str] | None,
        typer.Argument(
            help="Registered media identifiers to index.",
        ),
    ] = None,
    all_eligible: Annotated[
        bool,
        typer.Option(
            "--all",
            help="Index all eligible registered media in the catalog.",
        ),
    ] = False,
    reindex: Annotated[
        bool,
        typer.Option(
            "--reindex",
            help="Reindex media even if already present in the active index.",
        ),
    ] = False,
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
            help=(
                "Materialize every Nth frame for actor and legacy visual "
                "indexing."
            ),
        ),
    ] = 1,
    scene_sample_fps: Annotated[
        float | None,
        typer.Option(
            "--scene-sample-fps",
            min=0.01,
            help=(
                "Target scene samples per second; lower-FPS media uses every "
                "available frame."
            ),
        ),
    ] = None,
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
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit machine-readable JSON."),
    ] = False,
) -> None:
    """Index multiple media items or all eligible media in the catalog."""

    if not media_ids and not all_eligible:
        raise typer.BadParameter("Provide either media IDs or pass --all.")
    if media_ids and all_eligible:
        raise typer.BadParameter("Pass media IDs or --all, not both.")

    state = state_from_context(ctx)
    indexable = tuple(
        capability.name
        for capability in state.service.list_capabilities()
        if capability.supports_indexing
    )
    selected = selected_modalities(modalities, indexable)
    parsed_options = parse_capability_options(capability_options)
    output_fmt = effective_output_format(state, json_output)
    show_progress = not state.quiet and output_fmt == OutputFormat.rich

    with IndexProgress(show_progress) as progress:
        def on_item_start(media_id: str, filename: str) -> None:
            if show_progress:
                progress.update({
                    "stage": "indexing",
                    "message": f"Indexing {filename} ({media_id[:8]}...)",
                })

        def on_item_progress(media_id: str, current: Any) -> None:
            if show_progress:
                if hasattr(current, "progress") and current.progress is not None:
                    progress.update(current.progress.model_dump(mode="python"))
                elif isinstance(current, dict):
                    progress.update(current)

        summary = run_bulk_index(
            application=state.service,
            jobs=state.jobs,
            media_ids=media_ids,
            all_eligible=all_eligible,
            skip_indexed=not reindex,
            detach=detach,
            modalities=selected,
            frame_stride=frame_stride,
            scene_sample_fps=scene_sample_fps,
            capability_options=parsed_options,
            on_item_start=on_item_start,
            on_item_progress=on_item_progress,
        )

    if output_fmt == OutputFormat.json:
        payload = {
            "total": summary.total,
            "indexed": summary.indexed,
            "skipped": summary.skipped,
            "failed": summary.failed,
            "queued": summary.queued,
            "results": [
                {
                    "media_id": r.media_id,
                    "filename": r.filename,
                    "status": r.status,
                    "job_id": r.job_id,
                    "error_code": r.error_code,
                    "error_message": r.error_message,
                }
                for r in summary.results
            ],
        }
        emit_json(payload)
    else:
        table = Table(title="Bulk indexing summary")
        table.add_column("Media ID")
        table.add_column("Filename")
        table.add_column("Status")
        table.add_column("Job ID")
        table.add_column("Error")
        for r in summary.results:
            error_str = (
                f"[{r.error_code}] {r.error_message}"
                if r.error_code
                else (r.error_message or "—")
            )
            table.add_row(
                escape(r.media_id),
                escape(r.filename),
                escape(r.status),
                escape(r.job_id or "—"),
                escape(error_str),
            )
        Console().print(table)
        typer.echo(
            f"Total: {summary.total}, Indexed: {summary.indexed}, "
            f"Skipped: {summary.skipped}, Failed: {summary.failed}, "
            f"Queued: {summary.queued}."
        )

    if summary.failed > 0:
        raise typer.Exit(code=1)


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


@app.command("list")
def index_list(
    ctx: typer.Context,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit machine-readable JSON."),
    ] = False,
) -> None:
    """List registered metadata for media in the active index snapshot."""

    state = state_from_context(ctx)
    status = state.service.index_status()
    summary = status.summary
    assets = (
        ()
        if summary is None
        else tuple(
            state.service.get_media(media_id)
            for media_id in summary.media_ids
        )
    )
    payload = {
        "state": status.state,
        "message": status.message,
        "snapshot_id": None if summary is None else summary.snapshot_id,
        "media_count": 0 if summary is None else summary.media_count,
        "media_ids_truncated": (
            False if summary is None else summary.media_ids_truncated
        ),
        "modalities": [] if summary is None else list(summary.modalities),
        "items": [asset.model_dump(mode="json") for asset in assets],
    }
    if effective_output_format(state, json_output) == OutputFormat.json:
        emit_json(payload)
        return
    if summary is None:
        typer.echo(status.message)
        return

    table = Table(title="Active index media")
    table.add_column("ID")
    table.add_column("Filename")
    table.add_column("Duration", justify="right")
    table.add_column("Size", justify="right")
    for asset in assets:
        table.add_row(
            asset.media_id,
            asset.original_filename,
            f"{asset.duration_seconds:.3f}s",
            f"{asset.byte_size:,}",
        )
    Console().print(table)
    typer.echo(
        f"Snapshot {summary.snapshot_id}: {summary.media_count} media item(s); "
        f"modalities: {', '.join(summary.modalities) or 'none'}."
    )
    if summary.media_ids_truncated:
        typer.secho(
            "The active snapshot is larger than this status page; some media "
            "items are not shown.",
            fg=typer.colors.YELLOW,
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
