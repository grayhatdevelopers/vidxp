from __future__ import annotations

from pathlib import Path
from typing import Annotated, Iterable

import typer
from rich.console import Console
from rich.table import Table

from vidxp.cli_support import (
    CLIState,
    OutputFormat,
    effective_output_format,
    emit_json,
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
        clusters = state.service.actor_clusters()
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
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit machine-readable JSON."),
    ] = False,
) -> None:
    """List actor clusters in the selected index."""

    state = state_from_context(ctx)
    clusters = state.service.actor_clusters()
    payload = {
        "clusters": [cluster.to_dict() for cluster in clusters],
        "count": len(clusters),
    }
    if effective_output_format(state, json_output) == OutputFormat.json:
        emit_json(payload)
        return
    table = Table(title="Actor clusters")
    table.add_column("Cluster")
    table.add_column("Detections", justify="right")
    table.add_column("First", justify="right")
    table.add_column("Last", justify="right")
    for cluster in clusters:
        table.add_row(
            cluster.cluster_id,
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
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit machine-readable JSON."),
    ] = False,
) -> None:
    """Inspect retained detections for one actor cluster."""

    state = state_from_context(ctx)
    detections = state.service.actor_detections(cluster_id)
    payload = {
        "cluster_id": cluster_id,
        "detection_count": len(detections),
        "detections": [
            detection.model_dump(mode="json")
            for detection in detections[:limit]
        ],
        "truncated": len(detections) > limit,
    }
    if effective_output_format(state, json_output) == OutputFormat.json:
        emit_json(payload)
        return
    table = Table(title=f"Actor cluster {cluster_id}")
    table.add_column("Frame", justify="right")
    table.add_column("Timestamp", justify="right")
    table.add_column("Detection")
    for detection in detections[:limit]:
        table.add_row(
            str(detection.frame_index),
            f"{detection.timestamp:.3f}s",
            detection.detection_id,
        )
    Console().print(table)
    if len(detections) > limit:
        typer.echo(f"Showing {limit} of {len(detections)} detections.")


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
    input_path: Annotated[
        Path,
        typer.Argument(
            exists=True,
            dir_okay=False,
            readable=True,
            resolve_path=True,
            help="Source video used to create the active index.",
        ),
    ],
    output_path: Annotated[
        Path,
        typer.Option(
            "--output",
            "-o",
            dir_okay=False,
            help="Rendered video destination.",
        ),
    ] = Path("output.mp4"),
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit machine-readable JSON."),
    ] = False,
) -> None:
    """Render one actor cluster as a result video."""

    state = state_from_context(ctx)
    result = state.service.render_actor(
        cluster_id,
        input_path,
        output_path,
    )
    payload = {
        "cluster_id": cluster_id,
        "output_path": str(result.output_path),
        "detection_count": result.detection_count,
    }
    if effective_output_format(state, json_output) == OutputFormat.json:
        emit_json(payload)
    else:
        typer.secho(
            f"Video saved as {result.output_path}",
            fg=typer.colors.GREEN,
        )
