from __future__ import annotations

from typing import Annotated, Iterable

import typer
from rich.console import Console
from rich.table import Table

from vidxp.application_models import (
    BulkIndexTargetState,
    CreateIndexCommand,
    PlanBulkIndexCommand,
    RemoveIndexCommand,
)
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
        typer.Option(
            "--media-id",
            help=(
                "Registered media identifier to index; repeat to select more "
                "than one. Omit to select every registered media item."
            ),
        ),
    ] = None,
    modalities: Annotated[
        list[str] | None,
        typer.Option(
            "--modality",
            "-m",
            help="Modality to index; repeat to select more than one.",
        ),
    ] = None,
    reindex: Annotated[
        bool,
        typer.Option(
            "--reindex",
            help="Index already-indexed media instead of skipping it.",
        ),
    ] = False,
    plan_only: Annotated[
        bool,
        typer.Option(
            "--plan-only",
            help="Show what would be indexed and skipped without indexing.",
        ),
    ] = False,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit machine-readable JSON."),
    ] = False,
) -> None:
    """Index many media items, skipping those the active snapshot covers."""

    state = state_from_context(ctx)
    indexable = tuple(
        capability.name
        for capability in state.service.list_capabilities()
        if capability.supports_indexing
    )
    selected = selected_modalities(modalities, indexable)
    plan = state.service.plan_bulk_index(
        PlanBulkIndexCommand(
            media_ids=tuple(media_ids or ()),
            modalities=selected,
            reindex=reindex,
        )
    )
    output_format = effective_output_format(state, json_output)
    pending = plan.pending
    outcomes: list[dict] = [
        {
            "media_id": target.media_id,
            "original_filename": target.original_filename,
            "state": target.state.value,
            "reason": None if target.reason is None else target.reason.value,
            "generation_id": target.generation_id,
            "job_id": None,
        }
        for target in plan.skipped
    ]

    if plan_only:
        outcomes.extend(
            {
                "media_id": target.media_id,
                "original_filename": target.original_filename,
                "state": target.state.value,
                "reason": None,
                "generation_id": None,
                "job_id": None,
            }
            for target in pending
        )
    else:
        for position, target in enumerate(pending, start=1):
            if output_format == OutputFormat.rich and not state.quiet:
                typer.echo(
                    f"[{position}/{len(pending)}] Indexing "
                    f"{target.original_filename} ({target.media_id})."
                )
            entry = {
                "media_id": target.media_id,
                "original_filename": target.original_filename,
                "state": "indexed",
                "reason": None,
                "generation_id": None,
                "job_id": None,
            }
            try:
                job = state.jobs.submit_index(
                    CreateIndexCommand(
                        media_id=target.media_id,
                        modalities=plan.modalities,
                    )
                )
                entry["job_id"] = job.job_id
                completed = state.jobs.wait(job.job_id)
                entry["job_id"] = completed.job_id
            # One media item failing must not abandon the rest of the
            # batch, so every error is recorded and the loop continues.
            except Exception as exc:
                entry["state"] = "failed"
                entry["reason"] = str(exc)
            outcomes.append(entry)

    failed = [entry for entry in outcomes if entry["state"] == "failed"]
    payload = {
        "planned": len(pending),
        "skipped": len(plan.skipped),
        "indexed": len(
            [entry for entry in outcomes if entry["state"] == "indexed"]
        ),
        "failed": len(failed),
        "plan_only": plan_only,
        "modalities": list(plan.modalities),
        "items": outcomes,
    }
    if output_format == OutputFormat.json:
        emit_json(payload)
    else:
        table = Table(
            title="Planned media" if plan_only else "Bulk indexing results"
        )
        table.add_column("Filename")
        table.add_column("Outcome")
        table.add_column("Detail")
        for target in plan.skipped:
            table.add_row(
                target.original_filename,
                "skipped",
                "" if target.reason is None else target.reason.value,
            )
        if plan_only:
            for target in pending:
                table.add_row(target.original_filename, "would index", "")
        else:
            for entry in outcomes:
                if entry["state"] == BulkIndexTargetState.skipped.value:
                    continue
                table.add_row(
                    entry["original_filename"],
                    entry["state"],
                    entry["reason"] or entry["job_id"] or "",
                )
        Console().print(table)
        typer.echo(
            f"Selected {len(plan.targets)} media item(s): "
            f"{payload['skipped']} skipped, "
            + (
                f"{payload['planned']} would be indexed."
                if plan_only
                else f"{payload['indexed']} indexed, {payload['failed']} failed."
            )
        )
    if failed:
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
