from __future__ import annotations

from typing import Annotated

import typer

from vidxp.application_models import (
    ApplicationError,
    DependencyCheckCommand,
    ErrorCategory,
    PrepareModelsCommand,
)
from vidxp.cli_support import (
    OutputFormat,
    effective_output_format,
    emit_json,
    parse_capability_options,
    parse_modalities,
    state_from_context,
)

def doctor(
    ctx: typer.Context,
    modalities: Annotated[
        list[str] | None,
        typer.Option(
            "--modalities",
            "-m",
            help=(
                "Only validate dependencies for these modalities. "
                "Accepts comma-separated values or repeated options."
            ),
        ),
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit machine-readable JSON."),
    ] = False,
) -> None:
    """Validate selected indexing dependencies without downloading models."""

    state = state_from_context(ctx)
    available = tuple(
        capability.name for capability in state.service.list_capabilities()
    )
    selected = (
        available
        if modalities is None
        else parse_modalities(",".join(modalities), available)
    )
    result = state.service.check_dependencies(
        DependencyCheckCommand(modalities=selected)
    )
    payload = result.model_dump(mode="json")
    if effective_output_format(state, json_output) == OutputFormat.json:
        emit_json(payload)
    else:
        for check in payload["checks"]:
            owner = check["capability"]
            if check["provenance"] is not None:
                owner = (
                    f"{check['provenance']['distribution']}:"
                    f"{check['provenance']['entry_point']}"
                )
            if check["ok"]:
                typer.secho(
                    f"OK [{owner}] {check['name']}",
                    fg=typer.colors.GREEN,
                )
            else:
                typer.secho(
                    f"FAILED [{owner}] {check['name']}: {check['error']}",
                    fg=typer.colors.RED,
                )
    if not result.ok:
        raise typer.Exit(1)
    if effective_output_format(state, json_output) == OutputFormat.rich:
        typer.secho(
            "Selected VidXP dependencies are available.",
            fg=typer.colors.GREEN,
            bold=True,
        )


def prepare(
    ctx: typer.Context,
    modalities: Annotated[
        list[str] | None,
        typer.Option(
            "--modalities",
            "-m",
            help=(
                "Only prepare models for these modalities. "
                "Accepts comma-separated values or repeated options."
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
    """Download and cache selected runtime models before indexing."""

    state = state_from_context(ctx)
    preparable = tuple(
        capability.name
        for capability in state.service.list_capabilities()
        if capability.prepares_models
    )
    selected = (
        preparable
        if modalities is None
        else parse_modalities(",".join(modalities), preparable)
    )
    job = state.jobs.submit_prepare_models(
        PrepareModelsCommand(
            modalities=selected,
            capability_options=parse_capability_options(capability_options),
        )
    )
    if not detach:
        show_progress = (
            not state.quiet
            and effective_output_format(state, json_output)
            != OutputFormat.json
        )
        job = state.jobs.wait(
            job.job_id,
            progress=lambda current: (
                typer.echo(current.progress.message)
                if show_progress and current.progress is not None
                else None
            ),
        )
    if effective_output_format(state, json_output) == OutputFormat.json:
        emit_json(job.model_dump(mode="json"))
    else:
        typer.secho(
            (
                f"Model preparation job queued: {job.job_id}"
                if detach
                else f"Selected runtime models are prepared: {job.job_id}"
            ),
            fg=typer.colors.GREEN,
            bold=True,
        )


def ui(
    ctx: typer.Context,
    host: Annotated[
        str | None,
        typer.Option("--host", help="Streamlit server address."),
    ] = None,
    port: Annotated[
        int | None,
        typer.Option(
            "--port",
            min=1,
            max=65535,
            help="Streamlit server port.",
        ),
    ] = None,
) -> None:
    """Launch Streamlit with the selected repository configuration."""

    state = state_from_context(ctx)
    try:
        from vidxp import frontend
    except ModuleNotFoundError as exc:
        if exc.name == "streamlit":
            raise ApplicationError(
                "frontend_unavailable",
                ErrorCategory.unavailable,
                "The browser interface is unavailable. "
                'Install the "frontend" extra.',
            ) from exc
        raise

    streamlit_arguments = []
    if host is not None:
        streamlit_arguments.append(f"--server.address={host}")
    if port is not None:
        streamlit_arguments.append(f"--server.port={port}")
    frontend.main(
        streamlit_arguments,
        settings=state.settings,
    )
