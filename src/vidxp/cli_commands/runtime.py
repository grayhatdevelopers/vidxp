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
        str | None,
        typer.Option(
            "--modalities",
            "-m",
            help="Only validate dependencies for these modalities.",
        ),
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit machine-readable JSON."),
    ] = False,
) -> None:
    """Validate selected indexing dependencies without downloading models."""

    state = state_from_context(ctx)
    selected = (
        state.service.registry.names()
        if modalities is None
        else parse_modalities(modalities, state.service.registry)
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
        str | None,
        typer.Option(
            "--modalities",
            "-m",
            help="Only prepare models for these modalities.",
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
) -> None:
    """Download and cache selected runtime models before indexing."""

    state = state_from_context(ctx)
    selected = (
        state.service.registry.preparable_names()
        if modalities is None
        else parse_modalities(modalities, state.service.registry)
    )
    result = state.service.prepare_models(
        PrepareModelsCommand(
            modalities=selected,
            capability_options=parse_capability_options(capability_options),
        ),
        progress_callback=(
            None
            if state.quiet
            or effective_output_format(state, json_output)
            == OutputFormat.json
            else lambda event: typer.echo(event["message"])
        ),
    )
    if effective_output_format(state, json_output) == OutputFormat.json:
        emit_json(result.model_dump(mode="json"))
    else:
        typer.secho(
            "Selected VidXP runtime models are prepared.",
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
        settings=state.service.settings,
    )
