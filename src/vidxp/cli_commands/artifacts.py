from __future__ import annotations

from typing import Annotated

import typer

from vidxp.application_models import CreateSnippetCommand, SnippetProfile
from vidxp.cli_support import (
    OutputFormat,
    effective_output_format,
    emit_json,
    state_from_context,
)


app = typer.Typer(no_args_is_help=True, help="Inspect generated artifacts.")


@app.command("snippet")
def create_snippet(
    ctx: typer.Context,
    media_id: Annotated[
        str,
        typer.Argument(help="Cataloged media identifier."),
    ],
    start_seconds: Annotated[
        float,
        typer.Argument(min=0, help="Snippet start in seconds."),
    ],
    end_seconds: Annotated[
        float,
        typer.Argument(min=0, help="Snippet end in seconds."),
    ],
    profile: Annotated[
        SnippetProfile,
        typer.Option(help="Output compatibility profile."),
    ] = SnippetProfile.compatible_mp4,
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
    """Create a managed video snippet artifact."""

    if end_seconds <= start_seconds:
        raise typer.BadParameter(
            "The snippet end must be greater than its start.",
            param_hint="end_seconds",
        )
    state = state_from_context(ctx)
    job = state.jobs.submit_snippet(
        CreateSnippetCommand(
            media_id=media_id,
            start_seconds=start_seconds,
            end_seconds=end_seconds,
            profile=profile,
        )
    )
    if not detach:
        job = state.jobs.wait(job.job_id)
    payload = job.model_dump(mode="json")
    if effective_output_format(state, json_output) == OutputFormat.json:
        emit_json(payload)
    else:
        typer.secho(
            (
                f"Snippet job queued: {job.job_id}"
                if detach
                else f"Snippet artifact job completed: {job.job_id}"
            ),
            fg=typer.colors.GREEN,
        )


@app.command("show")
def show_artifact(
    ctx: typer.Context,
    artifact_id: Annotated[
        str,
        typer.Argument(help="Artifact identifier."),
    ],
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit machine-readable JSON."),
    ] = False,
) -> None:
    """Show metadata for one generated artifact."""

    state = state_from_context(ctx)
    artifact = state.service.get_artifact(artifact_id)
    payload = artifact.model_dump(mode="json")
    if effective_output_format(state, json_output) == OutputFormat.json:
        emit_json(payload)
    else:
        typer.echo(
            f"{artifact.kind.value} {artifact.artifact_id} "
            f"({artifact.byte_size:,} bytes)"
        )
