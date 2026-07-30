from __future__ import annotations

import shutil
from pathlib import Path
from typing import Annotated
from uuid import uuid4

import typer

from vidxp.application_models import (
    ArtifactJobResult,
    CreateSnippetCommand,
    SnippetProfile,
)
from vidxp.cli_support import (
    OutputFormat,
    effective_output_format,
    emit_job_progress,
    emit_json,
    emit_progress,
    require_media_runtime,
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
    require_media_runtime()
    state = state_from_context(ctx)
    output_format = effective_output_format(state, json_output)
    show_progress = (
        not detach
        and not state.quiet
        and output_format == OutputFormat.rich
    )
    if show_progress:
        emit_progress("Starting snippet rendering...")
    job = state.jobs.submit_snippet(
        CreateSnippetCommand(
            media_id=media_id,
            start_seconds=start_seconds,
            end_seconds=end_seconds,
            profile=profile,
        )
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
        if detach:
            typer.secho(
                f"Snippet job queued: {job.job_id}",
                fg=typer.colors.GREEN,
            )
        elif isinstance(job.result, ArtifactJobResult):
            artifact = job.result.result
            typer.secho(
                f"Clip ready: {artifact.artifact_id} "
                f"({artifact.byte_size:,} bytes)",
                fg=typer.colors.GREEN,
            )
            typer.echo(
                "Download it with: "
                f"vidxp artifacts download {artifact.artifact_id}"
            )
        else:
            typer.secho(
                f"Snippet artifact job completed: {job.job_id}",
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


@app.command("download")
def download_artifact(
    ctx: typer.Context,
    artifact_id: Annotated[
        str,
        typer.Argument(help="Artifact identifier from a completed job."),
    ],
    destination: Annotated[
        Path | None,
        typer.Argument(
            help=(
                "Output file or existing directory. Defaults to the generated "
                "artifact filename in the current directory."
            ),
        ),
    ] = None,
    force: Annotated[
        bool,
        typer.Option(
            "--force",
            help="Replace an existing output file.",
        ),
    ] = False,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit machine-readable JSON."),
    ] = False,
) -> None:
    """Download a generated clip or artifact to a local file."""

    state = state_from_context(ctx)
    resource = state.service.open_artifact_content(artifact_id)
    output = destination or Path(resource.filename)
    if output.is_dir():
        output = output / resource.filename
    output = output.expanduser().resolve()
    if not output.parent.is_dir():
        raise typer.BadParameter(
            "The output directory does not exist.",
            param_hint="destination",
        )
    if output.exists() and not force:
        raise typer.BadParameter(
            "The output file already exists; pass --force to replace it.",
            param_hint="destination",
        )

    temporary = output.with_name(f".{output.name}.{uuid4().hex}.tmp")
    try:
        shutil.copyfile(resource.path, temporary)
        if output.exists() and not force:
            raise typer.BadParameter(
                "The output file already exists; pass --force to replace it.",
                param_hint="destination",
            )
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)

    payload = {
        "artifact_id": artifact_id,
        "path": str(output),
        "mime_type": resource.mime_type,
        "byte_size": resource.byte_size,
        "sha256": resource.etag,
    }
    if effective_output_format(state, json_output) == OutputFormat.json:
        emit_json(payload)
    else:
        typer.secho(
            f"Downloaded {resource.byte_size:,} bytes to {output}",
            fg=typer.colors.GREEN,
        )
