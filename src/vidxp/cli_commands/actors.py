from __future__ import annotations

from typing import Annotated, Iterable

import typer
from rich.console import Console
from rich.table import Table

from vidxp.application_models import CreateActorOverlayCommand
from vidxp.cli_support import (
    CLIState,
    OutputFormat,
    effective_output_format,
    emit_job_progress,
    emit_json,
    emit_progress,
    state_from_context,
)
app = typer.Typer(
    no_args_is_help=True,
    help="Inspect and render actor clusters.",
)


def complete_cluster(
    ctx: typer.Context,
    incomplete: str,
) -> Iterable[tuple[str, str]]:
    state = ctx.find_root().obj
    if not isinstance(state, CLIState):
        return
    try:
        clusters = state.service.actor_clusters(page_size=100).clusters
    except Exception:
        return
    for cluster in clusters:
        if cluster.cluster_id.startswith(incomplete):
            yield (
                cluster.cluster_id,
                f"{cluster.detection_count} detections",
            )


@app.command("list")
def actors_list(
    ctx: typer.Context,
    page_size: Annotated[
        int,
        typer.Option("--page-size", min=1, max=100),
    ] = 50,
    cursor: Annotated[
        str | None,
        typer.Option("--cursor", help="Cursor returned by the previous page."),
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit machine-readable JSON."),
    ] = False,
) -> None:
    """List actor clusters in the selected index."""

    state = state_from_context(ctx)
    page = state.service.actor_clusters(
        page_size=page_size,
        cursor=cursor,
    )
    clusters = page.clusters
    payload = {
        "clusters": [cluster.to_dict() for cluster in clusters],
        "count": len(clusters),
        "total": page.total,
        "next_cursor": page.next_cursor,
    }
    if effective_output_format(state, json_output) == OutputFormat.json:
        emit_json(payload)
        return
    table = Table(title="Actor clusters")
    table.add_column("Cluster")
    table.add_column("Media")
    table.add_column("Detections", justify="right")
    table.add_column("First", justify="right")
    table.add_column("Last", justify="right")
    for cluster in clusters:
        table.add_row(
            cluster.cluster_id,
            cluster.media_id,
            str(cluster.detection_count),
            f"{cluster.first_timestamp:.3f}s",
            f"{cluster.last_timestamp:.3f}s",
        )
    Console().print(table)


@app.command("inspect")
def actors_inspect(
    ctx: typer.Context,
    cluster_id: Annotated[
        str,
        typer.Argument(
            autocompletion=complete_cluster,
            help="Actor cluster identifier.",
        ),
    ],
    limit: Annotated[
        int,
        typer.Option("--limit", min=1, help="Maximum detections to display."),
    ] = 20,
    cursor: Annotated[
        str | None,
        typer.Option("--cursor", help="Cursor returned by the previous page."),
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit machine-readable JSON."),
    ] = False,
) -> None:
    """Inspect retained detections for one actor cluster."""

    state = state_from_context(ctx)
    page = state.service.actor_detections(
        cluster_id,
        page_size=min(limit, 100),
        cursor=cursor,
    )
    detections = page.detections
    payload = {
        "cluster_id": cluster_id,
        "detection_count": page.total,
        "detections": [
            detection.model_dump(mode="json")
            for detection in detections
        ],
        "truncated": page.next_cursor is not None,
        "next_cursor": page.next_cursor,
    }
    if effective_output_format(state, json_output) == OutputFormat.json:
        emit_json(payload)
        return
    table = Table(title=f"Actor cluster {cluster_id}")
    table.add_column("Frame", justify="right")
    table.add_column("Timestamp", justify="right")
    table.add_column("Detection")
    for detection in detections:
        table.add_row(
            str(detection.frame_index),
            f"{detection.timestamp:.3f}s",
            detection.detection_id,
        )
    Console().print(table)
    if page.next_cursor is not None:
        typer.echo(
            f"Showing {len(detections)} detections; more are available."
        )


@app.command("render")
def actors_render(
    ctx: typer.Context,
    cluster_id: Annotated[
        str,
        typer.Argument(
            autocompletion=complete_cluster,
            help="Actor cluster identifier.",
        ),
    ],
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit machine-readable JSON."),
    ] = False,
    detach: Annotated[
        bool,
        typer.Option(
            "--detach",
            help="Return after the durable job is queued.",
        ),
    ] = False,
) -> None:
    """Render one actor cluster as a result video."""

    state = state_from_context(ctx)
    output_format = effective_output_format(state, json_output)
    show_progress = (
        not detach
        and not state.quiet
        and output_format == OutputFormat.rich
    )
    if show_progress:
        emit_progress("Starting actor-overlay rendering...")
    job = state.jobs.submit_actor_overlay(
        CreateActorOverlayCommand(cluster_id=cluster_id)
    )
    if not detach:
        job = state.jobs.wait(
            job.job_id,
            progress=emit_job_progress if show_progress else None,
        )
    payload = job.model_dump(mode="json")
    if output_format == OutputFormat.json:
        emit_json(payload)
    else:
        typer.secho(
            (
                f"Actor overlay job queued: {job.job_id}"
                if detach
                else f"Actor overlay job completed: {job.job_id}"
            ),
            fg=typer.colors.GREEN,
        )
